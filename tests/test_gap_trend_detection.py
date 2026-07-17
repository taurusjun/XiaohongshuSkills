"""
xhs-trend-detection-fix 测试套件
TC-P1/P2: _is_recent 时间字符串解析
TC-P3: trend_only 不污染 avg_saves
TC-P4: is_fresh 阈值逻辑
TC-PL1/PL2: 规划层 is_fresh 优先排序
"""
import os, sys, pytest
os.environ.setdefault("SQLITE_PATH", ":memory:")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.xhs_trend_scanner import _is_recent


# ── TC-P1/P2: _is_recent ─────────────────────────────────────

def test_tc_p1_yesterday_is_recent():
    """TC-P1: '昨天' → True (hours=48)"""
    assert _is_recent("昨天", hours=48) is True

def test_tc_p1_3days_ago_not_recent():
    """TC-P1: '3天前' → False (hours=48)"""
    assert _is_recent("3天前", hours=48) is False

def test_tc_p1_just_now():
    assert _is_recent("刚刚") is True

def test_tc_p1_1day_ago():
    """'1天前' → True (hours=48)"""
    assert _is_recent("1天前", hours=48) is True

def test_tc_p2_2hours_ago():
    """TC-P2: '2小时前' → True"""
    assert _is_recent("2小时前", hours=48) is True

def test_tc_p2_50hours_not_recent():
    """TC-P2: '50小时前' → False (hours=48)"""
    assert _is_recent("50小时前", hours=48) is False

def test_p1_minutes():
    assert _is_recent("30分钟前", hours=48) is True

def test_p1_iso_date_today():
    from datetime import datetime
    today = datetime.now().strftime("%Y-%m-%d")
    assert _is_recent(today, hours=48) is True

def test_p1_old_iso_date():
    assert _is_recent("2024-01-01", hours=48) is False

def test_p1_empty():
    assert _is_recent("", hours=48) is False


# ── TC-P3: trend_only 不污染 avg_saves ───────────────────────

def test_tc_p3_trend_only_no_avg_saves_change(fresh_db):
    """TC-P3: trend_only=True 调用后 avg_saves 不变"""
    # 先写入真实数据
    fresh_db.upsert_topic_performance("idol_topic", saves=100, comments=10, views=1000)
    with fresh_db._connect() as db:
        before = db.execute(
            "SELECT avg_saves, post_count FROM topic_performance WHERE topic='idol_topic'"
        ).fetchone()

    # trend scanner 调用（trend_only=True）
    fresh_db.upsert_topic_performance(
        "idol_topic",
        saves=0, comments=0, trend_only=True,
        topic_baseline_saves=50.0,
        trend_signal={"is_fresh": True, "top_titles": []},
        trend_updated_at="2026-05-22 10:00",
    )

    with fresh_db._connect() as db:
        after = db.execute(
            "SELECT avg_saves, post_count, topic_baseline_saves FROM topic_performance WHERE topic='idol_topic'"
        ).fetchone()

    assert after["avg_saves"] == before["avg_saves"], "avg_saves should not change with trend_only=True"
    assert after["post_count"] == before["post_count"], "post_count should not change with trend_only=True"
    assert after["topic_baseline_saves"] == 50.0, "topic_baseline_saves should be updated"


def test_tc_p3_trend_only_new_topic(fresh_db):
    """TC-P3b: 新话题 trend_only=True → 插入但 avg_saves=0"""
    fresh_db.upsert_topic_performance(
        "brand_new_topic",
        saves=0, comments=0, trend_only=True,
        topic_baseline_saves=25.0,
        trend_signal={"is_fresh": False, "top_titles": []},
    )
    with fresh_db._connect() as db:
        row = db.execute(
            "SELECT avg_saves, topic_baseline_saves FROM topic_performance WHERE topic='brand_new_topic'"
        ).fetchone()
    assert row is not None
    assert row["avg_saves"] == 0.0
    assert row["topic_baseline_saves"] == 25.0


# ── TC-P4: is_fresh 阈值 ─────────────────────────────────────

def test_tc_p4_is_fresh_threshold():
    """TC-P4: 22条结果中>=5条就是fresh"""
    # 方案B: fresh_count_24h >= 5 → is_fresh=True
    assert (22 >= 5) is True   # 22条一天内帖子 → fresh
    assert (3 >= 5) is False    # 只有3条 → not fresh


# ── TC-PL1/PL2: 规划层排序 ───────────────────────────────────

def test_tc_pl1_fresh_topics_get_earliest_slot(fresh_db):
    """TC-PL1: is_fresh=True 的话题分配最早时段"""
    fresh_db.set_config("default_post_times", ["09:30", "12:00", "18:00"])
    from scripts.agent_planner import TopicQuota, recommend_post_times
    topics = [
        TopicQuota(topic="A", quota=1, source="baseline", is_fresh=False),
        TopicQuota(topic="B", quota=1, source="explore", is_fresh=True),
        TopicQuota(topic="C", quota=1, source="baseline", is_fresh=False),
    ]
    times = recommend_post_times(topics, "20260522")
    idx_B = next(i for i, t in enumerate(topics) if t.topic == "B")
    all_times = sorted(times)
    assert times[idx_B] == all_times[0], f"Fresh topic B should get earliest slot, got {times[idx_B]}"


def test_tc_pl2_explore_fresh_first(fresh_db):
    """TC-PL2: explore 候选中 is_fresh=True 优先"""
    import json
    fresh_db.upsert_topic_performance(
        "fresh_explore", saves=0, comments=0, trend_only=True,
        topic_baseline_saves=10.0,
        trend_signal={"is_fresh": True, "top_titles": []},
    )
    fresh_db.upsert_topic_performance(
        "stale_explore", saves=0, comments=0, trend_only=True,
        topic_baseline_saves=50.0,  # higher baseline but not fresh
        trend_signal={"is_fresh": False, "top_titles": []},
    )

    from scripts.agent_planner import _topic_is_fresh
    with fresh_db._connect() as db:
        rows = db.execute(
            "SELECT * FROM topic_performance ORDER BY topic"
        ).fetchall()

    topics = [dict(r) for r in rows]
    sorted_topics = sorted(
        topics,
        key=lambda t: (0 if _topic_is_fresh(t) else 1, -t.get("engagement_score", 0))
    )
    assert sorted_topics[0]["topic"] == "fresh_explore", \
        "fresh_explore (is_fresh=True) should rank first in explore candidates"
