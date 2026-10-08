"""同事件关联素材（机械）：LIKE 取池 → cluster 精聚 → 返回候选所在组的其它素材。"""
import sqlite3

from services import paths, cluster as _cl

__all__ = ["find_related"]


def find_related(key: str, title: str, limit: int = 5, db: str | None = None):
    toks = [t for t in _cl.tokens(title) if len(t) >= 2][:8]
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        pool = {key: {"key": key, "title": title, "content_ja": ""}}
        for t in toks:
            for r in conn.execute(
                    "SELECT key,title,content_ja FROM news WHERE key!=? AND status='active' "
                    "AND (title LIKE ? OR content_ja LIKE ?) LIMIT 300",
                    (key, f"%{t}%", f"%{t}%")):
                pool.setdefault(r[0], {"key": r[0], "title": r[1], "content_ja": r[2] or ""})
        if len(pool) <= 1:
            return []
        grp = _cl.group_of(pool[key], list(pool.values()))
        sibs = [x for x in grp if x["key"] != key and x.get("title")]
        return sibs[:limit]
    finally:
        conn.close()
