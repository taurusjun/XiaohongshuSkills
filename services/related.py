"""关联候选（机械，全历史无窗口）：token 命中 + **通用短语过滤** + 相关性排序 top-K。

- 不做时间窗口：任何时间的素材都可作为候选（对齐原 skill「跨时间关联」）。
- 只把"共享非通用 token（人名/系列等）"的素材当候选 → 抑制「移籍新事务所/剪去长发」类误连。
"""
import re
import sqlite3

from services import paths, cluster as _cl

__all__ = ["find_related", "link_ok"]

_LAT = re.compile(r"[A-Za-z][A-Za-z0-9']+")
_STOP = None


def _stopwords():
    global _STOP
    if _STOP is None:
        f = paths.REPO_ROOT / "config" / "related_stopwords.txt"
        words = []
        if f.exists():
            words = [w.strip() for w in f.read_text(encoding="utf-8").splitlines()
                     if w.strip() and not w.startswith("#")]
        _STOP = tuple(words)
    return _STOP


def _is_generic(tok):
    for w in _stopwords():
        if w in tok or tok in w:
            return True
    return False


def _tokset(text):
    return {t for t in _cl.tokens(text or "") if not _is_generic(t)}


def _latin(text):
    return {w.lower() for w in _LAT.findall(text or "") if len(w) >= 2}


def _sim(a, b):
    """两文本相似度：非通用 CJK n-gram(按长度加权) + Latin 词(各 3 分)。"""
    s = sum(len(t) for t in (_tokset(a) & _tokset(b)))
    s += 3 * len(_latin(a) & _latin(b))
    return s


def link_ok(a, b):
    """两条标题是否有"非通用"共享证据（人名/系列/Latin 词）；否则判为仅通用短语相连。"""
    return _sim(a, b) > 0


def find_related(key: str, title: str, limit: int = 8, db: str | None = None):
    """返回按相关性排序的关联候选：[{key,title,content_ja,day,score,same_day}, ...]。"""
    toks = [t for t in _cl.tokens(title or "")][:10]
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
        out = []
        for v in cand.values():
            sc = _sim(title, v["title"]) + 0.4 * _sim(title, v["content_ja"])
            if sc <= 0:
                continue                          # 只共享通用短语 → 不作为候选
            v["score"] = round(sc, 2)
            v["same_day"] = (v["day"] == day and bool(day))
            out.append(v)
        out.sort(key=lambda x: (-x["score"], x["day"]))
        return out[:limit]
    finally:
        conn.close()
