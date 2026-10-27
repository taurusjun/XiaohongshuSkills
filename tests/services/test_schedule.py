"""services.schedule（pub_time_plan）测试。"""
import datetime as dt
import random
import sqlite3

from services import schedule as sch


def test_parse_slots():
    assert sch.parse_slots("09:00,12:00") == [(9, 0), (12, 0)]
    assert sch.parse_slots("09:00,12:00,") == [(9, 0), (12, 0)]


def test_build_plan_never_on_the_hour_and_deterministic():
    slots = sch.parse_slots("09:00,12:00,15:00,18:00,20:00")
    plan = sch.build_plan(dt.date(2026, 10, 9), slots, 8, random.Random(7))
    assert len(plan) == 5
    for slot, ts in plan:
        assert ts[-2:] != "00"            # 分钟绝不为 0
    plan2 = sch.build_plan(dt.date(2026, 10, 9), slots, 8, random.Random(7))
    assert plan == plan2                  # 同 seed 可复现


def _mkdb(tmp_path):
    db = str(tmp_path / "n.db")
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE news (key TEXT, title TEXT, xhs_pub_time TEXT, "
              "publish_xhs INTEGER, rewritten_content TEXT)")
    c.execute("INSERT INTO news VALUES (?, '标题', '', 0, 'x')", ("k" * 40,))
    c.commit()
    c.close()
    return db


def test_apply_sets_time_and_flag(tmp_path):
    db = _mkdb(tmp_path)
    plan = sch.build_plan(dt.date(2026, 10, 9), sch.parse_slots("09:00"), 8, random.Random(1))
    ok, bad = sch.apply_plan(db, ["k" * 40], plan, True)
    assert not bad and ok
    row = sqlite3.connect(db).execute(
        "SELECT xhs_pub_time, publish_xhs FROM news WHERE key=?", ("k" * 40,)).fetchone()
    assert row[0] == plan[0][1] and row[1] == 1


def test_apply_no_publish_flag(tmp_path):
    db = _mkdb(tmp_path)
    plan = sch.build_plan(dt.date(2026, 10, 9), sch.parse_slots("09:00"), 8, random.Random(1))
    sch.apply_plan(db, ["k" * 40], plan, False)
    row = sqlite3.connect(db).execute(
        "SELECT xhs_pub_time, publish_xhs FROM news WHERE key=?", ("k" * 40,)).fetchone()
    assert row[0] == plan[0][1] and row[1] == 0


def test_apply_missing_key_reported(tmp_path):
    db = _mkdb(tmp_path)
    plan = sch.build_plan(dt.date(2026, 10, 9), sch.parse_slots("09:00"), 8, random.Random(1))
    ok, bad = sch.apply_plan(db, ["z" * 40], plan, True)
    assert not ok and bad and bad[0][1] == "key 不存在"


def _mkdb_full(tmp_path):
    """带保真度相关列的库（模拟生产表结构）"""
    import sqlite3
    db = str(tmp_path / "full.db")
    c = sqlite3.connect(db)
    c.execute(
        "CREATE TABLE news (key TEXT, title TEXT, xhs_pub_time TEXT, publish_xhs INTEGER,"
        " rewritten_content TEXT, cluster_id TEXT, format TEXT, is_long_form INTEGER,"
        " publish_mode TEXT, preselected INTEGER)")
    c.commit(); c.close()
    return db


def test_preflight_rejects_short_story(tmp_path):
    """story&lf 正文 <800 → 拒绝排期（保真度审计 ①）"""
    import sqlite3
    db = _mkdb_full(tmp_path)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?,?)",
              ("k" * 40, "t", "", 0, "短" * 100, "", "story", 1, "rewritten", 1))
    c.commit(); c.close()
    ok, rej = sch.preflight(db, ["k" * 40])
    assert ok == [] and rej and "800" in rej[0][1]


def test_preflight_rejects_same_cluster(tmp_path):
    """同簇已有待发布/已发布 → 后续成员拒绝排期（②）"""
    import sqlite3
    db = _mkdb_full(tmp_path)
    c = sqlite3.connect(db)
    c.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?,?)",
              ("a" * 40, "t1", "2026-10-27 09:07", 1, "正文" * 500, "C1", "news", 0, "rewritten", 1))
    c.execute("INSERT INTO news VALUES (?,?,?,?,?,?,?,?,?,?)",
              ("b" * 40, "t2", "", 0, "正文" * 500, "C1", "news", 0, "rewritten", 1))
    c.commit(); c.close()
    ok, rej = sch.preflight(db, ["b" * 40])
    assert ok == [] and rej and "同簇" in rej[0][1]
