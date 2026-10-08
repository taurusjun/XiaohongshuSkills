import sqlite3
from services import related


def test_find_related(tmp_path):
    db = str(tmp_path / "n.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE news (key TEXT, title TEXT, content_ja TEXT, status TEXT)")
    c.execute("INSERT INTO news VALUES (?,?,?,?)", ("k" * 40, "乃木坂 小津玲奈 写真", "小津玲奈", "active"))
    c.execute("INSERT INTO news VALUES (?,?,?,?)", ("j" * 40, "小津玲奈 6期生", "小津玲奈 インタビュー", "active"))
    c.execute("INSERT INTO news VALUES (?,?,?,?)", ("z" * 40, "无关标题", "无关", "active"))
    c.commit(); c.close()
    res = related.find_related("k" * 40, "乃木坂 小津玲奈 写真", db=db)
    assert any(r["key"] == "j" * 40 for r in res), res
    assert all(r["key"] != "k" * 40 for r in res)
