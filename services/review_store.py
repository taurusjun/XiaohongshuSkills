"""review 结果的**结构化存储**（簇身份 + 运行历史）。

背景（2026-10-18）：review 的产出原先全部压平在 `news` 表的列上——
  · 「簇」是一对多的独立实体，被压平后 `main`/`note` 在每个成员行上重复存 N 份，簇本身没有身份；
  · 每次 review 覆盖同名列 → **没有运行历史**（看不到上一版判了什么、为什么改）。
本模块补两张表（不动现有列、不影响 write 现有逻辑）：
  review_clusters  一簇一行（date / cluster_id / main_key / note / grade / keys_json / n）
  review_runs      每次 review 一行（date / ran_at / 各字段计数 / ok）
成员行只额外存 `cluster_id` 指回簇。
"""
import json
import sqlite3
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

_DDL = [
    """CREATE TABLE IF NOT EXISTS review_clusters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, cluster_id TEXT, main_key TEXT, note TEXT, grade TEXT,
        keys_json TEXT, n INTEGER DEFAULT 0,
        created_at TEXT, updated_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS review_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT, ran_at TEXT, model TEXT,
        n_rows INTEGER DEFAULT 0, n_grade INTEGER DEFAULT 0, n_cluster INTEGER DEFAULT 0,
        n_advice INTEGER DEFAULT 0, n_cross INTEGER DEFAULT 0, n_date_trap INTEGER DEFAULT 0,
        n_unresolved INTEGER DEFAULT 0,
        ok INTEGER DEFAULT 1, note TEXT)""",
]


def _now():
    return datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S")


def ensure(conn):
    for ddl in _DDL:
        conn.execute(ddl)
    # 兼容旧表：CREATE IF NOT EXISTS 不会给已存在的表补列
    for tbl, cols in (("review_clusters", [("mains_json", "TEXT DEFAULT ''")]),
                      ("review_runs", [("n_unresolved", "INTEGER DEFAULT 0")])):
        try:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})")}
        except sqlite3.OperationalError:
            continue
        for c, t in cols:
            if c not in have:
                try:
                    conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {c} {t}")
                except sqlite3.OperationalError:
                    pass
    cols = {r[1] for r in conn.execute("PRAGMA table_info(news)")}
    if "cluster_id" not in cols:
        try:
            conn.execute("ALTER TABLE news ADD COLUMN cluster_id TEXT DEFAULT ''")
        except sqlite3.OperationalError:
            pass


def save_clusters(conn, date, clusters_raw, resolve_main=None):
    """clusters_raw: review 的 clusters 原样（[{keys, main, note, grade}, ...]）。
    返回 (簇数, {k12: cluster_id})。resolve_main: 把 main 的 k12 展开成 40 位键的函数。"""
    ensure(conn)
    conn.execute("DELETE FROM review_clusters WHERE date=?", (date,))   # 同日重跑 → 覆盖（不累积重复）
    now = _now()
    out = {}
    n = 0
    for c in (clusters_raw or []):
        keys = [str(k).strip() for k in (c.get("keys") or []) if str(k).strip()]
        if len(keys) < 2:
            continue
        _mains = c.get("mains") or ([c.get("main")] if c.get("main") else [])
        _mains = [str(x).strip() for x in _mains if str(x).strip()] or [keys[0]]
        # 落库一律全键（k12 只在 LLM 接口层）
        _full = []
        for _x in _mains:
            _f = resolve_main([_x]) if resolve_main else []
            _full.append(_f[0] if _f else _x)
        _mains = _full
        main_full = _mains[0]
        cid = f"{date}-{main_full[:12]}"
        conn.execute("INSERT INTO review_clusters(date,cluster_id,main_key,mains_json,note,grade,keys_json,n,created_at,updated_at)"
                     " VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (date, cid, main_full, json.dumps(_mains, ensure_ascii=False),
                      str(c.get("note") or "")[:300], str(c.get("grade") or ""),
                      json.dumps(keys, ensure_ascii=False), len(keys), now, now))
        n += 1
        for k in keys:
            out[k] = cid
    conn.commit()
    return n, out


def save_run(conn, date, counts=None, model="", ok=1, note=""):
    """运行历史 = **append-only**（每次运行留一行，用于「上次 review 判了什么」）。"""
    ensure(conn)
    c = counts or {}
    conn.execute("INSERT INTO review_runs(date,ran_at,model,n_rows,n_grade,n_cluster,n_advice,n_cross,n_date_trap,n_unresolved,ok,note)"
                 " VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                 (date, _now(), model, c.get("rows", 0), c.get("grade", 0), c.get("cluster", 0),
                  c.get("advice", 0), c.get("cross", 0), c.get("date_trap", 0), c.get("unresolved", 0), ok, note[:200]))
    conn.commit()


def clusters_of(conn, date):
    """某天的簇（含主条与说明）——回答「这一簇主条是谁」。"""
    ensure(conn)
    rows = conn.execute("SELECT cluster_id,main_key,mains_json,note,grade,n,keys_json FROM review_clusters WHERE date=?", (date,)).fetchall()
    return [{"cluster_id": r[0], "main_key": r[1], "mains": json.loads(r[2] or "[]"), "note": r[3],
             "grade": r[4], "n": r[5], "keys": json.loads(r[6] or "[]")} for r in rows]


def runs_recent(conn, n=10):
    """最近几次 review 运行——回答「上次 review 判了什么」。"""
    ensure(conn)
    rows = conn.execute("SELECT date,ran_at,model,n_rows,n_grade,n_cluster,n_advice,n_cross,n_date_trap,n_unresolved,ok,note"
                        " FROM review_runs ORDER BY id DESC LIMIT ?", (n,)).fetchall()
    cols = ["date", "ran_at", "model", "n_rows", "n_grade", "n_cluster", "n_advice", "n_cross", "n_date_trap", "n_unresolved", "ok", "note"]
    return [dict(zip(cols, r)) for r in rows]
