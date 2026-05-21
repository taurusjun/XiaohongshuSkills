#!/usr/bin/env python3
"""metrics_collector.py — 发布后 4h/24h/72h 自动回收实发数据（浏览/点赞/收藏/评论）"""

import sys
import os
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [metrics] %(message)s")
logger = logging.getLogger("metrics_collector")

COLLECTION_WINDOWS = [("4h", 4), ("24h", 24), ("72h", 72)]


def get_articles_pending_collection() -> list[dict]:
    """查询需要回收的文章：publish_xhs=1, xhs_pub_time 非空"""
    from scripts.sqlite_db import _connect
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM news WHERE publish_xhs=1 AND xhs_pub_time IS NOT NULL AND xhs_pub_time!='' AND status='active'"
        ).fetchall()
    return [dict(r) for r in rows]


def needs_collection(article: dict, label: str, hours: int) -> bool:
    """判断某文章在某时间点是否需要回收"""
    pub_time_str = article.get("xhs_pub_time", "")
    if not pub_time_str:
        return False
    try:
        pub_time = datetime.strptime(pub_time_str, "%Y-%m-%d %H:%M")
    except ValueError:
        try:
            pub_time = datetime.strptime(pub_time_str, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return False
    elapsed = (datetime.now() - pub_time).total_seconds() / 3600
    if elapsed < hours:
        return False
    collected = article.get("xhs_collected_at", "")
    if label in collected.split(","):
        return False
    return True


def collect_article(publisher, article: dict, label: str) -> bool:
    """对单篇文章发起 CDP 抓取并写回 DB"""
    from scripts.sqlite_db import update_news

    note_url = article.get("gallery_url", "")
    if not note_url:
        logger.warning(f"  {article.get('key')}: no gallery_url, skip")
        return False

    try:
        stats = publisher.fetch_note_stats(note_url)
    except Exception as e:
        logger.warning(f"  {article.get('key')}: CDP fetch failed: {e}")
        return False

    if not stats:
        return False

    fields = {}
    for field in ["xhs_views", "xhs_likes", "xhs_saves", "xhs_comments"]:
        val = stats.get(field.replace("xhs_", ""))
        if val is not None:
            fields[field] = int(val)

    if not fields:
        return False

    # Append collection mark
    existing = article.get("xhs_collected_at", "")
    new_val = ",".join(filter(None, [existing, label]))
    fields["xhs_collected_at"] = new_val

    update_news(article["key"], fields)
    logger.info(f"  {article.get('key')}: collected ({label}) — {fields}")
    return True


def collect_pending_articles(dry_run: bool = False, single_key: Optional[str] = None) -> dict:
    """遍历所有待回收文章，按时间点判断并回收"""
    articles = get_articles_pending_collection()
    if single_key:
        articles = [a for a in articles if a["key"] == single_key]
        if not articles:
            logger.warning(f"Article {single_key} not found or not pending collection")
            return {"checked": 0, "collected": 0}

    checked, collected = 0, 0
    publisher = None if dry_run else _get_publisher()

    for art in articles:
        for label, hours in COLLECTION_WINDOWS:
            if needs_collection(art, label, hours):
                checked += 1
                if dry_run:
                    logger.info(f"  [dry-run] {art.get('key')} would collect ({label})")
                    collected += 1
                elif publisher and collect_article(publisher, art, label):
                    collected += 1

    return {"checked": checked, "collected": collected}


def _get_publisher():
    """延迟初始化 CDP publisher"""
    from scripts.cdp_publish import XiaohongshuPublisher
    pub = XiaohongshuPublisher()
    pub.connect()
    return pub


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="XHS 实发数据回收器")
    p.add_argument("--key", help="单篇回收（指定 news key）")
    p.add_argument("--dry-run", action="store_true", help="仅检查，不执行 CDP")
    args = p.parse_args()

    result = collect_pending_articles(dry_run=args.dry_run, single_key=args.key)
    print(f"[metrics_collector] Done — checked={result['checked']}, collected={result['collected']}")
