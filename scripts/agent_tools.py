#!/usr/bin/env python3
"""agent_tools.py — 智能体执行层工具封装"""

from scripts.scoring import HINT_MAP


def regenerate_with_hint(article: dict, failed_dims: list[str]) -> dict:
    """根据失败维度构造修正提示词，返回更新后的 article dict

    HINT_MAP 包含常见失败维度的修正提示词。
    调用 generate_content_and_comment 时传入 hint 作为额外 system prompt。
    """
    hints = [HINT_MAP[d] for d in failed_dims if d in HINT_MAP]
    if not hints:
        return article

    hint_text = " ".join(hints)
    # 将 hint 写入 article，供 generate_content_and_comment 使用
    article = dict(article)
    article["_regen_hint"] = hint_text
    return article


def fetch_by_keywords(keywords: list, limit: int = 5) -> list[dict]:
    """通过 CDP 按关键词抓取文章"""
    try:
        from scripts.cdp_publish import XiaohongshuPublisher
        pub = XiaohongshuPublisher()
        pub.connect()
        results = []
        for kw in keywords:
            feeds = pub.search_feeds(keyword=kw, sort="最多收藏", limit=limit)
            results.extend(feeds.get("feeds", []))
        return results
    except Exception as e:
        print(f"  ⚠️ fetch_by_keywords failed: {e}")
        return []


def run_gallery_download(news_key: str) -> bool:
    """触发图集下载（异步）"""
    try:
        from web.gallery_downloader import trigger_download
        trigger_download(news_key)
        return True
    except Exception as e:
        print(f"  ⚠️ run_gallery_download failed: {e}")
        return False


def run_publish_pipeline(news_key: str, publish_time: str = "") -> bool:
    """执行发布流水线"""
    try:
        from scripts.sqlite_db import get_by_key, update_news
        article = get_by_key(news_key)
        if not article:
            return False
        update_news(news_key, {"publish_xhs": 1, "publish_time": publish_time})
        return True
    except Exception as e:
        print(f"  ⚠️ run_publish_pipeline failed: {e}")
        return False
