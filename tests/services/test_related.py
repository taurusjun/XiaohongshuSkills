import sqlite3
from services import related


def _mk(db, cols, rows):
    c = sqlite3.connect(db)
    c.execute(f"CREATE TABLE news ({cols})")
    for r in rows:
        c.execute("INSERT INTO news VALUES (%s)" % ",".join("?" * len(r)), r)
    c.commit(); c.close()


def test_find_related_by_entity(tmp_path):
    db = str(tmp_path / "n.db")
    _mk(db, "key TEXT,title TEXT,title_ja TEXT,content_ja TEXT,status TEXT,created_at TEXT", [
        ("k" * 40, "小津玲奈写真", "小津玲奈グラビア", "小津玲奈", "active", "2026-10-08 10:00:00"),
        ("j" * 40, "小津玲奈访谈", "小津玲奈インタビュー", "小津玲奈", "active", "2026-09-01 10:00:00"),
        ("z" * 40, "别人", "別の人", "無関係", "active", "2026-10-08 12:00:00"),
    ])
    res = related.find_related("k" * 40, ["小津玲奈"], db=db)
    assert any(r["key"] == "j" * 40 for r in res), res
    assert all(r["key"] != "k" * 40 for r in res)


def test_find_related_ignores_generic(tmp_path):
    """通用短语不是实体 → 无实体则不检索，避免误连。"""
    db = str(tmp_path / "g.db")
    _mk(db, "key TEXT,title TEXT,title_ja TEXT,content_ja TEXT,status TEXT,created_at TEXT", [
        ("a" * 40, "小林兰剪去长发", "", "", "active", "2026-10-08 10:00:00"),
        ("b" * 40, "清水理央剪去长发", "", "", "active", "2026-10-08 11:00:00"),
    ])
    assert related.find_related("a" * 40, [], db=db) == []


def test_find_related_cross_time(tmp_path):
    """无时间窗口：任意时间的同实体都是候选。"""
    db = str(tmp_path / "w.db")
    _mk(db, "key TEXT,title TEXT,title_ja TEXT,content_ja TEXT,status TEXT,created_at TEXT", [
        ("k" * 40, "相川暖花活动A", "相川暖花", "", "active", "2026-10-08 10:00:00"),
        ("o" + "0" * 39, "相川暖花活动B", "相川暖花", "", "active", "2026-04-08 10:00:00"),
    ])
    res = related.find_related("k" * 40, ["相川暖花"], db=db)
    assert any(r["key"] == "o" + "0" * 39 for r in res), res
