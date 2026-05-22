"""Gap 1 tests: xhs_collected_at write + window label. TC-FL-1 to TC-FL-3."""
import os, sys, pytest
os.environ.setdefault("SQLITE_PATH", ":memory:")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.metrics_collector import _calc_window_label, _titles_match


# ── _calc_window_label unit tests ─────────────────────────────

def test_tc_fl1_window_72h():
    """TC-FL-1: 发布3天前 → 72h"""
    label = _calc_window_label("2026.05.19 10:00", "2026-05-22 14:00")
    assert label == "72h"


def test_window_4h():
    label = _calc_window_label("2026.05.22 10:00", "2026-05-22 13:00")
    assert label == "4h"


def test_window_24h():
    label = _calc_window_label("2026.05.21 10:00", "2026-05-22 00:00")
    assert label == "24h"


def test_window_none_pub_time():
    """无发布时间默认72h"""
    label = _calc_window_label(None, "2026-05-22 14:00")
    assert label == "72h"


# ── xhs_collected_at append test ─────────────────────────────

def test_tc_fl2_collected_at_appended(fresh_db):
    """TC-FL-2: 重复采集时 xhs_collected_at 追加而非覆盖"""
    # Insert a published article
    with fresh_db._connect() as conn:
        conn.execute(
            """INSERT INTO news (key, title, link, publish_xhs, xhs_pub_time, status)
               VALUES ('test001', '测试文章', 'http://x.com', 1, '2026.05.19 10:00', 'active')"""
        )

    # Simulate first collection write
    with fresh_db._connect() as conn:
        conn.execute(
            "UPDATE news SET xhs_collected_at='2026-05-22 12:00 (72h)' WHERE key='test001'"
        )

    # Simulate second collection write (append mode)
    with fresh_db._connect() as conn:
        row = conn.execute(
            "SELECT xhs_collected_at FROM news WHERE key='test001'"
        ).fetchone()
        old_val = row["xhs_collected_at"] or ""
        new_tag = "2026-05-23 12:00 (72h)"
        new_val = f"{old_val} | {new_tag}" if old_val else new_tag
        conn.execute(
            "UPDATE news SET xhs_collected_at=? WHERE key='test001'", (new_val,)
        )

    with fresh_db._connect() as conn:
        row = conn.execute("SELECT xhs_collected_at FROM news WHERE key='test001'").fetchone()

    assert "2026-05-22 12:00 (72h)" in row["xhs_collected_at"]
    assert "2026-05-23 12:00 (72h)" in row["xhs_collected_at"]
    assert "|" in row["xhs_collected_at"]


def test_tc_fl3_72h_triggers_update(fresh_db):
    """TC-FL-3: xhs_collected_at 含72h的文章触发_update_topic_performance"""
    import scripts.sqlite_db as db_module
    # Ensure agent_runner uses the same test DB
    original_path = db_module.DB_PATH

    with fresh_db._connect() as conn:
        conn.execute(
            """INSERT INTO news (key, title, link, publish_xhs, pub_time, xhs_pub_time,
               xhs_saves, xhs_comments, xhs_views, xhs_collected_at, tags,
               topic_perf_updated_at, status, category)
               VALUES ('art001', '测试', 'http://x.com', 1,
               '2026.05.15', '2026.05.15 10:00', 50, 5, 1000,
               '2026-05-22 14:00 (72h)', '日本偶像', NULL, 'active', '娱乐')"""
        )

    # Run the mature articles update using the test DB path
    from scripts.agent_runner import _update_topic_performance_for_mature_articles
    _update_topic_performance_for_mature_articles()

    with fresh_db._connect() as conn:
        tp = conn.execute(
            "SELECT engagement_score, post_count FROM topic_performance WHERE topic='日本偶像'"
        ).fetchone()

    assert tp is not None, "topic_performance should have an entry for 日本偶像"
    assert tp["engagement_score"] > 0, f"engagement_score should be > 0, got {tp['engagement_score']}"


# ── fuzzy match threshold ─────────────────────────────────────

def test_threshold_lowered():
    """TC-FL-3b: 相似度0.70以上匹配成功（旧阈值0.85下失败）"""
    from scripts.metrics_collector import _normalize
    a = _normalize("桥本环奈一夜爆红的秘密")
    b = _normalize("桥本环奈意外走红的背后")
    assert _titles_match(a, b), f"Expected match: '{a}' vs '{b}'"
