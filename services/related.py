"""同事件关联素材查找（机械）：按标题/原文的 CJK token 命中度排序。"""
import re
import sqlite3

from services import paths

__all__ = ["find_related"]
_TOK = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff]{2,6}")


def find_related(key: str, title: str, limit: int = 4, scan: int = 400, db: str | None = None):
    toks = [t for t in dict.fromkeys(_TOK.findall(title or "")) if len(t) >= 2][:8]
    if not toks:
        return []
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        score: dict = {}
        for t in toks:
            rows = conn.execute(
                "SELECT key FROM news WHERE key!=? AND status='active' "
                "AND (title LIKE ? OR content_ja LIKE ?) LIMIT ?",
                (key, f"%{t}%", f"%{t}%", scan)).fetchall()
            for r in rows:
                score[r[0]] = score.get(r[0], 0) + 1
        best = [k for k, _ in sorted(score.items(), key=lambda kv: -kv[1])[:limit]]
        out = []
        for k in best:
            r = conn.execute("SELECT key,title,content_ja FROM news WHERE key=?", (k,)).fetchone()
            if r:
                out.append({"key": r[0], "title": r[1], "content_ja": r[2] or ""})
        return out
    finally:
        conn.close()
