import datetime as dt
import sqlite3

from services import recommend


def test_priority_fresh_beats_old():
    today = dt.date(2026, 10, 9)
    fresh = {"created_at": "2026-10-09 01:00", "title_score": 0}
    old = {"created_at": "2026-10-06 01:00", "title_score": 0}
    stat = {"n": 0, "median": 0, "max": 0}
    assert recommend.priority(fresh, stat, today) > recommend.priority(old, stat, today)


def test_priority_history_helps():
    today = dt.date(2026, 10, 9)
    c = {"created_at": "2026-10-09 01:00", "title_score": 0}
    lo = {"n": 0, "median": 0, "max": 0}
    hi = {"n": 40, "median": 800, "max": 3000}
    assert recommend.priority(c, hi, today) > recommend.priority(c, lo, today)


def test_data_overview_gap_and_pending(tmp_path):
    db = str(tmp_path / "o.db")
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE news (publish_time TEXT, publish_xhs INT, xhs_pub_time TEXT)")
    conn.execute("INSERT INTO news VALUES ('2026-10-06 10:00',1,'')")
    conn.execute("INSERT INTO news VALUES ('2026-10-08 10:00',1,'')")
    conn.execute("INSERT INTO news VALUES ('',1,'2026-10-10 09:00')")   # 待发（已排未发）
    conn.commit(); conn.close()
    ov = recommend.data_overview(dt.date(2026, 10, 9), [], db=db)
    assert ov["gap_days"] == 1
    assert ov["pending"] == 1
