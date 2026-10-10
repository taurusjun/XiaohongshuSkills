"""写稿失败队列：失败文章落表 → 1 小时后自动重试 → 再次失败转「待人工」。

- 初次失败：status='pending'，next_retry_at = now + 60min。
- 到期重试：**成功/已写/素材不存在 → status='resolved'（标记成功，不删行）**；再失败 → status='needs_manual'（等人工）。
"""
import datetime
import sqlite3
from contextlib import contextmanager

from services import paths

__all__ = ["ensure", "record", "due", "resolve", "mark_resolved", "mark_manual",
           "list_open", "list_all", "set_due_now", "stats", "categorize",
           "CATEGORY_LABELS", "warn", "RETRY_DELAY_MIN"]

RETRY_DELAY_MIN = 60

# 归一化失败主因（供聚合分析）
CATEGORY_LABELS = {
    "llm_network": "LLM 网络/超时",
    "llm_parse": "LLM 输出解析失败",
    "empty_body": "正文为空",
    "gate_density": "密度不足(<30%)",
    "gate_length": "字数不足",
    "gate_structure": "结构不合规(##/体裁)",
    "gate_title": "标题超长",
    "gate_kana": "残留假名",
    "gate_dunhao": "顿号排比",
    "gate_shintai": "日文新字体",
    "renwei": "renwei 拒稿",
    "gzh_review": "公众号审读不过",
    "score_low": "评分不达标",
    "exception_other": "其他异常",
    "gate_other": "其他门禁",
}


def categorize(reason, stage="", tb=""):
    """把自由文本原因/堆栈归一化为失败主因类别。"""
    r = reason or ""
    if stage == "exception":
        t = (tb or "") + " " + r
        if any(k in t for k in ("URLError", "TimeoutError", "socket.timeout", "Connection",
                                "timed out", "LiteLLMError", "Errno 110", "Temporary failure")):
            return "llm_network"
        if "JSONDecode" in t or "json parse" in t.lower():
            return "llm_parse"
        return "exception_other"
    checks = [
        ("empty_body", ("正文为空",)),
        ("gate_density", ("密度",)),
        ("gate_structure", ("小标题", "三级标题", "不得有 `##`", "`##`")),
        ("gate_length", ("字 <", "字数", "低于")),
        ("gate_title", ("标题 ", "标题×")),
        ("gate_kana", ("假名",)),
        ("gate_dunhao", ("顿号",)),
        ("gate_shintai", ("新字体",)),
        ("renwei", ("renwei",)),
        ("gzh_review", ("公众号", "去魅", "适配度")),
        ("score_low", ("评分", "门槛")),
    ]
    for cat, kws in checks:
        if any(k in r for k in kws):
            return cat
    return "gate_other"


@contextmanager
def _db():
    conn = sqlite3.connect(paths.sqlite_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_DDL = """CREATE TABLE IF NOT EXISTS write_failures (
    key           TEXT PRIMARY KEY,
    reason        TEXT DEFAULT '',
    stage         TEXT DEFAULT '',
    attempts      INTEGER DEFAULT 0,
    channel       TEXT DEFAULT '',
    title         TEXT DEFAULT '',
    retry_count   INTEGER DEFAULT 0,
    status        TEXT DEFAULT 'pending',
    next_retry_at TEXT DEFAULT '',
    created_at    TEXT DEFAULT '',
    updated_at    TEXT DEFAULT '',
    traceback     TEXT DEFAULT '',
    category      TEXT DEFAULT '',
    level         TEXT DEFAULT 'error'
)"""


def ensure():
    with _db() as c:
        c.execute(_DDL)
        for _col in ("traceback", "category", "level"):  # 老表补列（幂等）
            try:
                c.execute(f"ALTER TABLE write_failures ADD COLUMN {_col} TEXT DEFAULT ''")
            except sqlite3.OperationalError:
                pass


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def record(key, reason, stage="", attempts=0, channel="", title="", tb="", category=None, level="error"):
    """记录一次写稿失败；category 缺省由 categorize() 归一化（供主因分析）。"""
    ensure()
    category = category or categorize(reason, stage, tb)
    now = datetime.datetime.now()
    nra = _fmt(now + datetime.timedelta(minutes=RETRY_DELAY_MIN))
    with _db() as c:
        row = c.execute("SELECT key FROM write_failures WHERE key=?", (key,)).fetchone()
        if row:
            c.execute("UPDATE write_failures SET reason=?, category=?, stage=?, attempts=attempts+?, "
                      "channel=?, title=?, traceback=?, level=?, status='pending', next_retry_at=?, updated_at=? "
                      "WHERE key=?",
                      (reason, category, stage, attempts, channel, title, tb or "", level, nra, _fmt(now), key))
        else:
            c.execute("INSERT INTO write_failures (key, reason, category, stage, attempts, channel, "
                      "title, traceback, retry_count, status, next_retry_at, created_at, updated_at, level) "
                      "VALUES (?,?,?,?,?,?,?,?,0,'pending',?,?,?,?)",
                      (key, reason, category, stage, attempts, channel, title, tb or "", nra,
                       _fmt(now), _fmt(now), level))


def due(now=None):
    ensure()
    now = now or datetime.datetime.now()
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM write_failures WHERE status='pending' AND next_retry_at<=? "
            "ORDER BY next_retry_at", (_fmt(now),))]


def warn(key, reason, category="gate_kana"):
    """记一条 warn 级（不重试，仅留痕）：status=resolved, level=warn。"""
    ensure()
    now = _fmt(datetime.datetime.now())
    with _db() as c:
        c.execute("INSERT INTO write_failures (key, reason, category, stage, level, status, "
                  "created_at, updated_at) VALUES (?,?,?,'gate','warn','resolved',?,?) "
                  "ON CONFLICT(key) DO UPDATE SET reason=excluded.reason, category=excluded.category, "
                  "level='warn', status='resolved', updated_at=excluded.updated_at",
                  (key, reason, category, now, now))


def mark_resolved(key, note=""):
    """重试成功/已写/素材不存在 → 标记 resolved（**保留记录，不删除**）。"""
    ensure()
    sets, args = ["status='resolved'", "next_retry_at=''", "updated_at=?"], [_fmt(datetime.datetime.now())]
    if note:
        sets.append("reason=?"); args.append(note)
    args.append(key)
    with _db() as c:
        c.execute(f"UPDATE write_failures SET {', '.join(sets)} WHERE key=?", args)


# 兼容旧调用名
resolve = mark_resolved


def mark_manual(key, reason=None, tb="", category=None):
    ensure()
    sets, args = ["status='needs_manual'", "retry_count=retry_count+1", "next_retry_at=''",
                  "updated_at=?"], [_fmt(datetime.datetime.now())]
    if reason:
        sets.append("reason=?"); args.append(reason)
    if tb:
        sets.append("traceback=?"); args.append(tb)
    if reason:
        sets.append("category=?"); args.append(category or categorize(reason, "gate", tb))
    args.append(key)
    with _db() as c:
        c.execute(f"UPDATE write_failures SET {', '.join(sets)} WHERE key=?", args)


def set_due_now(key):
    """把某条置为「立即到期」，供下次 runner 重试（后台重试按钮用）。"""
    ensure()
    with _db() as c:
        c.execute("UPDATE write_failures SET status='pending', next_retry_at=? WHERE key=?",
                  (_fmt(datetime.datetime.now()), key))


def list_open(status=None):
    """未结（pending / needs_manual）；给 pick_candidates 做去重。"""
    ensure()
    with _db() as c:
        if status:
            rows = c.execute("SELECT * FROM write_failures WHERE status=? ORDER BY updated_at DESC",
                             (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM write_failures WHERE status IN ('pending','needs_manual') "
                             "ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]


def stats(days=30):
    """写稿失败主因聚合（近 N 天）：按类别/状态计数，供分析「主要原因」。"""
    ensure()
    cutoff = _fmt(datetime.datetime.now() - datetime.timedelta(days=days))
    with _db() as c:
        total = c.execute("SELECT COUNT(*) FROM write_failures WHERE created_at>=?", (cutoff,)).fetchone()[0]
        cats = [dict(r) for r in c.execute(
            "SELECT COALESCE(NULLIF(category,''),'gate_other') category, COUNT(*) n "
            "FROM write_failures WHERE created_at>=? GROUP BY 1 ORDER BY n DESC", (cutoff,))]
        sts = [dict(r) for r in c.execute(
            "SELECT status, COUNT(*) n FROM write_failures WHERE created_at>=? GROUP BY status", (cutoff,))]
    for x in cats:
        x["label"] = CATEGORY_LABELS.get(x["category"], x["category"])
    return {"days": days, "total": total, "by_category": cats, "by_status": sts}


def list_all(status=None):
    """全部记录（含 resolved），供后台查看。"""
    ensure()
    with _db() as c:
        if status:
            rows = c.execute("SELECT * FROM write_failures WHERE status=? ORDER BY updated_at DESC",
                             (status,)).fetchall()
        else:
            rows = c.execute("SELECT * FROM write_failures ORDER BY updated_at DESC").fetchall()
    return [dict(r) for r in rows]
