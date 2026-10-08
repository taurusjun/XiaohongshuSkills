import sqlite3

from services import import_drafts as imp

K = "k" * 40
UNREACHABLE = "http://127.0.0.1:1"  # 强制回落 sqlite3


def _makedb(tmp_path):
    db = str(tmp_path / "n.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE news (key TEXT, title TEXT, rewritten_title TEXT, "
              "rewritten_content TEXT, publish_mode TEXT, preselected INTEGER, "
              "publish_xhs INTEGER, related_keys TEXT, wechat_title TEXT, "
              "wechat_content TEXT, channel TEXT)")
    c.execute("INSERT INTO news (key, title, preselected, publish_xhs) VALUES (?, '标题', 0, 0)", (K,))
    c.commit()
    c.close()
    return db


def _mkdraft(tmp_path, name, title, body):
    d = tmp_path / "drafts"
    d.mkdir(exist_ok=True)
    (d / name).write_text(f"## {title}\n{body}", encoding="utf-8")
    return str(d)


def test_import_xhs_fallback_sqlite(tmp_path):
    db = _makedb(tmp_path)
    d = _mkdraft(tmp_path, f"xhs_draft_{K[:12]}.md", "新标题", "正" * 100)
    ok, gzh_ok, fail = imp.import_drafts(d, None, {}, set(), db=db, api_base=UNREACHABLE)
    assert not fail and len(ok) == 1
    row = sqlite3.connect(db).execute(
        "SELECT rewritten_title, publish_mode, preselected, publish_xhs FROM news WHERE key=?",
        (K,)).fetchone()
    assert row[0] == "新标题" and row[1] == "rewritten" and row[2] == 1 and row[3] == 0


def test_import_key_not_found(tmp_path):
    db = _makedb(tmp_path)
    d = _mkdraft(tmp_path, "xhs_draft_zzzzzzzzzzzz.md", "T", "正" * 100)
    ok, gzh_ok, fail = imp.import_drafts(d, None, {}, set(), db=db, api_base=UNREACHABLE)
    assert fail and fail[0][1] == "KEY_NOT_FOUND"


def test_import_gzh_writes_wechat(tmp_path):
    db = _makedb(tmp_path)
    d = _mkdraft(tmp_path, f"gzh_draft_{K[:12]}.md", "公号标题", "公" * 100)
    ok, gzh_ok, fail = imp.import_drafts(d, None, {}, {K[:12]}, db=db, api_base=UNREACHABLE)
    assert gzh_ok and not fail
    row = sqlite3.connect(db).execute(
        "SELECT wechat_title, channel, preselected FROM news WHERE key=?", (K,)).fetchone()
    assert row[0] == "公号标题" and row[1] == "gzh" and row[2] == 0
