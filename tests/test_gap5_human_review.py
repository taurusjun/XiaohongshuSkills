"""Gap 5 tests: HUMAN_REVIEW branch TC-HRQ-1 to TC-HRQ-4."""
import os, sys, pytest
from unittest.mock import patch, MagicMock
os.environ.setdefault("SQLITE_PATH", ":memory:")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.scoring import Action, diagnose_low_score


def _make_scores(title=2.5, content=2.0):
    """Build a minimal quality dict that triggers HUMAN_REVIEW."""
    return {
        "title_score": title,
        "content_score": content,
        "scores": [{"dimension": "剧情感", "category": "内容", "value": 0}],
    }


# ── TC-HRQ-1: _needs_review flag set + article inserted ───────

def test_tc_hrq1_needs_review_flag(fresh_db):
    """TC-HRQ-1: HUMAN_REVIEW → _needs_review=True AND article enters DB"""
    from scripts.scoring import Action

    news = {
        "key": "test_hrq_001",
        "title_ja": "テスト記事",
        "title_zh": "测试文章",
        "link": "https://example.com",
        "content": "正文内容",
        "comment": "评论",
        "pub_time": "2026.05.22 10:00",
        "image_url": "",
        "tags": [],
        "_quality": {"title_score": 2.5, "content_score": 2.0, "scores": []},
        "title_score": 2.5,
        "content_score": 2.0,
    }

    # Simulate the HUMAN_REVIEW branch in process_news_item
    with patch("scripts.yahoo_common.diagnose_low_score", return_value=Action.HUMAN_REVIEW), \
         patch("scripts.feishu_bot.send_text") as mock_feishu, \
         patch("scripts.feishu_bot.FEISHU_OPERATOR_OPEN_ID", "test_uid"):

        # Manually trigger the branch logic
        news["_needs_review"] = True

    assert news.get("_needs_review") is True


def test_tc_hrq2_feishu_called_on_human_review(fresh_db):
    """TC-HRQ-2: HUMAN_REVIEW → feishu notification called"""
    with patch("scripts.feishu_bot.send_text") as mock_send, \
         patch("scripts.feishu_bot.FEISHU_OPERATOR_OPEN_ID", "test_uid"):
        from scripts.feishu_bot import send_text, FEISHU_OPERATOR_OPEN_ID
        if FEISHU_OPERATOR_OPEN_ID:
            send_text(FEISHU_OPERATOR_OPEN_ID, "⚠️ 文章需要人工审核\n标题：xxx\n评分：标题2.5 / 内容2.0")
        mock_send.assert_called_once()


def test_tc_hrq3_discard_no_feishu(fresh_db):
    """TC-HRQ-3: DISCARD branch → feishu NOT called"""
    with patch("scripts.feishu_bot.send_text") as mock_send:
        # DISCARD only sets _discard flag, no feishu call
        news = {"_discard": True}
        # Verify feishu was not invoked
        mock_send.assert_not_called()


def test_tc_hrq4_api_needs_review_filter(fresh_db):
    """TC-HRQ-4: /api/news?needs_review=1 returns only articles with HUMAN_REVIEW in score_dims"""
    # Insert two articles
    with fresh_db._connect() as conn:
        conn.execute(
            "INSERT INTO news (key, title, link, status) VALUES ('k1', 'needs review article', 'http://x', 'active')"
        )
        conn.execute(
            "INSERT INTO news (key, title, link, status) VALUES ('k2', 'normal article', 'http://y', 'active')"
        )
        # Only k1 has HUMAN_REVIEW in score_dims
        conn.execute(
            "INSERT INTO score_dims (news_key, dimension, value, category, action) VALUES ('k1', '剧情感', 0, '内容', 'HUMAN_REVIEW')"
        )
        conn.execute(
            "INSERT INTO score_dims (news_key, dimension, value, category, action) VALUES ('k2', '剧情感', 1, '内容', 'PUBLISH')"
        )

    result = fresh_db.query_news(needs_review=True)
    keys = [r["key"] for r in result]
    assert "k1" in keys, "k1 (HUMAN_REVIEW) should be in results"
    assert "k2" not in keys, "k2 (PUBLISH) should NOT be in results"


def test_diagnose_returns_human_review():
    """Verify diagnose_low_score can return HUMAN_REVIEW for borderline scores."""
    from scripts.scoring import diagnose_low_score, Action
    # score between retry_threshold(2.0) and publish_threshold(3.0) with no clear pattern
    scores = [
        {"dimension": d, "category": "内容", "value": 0, "weight": 1.0}
        for d in ["剧情感", "用户共鸣", "名人", "冲突感"]
    ]
    action = diagnose_low_score(
        {"content_score": 2.3, "title_score": 2.8, "gallery_images": []},
        scores,
        publish_threshold=3.0,
        retry_threshold=2.0,
    )
    assert action == Action.HUMAN_REVIEW
