"""LLM token 用量：按篇/按次落 DB（token_usage 表）+ 追加 logs/token.log。

- scope=article：一篇稿子（write_one 期间）的用量，key=文章 key。
- scope=run：一次跑（写稿/review 整批）的合计。
"""
import datetime
import sqlite3
from contextlib import contextmanager

from services import paths

__all__ = ["ensure", "record", "diff", "summary", "by_day", "detail", "detail_count", "list_recent"]

_DDL = """CREATE TABLE IF NOT EXISTS token_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts TEXT DEFAULT '',
  run TEXT DEFAULT '',          -- write|review|split|...
  scope TEXT DEFAULT '',        -- article|run
  key TEXT DEFAULT '',
  model TEXT DEFAULT '',
  calls INTEGER DEFAULT 0,
  prompt_tokens INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  total_tokens INTEGER DEFAULT 0,
  cache_hit_tokens INTEGER DEFAULT 0
)"""


@contextmanager
def _db():
    conn = sqlite3.connect(paths.sqlite_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def ensure():
    with _db() as c:
        c.execute(_DDL)
        c.execute("CREATE INDEX IF NOT EXISTS idx_token_ts ON token_usage(ts)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_token_key ON token_usage(key)")


def diff(before, after):
    """两次 usage() 的差（before/after 为 agent.llm.usage() 快照）。"""
    keys = ("calls", "prompt_tokens", "completion_tokens", "total_tokens",
            "cache_hit_tokens", "cache_miss_tokens")
    return {k: int((after or {}).get(k, 0)) - int((before or {}).get(k, 0)) for k in keys}


def record(run, scope, key, usage, model=""):
    ensure()
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    u = usage or {}
    with _db() as c:
        c.execute("INSERT INTO token_usage (ts,run,scope,key,model,calls,prompt_tokens,"
                  "completion_tokens,total_tokens,cache_hit_tokens) VALUES (?,?,?,?,?,?,?,?,?,?)",
                  (now, run, scope, key or "", model, u.get("calls", 0), u.get("prompt_tokens", 0),
                   u.get("completion_tokens", 0), u.get("total_tokens", 0), u.get("cache_hit_tokens", 0)))
    try:
        log = paths.REPO_ROOT / "logs" / "token.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"{now}\t{run}\t{scope}\t{(key or '')[:12]}\t{u.get('calls',0)}\t"
                    f"{u.get('prompt_tokens',0)}\t{u.get('completion_tokens',0)}\t"
                    f"{u.get('total_tokens',0)}\t{u.get('cache_hit_tokens',0)}\n")
    except Exception:  # noqa: BLE001
        pass


def summary(days=30):
    ensure()
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    with _db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT run, COUNT(*) n, SUM(calls) calls, SUM(prompt_tokens) prompt_tokens, "
            "SUM(completion_tokens) completion_tokens, SUM(total_tokens) total_tokens, "
            "SUM(cache_hit_tokens) cache_hit_tokens FROM token_usage WHERE ts>=? GROUP BY run",
            (cutoff,)).fetchall()]
    return {"days": days, "by_run": rows}


def list_recent(limit=200, run=None):
    ensure()
    sql, args = "SELECT * FROM token_usage WHERE 1=1", []
    if run:
        sql += " AND run=?"; args.append(run)
    sql += " ORDER BY id DESC LIMIT ?"; args.append(limit)
    with _db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def by_day(days=30, run=None):
    """按天汇总（近 days 天）。"""
    ensure()
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    sql = ("SELECT substr(ts,1,10) d, COUNT(*) n, SUM(calls) calls, SUM(prompt_tokens) prompt_tokens, "
           "SUM(completion_tokens) completion_tokens, SUM(total_tokens) total_tokens, "
           "SUM(cache_hit_tokens) cache_hit_tokens FROM token_usage WHERE ts>=? ")
    args = [cutoff]
    if run:
        sql += "AND run=? "; args.append(run)
    sql += "GROUP BY d ORDER BY d DESC"
    with _db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def _detail_where(days, run):
    cutoff = (datetime.datetime.now() - datetime.timedelta(days=days)).strftime("%Y-%m-%d")
    sql, args = "FROM token_usage WHERE ts>=?", [cutoff]
    if run:
        sql += " AND run=?"; args.append(run)
    return sql, args


def detail_count(days=3, run=None):
    ensure()
    w, a = _detail_where(days, run)
    with _db() as c:
        return c.execute("SELECT COUNT(*) " + w, a).fetchone()[0]


def detail(days=3, run=None, page=1, page_size=100):
    """明细（近 days 天，按 run 过滤，分页）。"""
    ensure()
    w, a = _detail_where(days, run)
    off = max(0, (int(page) - 1) * int(page_size))
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * " + w + " ORDER BY id DESC LIMIT ? OFFSET ?", a + [int(page_size), off]).fetchall()]
