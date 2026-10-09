"""第4层：发布数据聚合 + data-feedback-patterns.md（人类报告）+ feedback_patterns 表（决策检索）。

**双写**：md（人类可读累积报告）＋表 `feedback_patterns`（结构化规律，供决策/检索）。
**规律结构化字段（facets）**：category/genre/title_style/publish_mode/direction/action/confidence/entities；
`body` 保留 md 原文给人看。检索 `relevant()` 按 entities/genre/category/title_style 命中并回带 direction/action。
"""
import datetime as dt
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

from services import paths

__all__ = ["aggregate", "context", "update", "relevant", "insert_patterns",
           "all_patterns", "update_facets", "get_pattern", "update_row"]

FEEDBACK_SEED_REL = "skills/creative/xhs-daily-material-review/references/data-feedback-patterns.md"
FEEDBACK_RUNTIME_REL = "data/feedback/data-feedback-patterns.md"
AGG_REL = "skills/xhs-daily-material-review-layer4/scripts/layer4_aggregate.py"
TREND_MARK = "## 跨会话趋势表"
FACETS = ("category", "genre", "title_style", "publish_mode", "direction", "action",
          "confidence", "entities")


def _now_jst():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Asia/Tokyo"))
    except Exception:  # noqa: BLE001
        return dt.datetime.utcnow() + dt.timedelta(hours=9)


def _seed_path():
    return paths.REPO_ROOT / FEEDBACK_SEED_REL


def _runtime_path():
    env = os.environ.get("XHS_FEEDBACK_MD")
    if env:
        p = Path(env)
        return p if p.is_absolute() else paths.REPO_ROOT / p
    return paths.REPO_ROOT / FEEDBACK_RUNTIME_REL


def _fb_path():
    p = _runtime_path()
    if not p.exists():
        seed = _seed_path()
        if seed.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(seed, p)
    return p


# ---------------- 结构化表（决策检索源） ----------------

def _connect():
    return sqlite3.connect(paths.sqlite_path())


def _ensure_table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS feedback_patterns (
        no INTEGER PRIMARY KEY, date TEXT, title TEXT, body TEXT, tags TEXT, created_at TEXT)""")
    cols = {r[1] for r in conn.execute("PRAGMA table_info(feedback_patterns)")}
    for c in ("tags", "created_at", *FACETS):
        if c not in cols:
            try:
                conn.execute(f"ALTER TABLE feedback_patterns ADD COLUMN {c} TEXT")
            except Exception:  # noqa: BLE001
                pass


def _parse_sections(txt):
    out, cur, buf = [], None, []
    for l in txt.split("\n"):
        if l.strip().startswith(TREND_MARK):
            break
        m = re.match(r"^###\s+(\d+)\.\s*(.*)$", l)
        if m:
            if cur is not None:
                out.append({"no": cur[0], "title": cur[1], "body": "\n".join(buf).strip()})
            cur = (int(m.group(1)), m.group(2).strip())
            buf = []
        elif cur is not None:
            buf.append(l)
    if cur is not None:
        out.append({"no": cur[0], "title": cur[1], "body": "\n".join(buf).strip()})
    return out


def _ensure_seed():
    with _connect() as conn:
        _ensure_table(conn)
        if conn.execute("SELECT COUNT(*) FROM feedback_patterns").fetchone()[0]:
            return
        p = _fb_path()
        if not p.exists():
            return
        for s in _parse_sections(p.read_text(encoding="utf-8")):
            conn.execute("INSERT OR REPLACE INTO feedback_patterns"
                         "(no,date,title,body,tags,created_at) VALUES(?,?,?,?,?,datetime('now','localtime'))",
                         (s["no"], None, s["title"], s["body"], ""))


def insert_patterns(patterns, date=None):
    """patterns:[{title,body,tags,category,genre,title_style,publish_mode,direction,action,confidence,entities}]
    → 分配编号(接续 MAX)、写库，返回入库行。"""
    rows = []
    if not patterns:
        return rows
    _ensure_seed()
    with _connect() as conn:
        _ensure_table(conn)
        mx = conn.execute("SELECT COALESCE(MAX(no),0) FROM feedback_patterns").fetchone()[0]
        for i, p in enumerate(patterns):
            title = str(p.get("title") or "").strip()
            if not title:
                continue
            no = mx + i + 1
            vals = [no, date, title, str(p.get("body") or "").strip(), str(p.get("tags") or "").strip()]
            cols = ["no", "date", "title", "body", "tags"]
            for k in FACETS:
                cols.append(k)
                vals.append(str(p.get(k) or "").strip())
            ph = ",".join(["?"] * len(vals))
            conn.execute(f"INSERT OR REPLACE INTO feedback_patterns({','.join(cols)},created_at) "
                         f"VALUES({ph},datetime('now','localtime'))", vals)
            rows.append({"no": no, "title": title, **{k: str(p.get(k) or "").strip() for k in FACETS}})
    return rows


def update_facets(no, facets):
    cols = [k for k in FACETS if k in (facets or {})]
    if not cols:
        return False
    with _connect() as conn:
        _ensure_table(conn)
        conn.execute(f"UPDATE feedback_patterns SET {','.join(c + '=?' for c in cols)} WHERE no=?",
                     [str(facets[c] or "").strip() for c in cols] + [no])
    return True


def get_pattern(no):
    with _connect() as conn:
        _ensure_table(conn)
        conn.row_factory = sqlite3.Row
        r = conn.execute("SELECT * FROM feedback_patterns WHERE no=?", (no,)).fetchone()
        return dict(r) if r else None


_EDITABLE = ("title", "body", "tags", "category", "genre", "title_style",
             "publish_mode", "direction", "action", "confidence", "entities")


def update_row(no, fields):
    """人工 review 编辑：更新 title/body/tags + facets。"""
    cols = [k for k in _EDITABLE if k in (fields or {})]
    if not cols:
        return False
    with _connect() as conn:
        _ensure_table(conn)
        conn.execute(f"UPDATE feedback_patterns SET {','.join(c + '=?' for c in cols)} WHERE no=?",
                     [str(fields[c] or "").strip() for c in cols] + [no])
    return True


def all_patterns():
    _ensure_seed()
    with _connect() as conn:
        _ensure_table(conn)
        conn.row_factory = sqlite3.Row
        return [dict(r) for r in conn.execute("SELECT * FROM feedback_patterns ORDER BY no")]


def relevant(query, k=3, chars=900):
    """字段化检索：entities(×3) > category/genre/title_style(×2) > title/tags/body(×1)；
    命中的规律回带 direction/action 供决策。无命中回退最近 k 条。"""
    _ensure_seed()
    terms = [t for t in re.split(r"[\s，。、/｜|·]+", query or "") if len(t) >= 2][:8]
    rows = all_patterns()
    def sc(r):
        ent = r.get("entities") or ""
        gf = f"{r.get('category') or ''} {r.get('genre') or ''} {r.get('title_style') or ''}"
        tt = f"{r.get('title') or ''} {r.get('tags') or ''}"
        b = r.get("body") or ""
        s = 0
        for t in terms:
            s += 3 * ent.count(t) + 2 * gf.count(t) + (tt.count(t) + b.count(t))
        return s
    hits = [r for r in rows if terms and sc(r) > 0]
    hits.sort(key=lambda r: (-sc(r), -(r.get("no") or 0)))
    picked = hits[:k] if hits else sorted(rows, key=lambda r: -(r.get("no") or 0))[:k]
    out = []
    for r in picked:
        meta = " ｜ ".join(x for x in (
            f"方向:{r.get('direction')}" if r.get("direction") else "",
            f"对策:{r.get('action')}" if r.get("action") else "",
            f"题材:{r.get('genre')}" if r.get("genre") else "",
            f"标题:{r.get('title_style')}" if r.get("title_style") else "") if x)
        head = f"[#{r.get('no')}] {r.get('title')}" + (f"（{meta}）" if meta else "")
        out.append(head + "\n" + (r.get("body") or "")[:chars])
    return "\n\n".join(out)


# ---------------- 第4层聚合 + md 追加 ----------------

def aggregate(date=None):
    script = paths.REPO_ROOT / AGG_REL
    if not script.exists():
        return f"[aggregate 跳过] 找不到 {AGG_REL}"
    now = _now_jst()
    args = [sys.executable, str(script), now.strftime("%Y-%m-%d"), now.strftime("%H:%M")]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=240, cwd=str(paths.REPO_ROOT))
    except Exception as e:  # noqa: BLE001
        return f"[aggregate 失败] {e}"
    out = r.stdout or ""
    if r.returncode != 0:
        out += f"\n[aggregate rc={r.returncode}]\n{(r.stderr or '')[-1500:]}"
    return out


def context():
    p = _fb_path()
    if not p.exists():
        return {"last_pattern_no": 0, "tail": ""}
    txt = p.read_text(encoding="utf-8")
    nums = [int(m) for m in re.findall(r"^###\s+(\d+)\.", txt, re.M)]
    last = max(nums) if nums else 0
    i = txt.find(TREND_MARK)
    tail = txt[i:i + 3000] if i >= 0 else txt[-3000:]
    return {"last_pattern_no": last, "tail": tail}


def update(pattern_section=None, trend_row=None, date=None, patterns=None):
    """仅追加：模式节插趋势表前；趋势行追加表末。同日幂等。同时写 feedback_patterns 表。"""
    pat = (pattern_section or "").strip()
    row = (trend_row or "").strip()
    p = _fb_path()
    if not p.exists():
        return False, f"找不到运行时反馈文件（种子: {FEEDBACK_SEED_REL}）"
    txt = p.read_text(encoding="utf-8")
    orig = txt
    same_day = bool(date) and any(
        re.match(rf"^\|\s*{re.escape(str(date))}\s*\|", l) for l in txt.split("\n"))
    if not same_day:
        if patterns:
            rows = insert_patterns(patterns, date)
            pat = "\n\n".join(f"### {r['no']}. {r['title']}\n\n{(r.get('body') or '').strip()}".rstrip()
                              for r in rows)
        elif pat:
            try:
                _ensure_seed()
                with _connect() as conn:
                    for s in _parse_sections(pat):
                        conn.execute("INSERT OR REPLACE INTO feedback_patterns"
                                     "(no,date,title,body,tags,created_at) VALUES(?,?,?,?,?,datetime('now','localtime'))",
                                     (s["no"], date, s["title"], s["body"], ""))
            except Exception:  # noqa: BLE001
                pass
    else:
        pat = ""
    if pat:
        i = txt.find(TREND_MARK)
        if i < 0:
            return False, "找不到趋势表锚点"
        txt = txt[:i] + pat.rstrip() + "\n\n---\n\n" + txt[i:]
    tail = "无"
    if row:
        lines = txt.split("\n")
        mi = next((k for k, l in enumerate(lines) if l.strip() == TREND_MARK), None)
        if mi is None:
            return False, "找不到趋势表锚点"
        cand = [k for k in range(mi + 1, len(lines)) if lines[k].strip().startswith("|")]
        if not cand:
            return False, "趋势表无数据行"
        idx = None
        if date:
            for k in cand:
                if re.match(rf"^\|\s*{re.escape(str(date))}\s*\|", lines[k]):
                    idx = k
                    break
        if idx is not None:
            lines[idx] = row
            tail = "替换"
        else:
            lines.insert(cand[-1] + 1, row)
            tail = "追加"
        txt = "\n".join(lines)
    if txt == orig:
        return False, "无变化"
    p.write_text(txt, encoding="utf-8")
    return True, f"已更新（模式节={'有' if pat else '无/跳过'}，趋势行={tail}）"
