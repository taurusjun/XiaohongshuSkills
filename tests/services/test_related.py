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


def test_find_related_work_only_last(tmp_path):
    """只命中作品/角色名（w）的候选：垫底、可限量（同事件素材要靠它，同作品不同人要靠判定滤）。"""
    db = str(tmp_path / "w2.db")
    _mk(db, "key TEXT,title TEXT,title_ja TEXT,content_ja TEXT,status TEXT,created_at TEXT", [
        ("m" * 40, "井上和演千寻", "井上和", "千と千尋の神隠し", "active", "2026-10-16 10:00:00"),
        ("n" * 40, "川荣李奈", "川荣李奈", "千尋の舞台に出演", "active", "2026-06-22 10:00:00"),
        ("o" + "0" * 39, "井上和走秀", "井上和", "", "active", "2026-10-17 10:00:00"),
    ])
    res = related.find_related("m" * 40, {"p": ["井上和"], "w": ["千寻"]}, db=db)
    assert res[0]["key"] == "o" + "0" * 39          # 同人物 + 日期新 → 第一
    assert res[-1]["key"] == "n" * 40               # w-only → 垫底
    res0 = related.find_related("m" * 40, {"p": ["井上和"], "w": ["千寻"]}, db=db, max_work_only=0)
    assert "n" * 40 not in [r["key"] for r in res0]  # 限量 0 → 全丢


def test_find_related_group_ranks_below_person(tmp_path):
    """团体命中（g）排在人物命中（p）之后，避免「组合通稿」把同人物历史顶掉。"""
    db = str(tmp_path / "w4.db")
    _mk(db, "key TEXT,title TEXT,title_ja TEXT,content_ja TEXT,status TEXT,created_at TEXT", [
        ("a" * 40, "本篇", "", "", "active", "2026-10-16 10:00:00"),
        ("b" * 40, "组合通稿", "乃木坂46", "乃木坂46の記事", "active", "2026-10-17 10:00:00"),
        ("c" * 40, "同人物旧稿", "井上和", "井上和の記事", "active", "2026-09-01 10:00:00"),
    ])
    res = related.find_related("a" * 40, {"p": ["井上和"], "g": ["乃木坂46"], "w": []}, db=db)
    assert res[0]["key"] == "c" * 40
