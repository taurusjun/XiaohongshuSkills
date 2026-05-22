#!/usr/bin/env python3
"""xhs_trend_scanner.py — XHS 话题热门内容趋势扫描"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import json
import logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [trend] %(message)s")
logger = logging.getLogger("trend_scanner")


def _get_publisher():
    from scripts.cdp_publish import XiaohongshuPublisher
    pub = XiaohongshuPublisher()
    pub.connect()
    return pub


def scan_topic_trends(keywords: list[str], limit: int = 10) -> list[dict]:
    """对每个 keyword 搜索 XHS 热门内容，提取趋势信号"""
    from scripts.yahoo_common import call_litellm

    publisher = None
    try:
        publisher = _get_publisher()
    except Exception as e:
        logger.warning(f"CDP 未就绪，跳过趋势扫描: {e}")
        return []

    results = []
    for keyword in keywords:
        try:
            feeds_result = publisher.search_feeds(keyword=keyword, sort="最多收藏")
            feeds = feeds_result.get("feeds", [])
            if not feeds:
                continue

            titles = [f.get("title", "") for f in feeds[:10]]
            likes = [f.get("likes", 0) or 0 for f in feeds]
            saves = [f.get("saves", 0) or 0 for f in feeds]
            comments = [f.get("comments", 0) or 0 for f in feeds]
            types = [f.get("type", "image") for f in feeds]
            pub_times = [f.get("pub_time", "") for f in feeds]

            avg_saves = sum(saves) / len(saves) if saves else 0
            avg_comments = sum(comments) / len(comments) if comments else 0
            image_count = sum(1 for t in types if t in ("image", "图文"))
            image_ratio = image_count / len(types) if types else 1.0
            avg_title_len = sum(len(t) for t in titles) / len(titles) if titles else 0

            # is_fresh: >50% 发布时间是今天或昨天
            from datetime import timedelta
            today = datetime.now().strftime("%Y-%m-%d")
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            fresh_count = sum(1 for pt in pub_times if pt and (today in str(pt) or yesterday in str(pt)))
            is_fresh = fresh_count > len(pub_times) / 2 if pub_times else False

            # LLM 提炼推荐关键词
            recommended_keywords = []
            try:
                lite_result = call_litellm(
                    f"从以下 XHS 热门笔记标题中提炼 3-5 个推荐搜索关键词（JSON 字符串数组）：\n" + "\n".join(titles[:5]),
                    temperature=0.3, max_tokens=200,
                )
                if lite_result:
                    recommended_keywords = json.loads(lite_result)
                    if isinstance(recommended_keywords, str):
                        recommended_keywords = [recommended_keywords]
                    recommended_keywords = recommended_keywords[:5]
            except Exception:
                recommended_keywords = [keyword]

            results.append({
                "topic": keyword,
                "top_titles": titles[:5],
                "recommended_keywords": recommended_keywords,
                "image_ratio": round(image_ratio, 2),
                "avg_title_len": int(avg_title_len),
                "baseline_saves": round(avg_saves, 1),
                "baseline_comments": round(avg_comments, 1),
                "is_fresh": is_fresh,
            })
        except Exception as e:
            logger.warning(f"扫描 '{keyword}' 失败: {e}")

    return results


def update_trend_signals(trend_results: list[dict]) -> None:
    """将趋势结果写入 topic_performance 表"""
    from scripts.sqlite_db import upsert_topic_performance
    for r in trend_results:
        upsert_topic_performance(
            r["topic"],
            saves=r.get("baseline_saves", 0),
            comments=r.get("baseline_comments", 0),
            topic_baseline_saves=r.get("baseline_saves", 0),
            topic_baseline_comments=r.get("baseline_comments", 0),
            trend_signal={"is_fresh": r.get("is_fresh", False),
                          "top_titles": r.get("top_titles", []),
                          "recommended_keywords": r.get("recommended_keywords", [])},
            trend_updated_at=datetime.now().strftime("%Y-%m-%d %H:%M"),
        )


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="XHS 趋势扫描")
    p.add_argument("--keywords", default="写真集,日本女星")
    p.add_argument("--limit", type=int, default=10)
    args = p.parse_args()

    keywords = [k.strip() for k in args.keywords.split(",")]
    trends = scan_topic_trends(keywords, limit=args.limit)
    if trends:
        update_trend_signals(trends)
        print(f"Updated {len(trends)} topic trends")
    else:
        print("No trends found (CDP may be unavailable)")
