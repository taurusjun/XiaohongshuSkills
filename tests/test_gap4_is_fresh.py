"""Gap 4 tests: is_fresh propagation TC-AM-3, TC-AM-4"""
import os, sys, json, pytest
os.environ.setdefault("SQLITE_PATH", ":memory:")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def test_tc_am3_update_trend_signals_includes_is_fresh(fresh_db):
    """TC-AM-3: update_trend_signals写入后trend_signal含is_fresh字段"""
    from scripts.xhs_trend_scanner import update_trend_signals
    update_trend_signals([{
        "topic": "乃木坂46",
        "top_titles": ["title1"],
        "recommended_keywords": ["kw1"],
        "baseline_saves": 120.0,
        "baseline_comments": 15.0,
        "is_fresh": True,
        "image_ratio": 0.8,
        "avg_title_len": 18,
    }])

    with fresh_db._connect() as conn:
        row = conn.execute(
            "SELECT trend_signal FROM topic_performance WHERE topic='乃木坂46'"
        ).fetchone()

    assert row is not None, "topic should be inserted"
    ts = json.loads(row["trend_signal"])
    assert "is_fresh" in ts, "trend_signal must contain is_fresh"
    assert ts["is_fresh"] is True


def test_tc_am3_is_fresh_false_stored(fresh_db):
    """TC-AM-3b: is_fresh=False 也正确写入"""
    from scripts.xhs_trend_scanner import update_trend_signals
    update_trend_signals([{
        "topic": "AKB48",
        "top_titles": [],
        "recommended_keywords": [],
        "baseline_saves": 50.0,
        "baseline_comments": 5.0,
        "is_fresh": False,
    }])

    with fresh_db._connect() as conn:
        row = conn.execute(
            "SELECT trend_signal FROM topic_performance WHERE topic='AKB48'"
        ).fetchone()

    ts = json.loads(row["trend_signal"])
    assert ts["is_fresh"] is False


def test_tc_am4_planner_is_fresh_true(fresh_db):
    """TC-AM-4: _topic_is_fresh() 在 is_fresh=True 话题上返回 True"""
    fresh_db.upsert_topic_performance(
        "日向坂46",
        saves=80.0,
        trend_signal={"is_fresh": True, "top_titles": []},
        trend_updated_at="2026-05-22 10:00",
    )

    with fresh_db._connect() as conn:
        row = conn.execute(
            "SELECT * FROM topic_performance WHERE topic='日向坂46'"
        ).fetchone()

    from scripts.agent_planner import _topic_is_fresh
    assert _topic_is_fresh(dict(row)) is True


def test_tc_am4_planner_is_fresh_false(fresh_db):
    """TC-AM-4b: _topic_is_fresh() 在 is_fresh=False 话题上返回 False"""
    fresh_db.upsert_topic_performance(
        "旧话题",
        saves=30.0,
        trend_signal={"is_fresh": False, "top_titles": []},
    )

    with fresh_db._connect() as conn:
        row = conn.execute(
            "SELECT * FROM topic_performance WHERE topic='旧话题'"
        ).fetchone()

    from scripts.agent_planner import _topic_is_fresh
    assert _topic_is_fresh(dict(row)) is False
