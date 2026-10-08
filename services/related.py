"""关联候选（机械，**全历史无窗口**）：token 命中 + 相关性排序 top-K。

- 不做时间窗口：任何时间的素材都可作为关联候选（对齐原 skill「跨时间关联」）。
- 排序：共享 token 越多、越长、越稀有 → 分越高；同日的候选给 same_day 标记。
"""
import sqlite3

from services import paths, cluster as _cl

__all__ = ["find_related"]


def _tokens(text):
    return [t for t in _cl.tokens(text or "")]


def find_related(key: str, title: str, limit: int = 8, db: str | None = None):
    """返回按相关性排序的关联候选：[{key,title,content_ja,day,score,same_day}, ...]。"""
    toks = _tokens(title)[:10]
    if not toks:
        return []
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        try:
            row = conn.execute("SELECT created_at FROM news WHERE key=?", (key,)).fetchone()
            day = (row[0] or "")[:10] if row and row[0] else ""
        except sqlite3.OperationalError:      # 旧表/测试表无 created_at
            day = ""
        cand = {}
        for t in toks:
            for r in conn.execute(
                    "SELECT key,title,content_ja,created_at FROM news WHERE key!=? AND status='active' "
                    "AND (title LIKE ? OR content_ja LIKE ?) LIMIT 300",
                    (key, f"%{t}%", f"%{t}%")):
                cand.setdefault(r[0], {"key": r[0], "title": r[1] or "",
                                       "content_ja": r[2] or "", "day": (r[3] or "")[:10]})
        if not cand:
            return []
        tset = {k: (_cl.tokens(v["title"]) & set(toks)) for k, v in cand.items()}
        from collections import Counter
        df = Counter(t for ts in tset.values() for t in ts)
        n = len(cand) or 1
        rare = max(2, int(n * 0.05))
        out = []
        for k, v in cand.items():
            shared = tset[k]
            if not shared:
                continue
            v["score"] = round(sum(len(t) * (1.0 if df[t] <= rare else 0.3) for t in shared), 2)
            v["same_day"] = (v["day"] == day and bool(day))
            out.append(v)
        out.sort(key=lambda x: (-x["score"], x["day"]))
        return out[:limit]
    finally:
        conn.close()
