"""关联候选（机械，全历史无窗口）：**按实体名检索**（人物/团体/作品/系列名）。

不做 n-gram 重叠：检索键是实体，避免「剪去长发/移籍新事务所」类通用短语误连。
"""
import sqlite3

from services import paths

__all__ = ["find_related"]


def find_related(key: str, entities, limit: int = 8, db: str | None = None):
    """entities: [实体名,...]（日文原始写法优先）。返回 [{key,title,day,score,n}]。"""
    ents = []
    for e in (entities or []):
        e = str(e).strip()
        if len(e) >= 2 and e not in ents:
            ents.append(e)
    if not ents:
        return []
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(news)")}
        search = [c for c in ("title", "title_ja", "content_ja", "rewritten_title") if c in cols] or ["title"]
        sel = "key,title" + (",title_ja" if "title_ja" in cols else ", ''")
        sel += ",created_at" if "created_at" in cols else ", ''"
        where = " OR ".join(f"{c} LIKE ?" for c in search)
        cand = {}
        for e in ents:
            like = f"%{e}%"
            for r in conn.execute(
                    f"SELECT {sel} FROM news WHERE key!=? AND status='active' AND ({where}) LIMIT 200",
                    (key, *([like] * len(search)))):
                d = cand.setdefault(r[0], {"key": r[0], "title": r[1] or "", "day": (r[3] or "")[:10],
                                           "n": 0, "mx": 0})
                d["n"] += 1
                d["mx"] = max(d["mx"], len(e))
        out = sorted(cand.values(), key=lambda x: (-x["n"], -x["mx"], x["day"]))
        for v in out:
            v["score"] = v["n"]
        return out[:limit]
    finally:
        conn.close()
