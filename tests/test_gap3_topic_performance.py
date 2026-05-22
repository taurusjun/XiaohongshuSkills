"""
Gap 3 tests: upsert_topic_performance rolling average + discard_count preservation.
TC-AM-1 to TC-AM-5 from spec.
"""
import os, sys, pytest
os.environ.setdefault("SQLITE_PATH", ":memory:")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def db(fresh_db):
    return fresh_db


def test_tc_am1_rolling_average(db):
    """TC-AM-1: 同一话题更新2次 saves=10/20，avg_saves=15，post_count=2"""
    db.upsert_topic_performance("test_topic", saves=10, comments=2, views=100)
    db.upsert_topic_performance("test_topic", saves=20, comments=4, views=200)

    with db._connect() as conn:
        row = conn.execute(
            "SELECT avg_saves, avg_comments, avg_views, post_count FROM topic_performance WHERE topic=?",
            ("test_topic",)
        ).fetchone()

    assert row["post_count"] == 2
    assert abs(row["avg_saves"] - 15.0) < 0.001, f"avg_saves expected 15, got {row['avg_saves']}"
    assert abs(row["avg_comments"] - 3.0) < 0.001
    assert abs(row["avg_views"] - 150.0) < 0.001


def test_tc_am2_discard_count_preserved(db):
    """TC-AM-2: upsert后，已有的 discard_count 不被归零"""
    db.upsert_topic_performance("test_topic", saves=10)
    db.increment_topic_discard("test_topic", reason="boring")
    db.increment_topic_discard("test_topic", reason="boring")
    db.increment_topic_discard("test_topic", reason="boring")

    # 再次 upsert（模拟文章成熟数据回写）
    db.upsert_topic_performance("test_topic", saves=20)

    with db._connect() as conn:
        row = conn.execute(
            "SELECT discard_count, post_count FROM topic_performance WHERE topic=?",
            ("test_topic",)
        ).fetchone()

    assert row["discard_count"] == 3, f"discard_count should be 3, got {row['discard_count']}"
    assert row["post_count"] == 2


def test_tc_am3_is_fresh_in_trend_signal(db):
    """TC-AM-3: update_trend_signals 写入后，trend_signal 包含 is_fresh 字段"""
    import json
    db.upsert_topic_performance(
        "idol_topic",
        saves=0,
        trend_signal={"is_fresh": True, "top_titles": ["title1"], "recommended_keywords": ["kw"]},
        trend_updated_at="2026-05-22 10:00",
    )

    with db._connect() as conn:
        row = conn.execute(
            "SELECT trend_signal FROM topic_performance WHERE topic=?",
            ("idol_topic",)
        ).fetchone()

    ts = json.loads(row["trend_signal"])
    assert "is_fresh" in ts, "trend_signal must contain is_fresh key"
    assert ts["is_fresh"] is True


def test_tc_am4_is_fresh_planner(db):
    """TC-AM-4: agent_planner._topic_is_fresh() 在 is_fresh=True 的话题上返回 True"""
    import json
    db.upsert_topic_performance(
        "fresh_topic",
        saves=0,
        trend_signal={"is_fresh": True, "top_titles": []},
        trend_updated_at="2026-05-22 10:00",
    )

    with db._connect() as conn:
        row = conn.execute(
            "SELECT trend_signal FROM topic_performance WHERE topic=?", ("fresh_topic",)
        ).fetchone()

    topic_dict = dict(row)
    from scripts.agent_planner import _topic_is_fresh
    assert _topic_is_fresh(topic_dict) is True


def test_tc_am5_no_division_by_zero(db):
    """TC-AM-5: 首次 upsert（post_count=0 基础）不触发除零错误"""
    # Should not raise
    db.upsert_topic_performance("brand_new_topic", saves=0, comments=0, views=0)

    with db._connect() as conn:
        row = conn.execute(
            "SELECT post_count, avg_saves FROM topic_performance WHERE topic=?",
            ("brand_new_topic",)
        ).fetchone()

    assert row["post_count"] == 1
    assert row["avg_saves"] == 0.0


def test_rolling_average_10_updates(db):
    """Extra: 10次更新后 avg_saves 正确"""
    for i in range(1, 11):
        db.upsert_topic_performance("multi_topic", saves=float(i))

    with db._connect() as conn:
        row = conn.execute(
            "SELECT avg_saves, post_count FROM topic_performance WHERE topic=?",
            ("multi_topic",)
        ).fetchone()

    assert row["post_count"] == 10
    assert abs(row["avg_saves"] - 5.5) < 0.01, f"Expected 5.5, got {row['avg_saves']}"
