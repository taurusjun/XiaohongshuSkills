from services import gallery


def test_sync_one_way_triggers_uncached(monkeypatch):
    posts = []
    monkeypatch.setattr(gallery, "status", lambda k: {"status": "done" if k == "cached" else "idle"})
    monkeypatch.setattr(gallery, "trigger", lambda k, url="": posts.append(k) or {"status": "started"})
    res = dict(gallery.sync(keys=["cached", "fresh"], wait=False))
    assert res["cached"].startswith("skip")      # 已有缓存 → 不触发
    assert res["fresh"] == "started"             # 未缓存 → one-way 触发
    assert posts == ["fresh"]                    # 只对未缓存 POST


def test_pending_keys_query(monkeypatch, tmp_path):
    import sqlite3
    db = tmp_path / "t.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE news(key TEXT, publish_xhs INTEGER, publish_time TEXT, xhs_pub_time TEXT)")
    c.executemany("INSERT INTO news VALUES(?,?,?,?)",
                  [("A", 1, "", "2026-10-10 09:00"), ("B", 1, "", "2026-10-10 12:00"),
                   ("C", 1, "2026-10-09 10:00", ""), ("D", 0, "", "")])
    c.commit()
    assert gallery.pending_keys(str(db)) == ["A", "B"]
