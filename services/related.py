"""关联候选（机械，全历史无窗口）：**按实体名检索**（人物/团体/作品/系列名）。

实体由 LLM 抽取，**中日双形**（日语汉字 + 简体中文）；本模块再补 zhconv 简繁/地区字形变体。
全库历史以简体中文为主、content_ja 为日文，故用只读 DB 三字段 OR（title/content_ja/rewritten_title），
比 API search= 更全（API search= 不索引 content_ja 日文正文）。
"""
import sqlite3

from services import paths

__all__ = ["find_related", "variants"]


def variants(e):
    """把一个实体扩展为检索变体：原样 + zhconv 简繁/地区字形。"""
    out = []
    e = str(e).strip()
    if e:
        out.append(e)
    try:
        import zhconv
        for tgt in ("zh-cn", "zh-hant", "zh-hk", "zh-tw"):
            try:
                v = zhconv.convert(e, tgt)
            except Exception:  # noqa: BLE001
                v = ""
            if v and v not in out:
                out.append(v)
    except Exception:  # noqa: BLE001
        pass
    return out


def find_related(key, entities, limit=8, db=None):
    """entities: [实体名,...]（中日双形）。返回 [{key,title,day,score,n}]。"""
    terms = []
    for e in (entities or []):
        for v in variants(e):
            if len(v) >= 2 and v not in terms:
                terms.append(v)
    if not terms:
        return []
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(news)")}
        search = [c for c in ("title", "title_ja", "content_ja", "rewritten_title") if c in cols] or ["title"]
        sel = "key,title" + (",title_ja" if "title_ja" in cols else ", ''")
        sel += ",created_at" if "created_at" in cols else ", ''"
        where = " OR ".join(f"{c} LIKE ?" for c in search)
        cand = {}
        for e in terms:
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
