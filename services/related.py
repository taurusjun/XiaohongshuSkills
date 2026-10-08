"""同事件关联素材（机械）：LIKE 取池 + 日期窗口 → cluster 精聚（review 用；write 只读 DB）。"""
import datetime as dt
import sqlite3

from services import paths, cluster as _cl

__all__ = ["find_related"]


def _day(s):
    s = str(s or "").strip().replace(".", "-")[:10]
    try:
        return dt.date.fromisoformat(s)
    except Exception:
        return None


def find_related(key: str, title: str, limit: int = 5, db: str | None = None,
                 max_gap_days: int | None = 3):
    toks = [t for t in _cl.tokens(title) if len(t) >= 2][:8]
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        pool = {key: {"key": key, "title": title, "content_ja": ""}}
        try:
            row = conn.execute("SELECT created_at FROM news WHERE key=?", (key,)).fetchone()
            day = _day(row[0]) if row else None
        except sqlite3.OperationalError:      # 旧表/测试表无 created_at → 不加窗口
            day = None
        win, wparams = "", []
        if day is not None and max_gap_days is not None:
            lo = (day - dt.timedelta(days=max_gap_days)).isoformat()
            hi = (day + dt.timedelta(days=max_gap_days)).isoformat()
            win = " AND substr(created_at,1,10)>=? AND substr(created_at,1,10)<=?"
            wparams = [lo, hi]
        for t in toks:
            for r in conn.execute(
                    "SELECT key,title,content_ja FROM news WHERE key!=? AND status='active' "
                    "AND (title LIKE ? OR content_ja LIKE ?)" + win + " LIMIT 300",
                    (key, f"%{t}%", f"%{t}%", *wparams)):
                pool.setdefault(r[0], {"key": r[0], "title": r[1], "content_ja": r[2] or ""})
        if len(pool) <= 1:
            return []
        grp = _cl.group_of(pool[key], list(pool.values()))
        sibs = [x for x in grp if x["key"] != key and x.get("title")]
        return sibs[:limit]
    finally:
        conn.close()
