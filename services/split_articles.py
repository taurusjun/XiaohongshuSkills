"""拆篇文章（split_articles）：一份主稿（cluster 主 key）按体量拆多篇，**1 主稿对多篇**。

与 `news.rewritten_*` 解耦：拆篇结果只写本表，**不覆盖**主稿的常规改写字段。
"""
import datetime
import sqlite3
from contextlib import contextmanager

from services import paths

__all__ = ["ensure", "replace_for_main", "list_by_main", "count_for_main",
           "counts_for", "list_all"]


@contextmanager
def _db():
    conn = sqlite3.connect(paths.sqlite_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_DDL = """CREATE TABLE IF NOT EXISTS split_articles (
    main_key    TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    group_keys  TEXT DEFAULT '',
    title       TEXT DEFAULT '',
    content     TEXT DEFAULT '',
    channel     TEXT DEFAULT '',
    method      TEXT DEFAULT '',
    score       INTEGER DEFAULT 0,
    dims        TEXT DEFAULT '',
    status      TEXT DEFAULT 'draft',
    created_at  TEXT DEFAULT '',
    updated_at  TEXT DEFAULT '',
    PRIMARY KEY (main_key, seq)
)"""


def ensure():
    with _db() as c:
        c.execute(_DDL)


def _fmt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def replace_for_main(main_key, items):
    """覆盖某主稿的全部拆篇（重新拆时用），返回写入篇数。

    items: [{seq, group_keys, title, content, channel, method, score, dims}]
    """
    ensure()
    now = _fmt(datetime.datetime.now())
    with _db() as c:
        c.execute("DELETE FROM split_articles WHERE main_key=?", (main_key,))
        for it in items:
            c.execute(
                "INSERT INTO split_articles (main_key, seq, group_keys, title, content, channel, "
                "method, score, dims, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (main_key, int(it.get("seq") or 0), it.get("group_keys", ""), it.get("title", ""),
                 it.get("content", ""), it.get("channel", ""), it.get("method", ""),
                 int(it.get("score") or 0), it.get("dims", ""), it.get("status", "draft"), now, now))
    return len(items)


def list_by_main(main_key):
    ensure()
    with _db() as c:
        rows = c.execute("SELECT * FROM split_articles WHERE main_key=? ORDER BY seq", (main_key,)).fetchall()
    return [dict(r) for r in rows]


def count_for_main(main_key):
    ensure()
    with _db() as c:
        return c.execute("SELECT COUNT(*) FROM split_articles WHERE main_key=?", (main_key,)).fetchone()[0]


def counts_for(keys):
    """批量取「已拆篇数」：{main_key: n}（一次查询）。"""
    keys = [k for k in (keys or []) if k]
    if not keys:
        return {}
    ensure()
    ph = ",".join("?" for _ in keys)
    with _db() as c:
        rows = c.execute(f"SELECT main_key, COUNT(*) n FROM split_articles "
                         f"WHERE main_key IN ({ph}) GROUP BY main_key", keys).fetchall()
    return {r["main_key"]: r["n"] for r in rows}


def list_all(limit=200):
    ensure()
    with _db() as c:
        rows = c.execute("SELECT * FROM split_articles ORDER BY updated_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]
