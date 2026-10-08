import sqlite3
from services import related


def _mk(db, rows):
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE news (key TEXT, title TEXT, content_ja TEXT, status TEXT, created_at TEXT)")
    for r in rows:
        c.execute("INSERT INTO news VALUES (?,?,?,?,?)", r)
    c.commit(); c.close()


def test_find_related(tmp_path):
    db = str(tmp_path / "n.db")
    _mk(db, [
        ("k" * 40, "乃木坂 小津玲奈 写真", "小津玲奈", "active", "2026-10-08 10:00:00"),
        ("j" * 40, "小津玲奈 6期生インタビュー", "小津玲奈 インタビュー", "active", "2026-10-08 11:00:00"),
        ("z" * 40, "无关标题不好判断", "无关内容", "active", "2026-10-08 12:00:00"),
    ])
    res = related.find_related("k" * 40, "乃木坂 小津玲奈 写真", db=db)
    assert any(r["key"] == "j" * 40 for r in res), res
    assert all(r["key"] != "k" * 40 for r in res)


def test_find_related_date_window(tmp_path):
    """同名但相隔 30 天 → 日期窗口应排除，不算同事件。"""
    db = str(tmp_path / "w.db")
    _mk(db, [
        ("k" * 40, "相川暖花 活动A", "相川暖花", "active", "2026-10-08 10:00:00"),
        ("old" + "0" * 37, "相川暖花 活动B", "相川暖花", "active", "2026-09-08 10:00:00"),
    ])
    res = related.find_related("k" * 40, "相川暖花 活动A", db=db, max_gap_days=3)
    assert all(r["key"] != "old" + "0" * 37 for r in res), res
