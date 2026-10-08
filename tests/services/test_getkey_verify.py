import sqlite3
from services import draft_io


def _makedb(tmp_path):
    db = str(tmp_path / "n.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE news (id INTEGER PRIMARY KEY AUTOINCREMENT, key TEXT, title TEXT, "
              "rewritten_title TEXT, rewritten_content TEXT, publish_mode TEXT, preselected INTEGER, "
              "publish_xhs INTEGER, status TEXT, content_ja TEXT, related_keys TEXT, channel TEXT)")
    c.execute("INSERT INTO news (key,title,rewritten_title,rewritten_content,preselected,publish_xhs,status,content_ja) "
              "VALUES (?, 'ラウール登场', '', '', 0, 0, 'active', 'あいう')", ("k" * 40,))
    c.commit(); c.close()
    return db


def test_get_key_by_keyword(tmp_path):
    rows = draft_io.get_key("ラウール", db=_makedb(tmp_path))
    assert len(rows) == 1 and rows[0][0] == "k" * 40 and rows[0][2] == "未写"


def test_verify_rewrite_checks():
    def fake_fetch(key, api_base=None):
        return {"rewritten_title": "标题", "rewritten_content": "正文内容",
                "preselected": 1, "publish_xhs": 1, "publish_mode": "rewritten",
                "related_keys": "x", "channel": ""}
    r = draft_io.verify_rewrite("k" * 40, fetch=fake_fetch)
    assert r["checks"]["preselected"] and r["checks"]["publish_xhs"]
    assert r["checks"]["标题不以#开头"] and r["checks"]["正文开头不是标题"]


def test_verify_rewrite_title_in_body():
    def fake_fetch(key, api_base=None):
        return {"rewritten_title": "标题X", "rewritten_content": "标题X正文",
                "preselected": 0, "publish_xhs": 0, "publish_mode": "", "related_keys": "", "channel": ""}
    r = draft_io.verify_rewrite("k" * 40, fetch=fake_fetch)
    assert r["checks"]["正文开头不是标题"] is False
