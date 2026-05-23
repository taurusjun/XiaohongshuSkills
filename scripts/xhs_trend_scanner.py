#!/usr/bin/env python3
"""xhs_trend_scanner.py — XHS 话题热门内容趋势扫描"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re
import json
import logging
from datetime import datetime, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [trend] %(message)s")
logger = logging.getLogger("trend_scanner")


def _is_recent(text: str, hours: int = 48) -> bool:
    """判断 XHS pub_time 字符串是否在 hours 小时内。
    处理格式：'刚刚' / 'x分钟前' / 'x小时前' / '昨天' / 'x天前' / ISO日期 '2024-05-22'
    """
    if not text:
        return False
    t = str(text).strip()
    if t in ("刚刚",):
        return True
    m = re.match(r"(\d+)\s*分钟前", t)
    if m:
        return int(m.group(1)) <= hours * 60
    m = re.match(r"(\d+)\s*小时前", t)
    if m:
        return int(m.group(1)) <= hours
    if t == "昨天":
        return hours >= 24
    m = re.match(r"(\d+)\s*天前", t)
    if m:
        return int(m.group(1)) * 24 <= hours
    try:
        pub = datetime.strptime(t[:10], "%Y-%m-%d")
        return (datetime.now() - pub).total_seconds() / 3600 <= hours
    except Exception:
        return False


def _get_publisher():
    from scripts.cdp_publish import XiaohongshuPublisher
    pub = XiaohongshuPublisher()
    pub.connect()
    return pub


def _nc(f): return f.get("noteCard", {})
def _ii(f): return _nc(f).get("interactInfo", {})
def _pub_time(f):
    for tag in _nc(f).get("cornerTagInfo", []):
        if tag.get("type") == "publish_time":
            return tag.get("text", "")
    return ""


def _extract_feeds_data(feeds: list) -> dict:
    """从 feeds 列表提取统计数据。"""
    def _pub_time_local(f):
        for tag in _nc(f).get("cornerTagInfo", []):
            if tag.get("type") == "publish_time":
                return tag.get("text", "")
        return ""

    titles = [_nc(f).get("displayTitle", "") for f in feeds[:10]]
    saves = [int(_ii(f).get("collectedCount", 0) or 0) for f in feeds]
    comments = [int(_ii(f).get("commentCount", 0) or 0) for f in feeds]
    types = [_nc(f).get("type", "normal") for f in feeds]
    pub_times = [_pub_time(f) for f in feeds]

    # 截尾均值（去掉最高值防单篇爆款拉高）
    saves_s = sorted(saves)
    trim_saves = saves_s[:-1] if len(saves_s) > 3 else saves_s
    baseline_saves = sum(trim_saves) / len(trim_saves) if trim_saves else 0

    comments_s = sorted(comments)
    trim_comments = comments_s[:-1] if len(comments_s) > 3 else comments_s
    baseline_comments = sum(trim_comments) / len(trim_comments) if trim_comments else 0

    image_count = sum(1 for t in types if t in ("image", "normal", "图文"))
    image_ratio = image_count / len(types) if types else 1.0
    avg_title_len = sum(len(t) for t in titles) / len(titles) if titles else 0

    return {
        "titles": titles,
        "pub_times": pub_times,
        "baseline_saves": round(baseline_saves, 1),
        "baseline_comments": round(baseline_comments, 1),
        "image_ratio": round(image_ratio, 2),
        "avg_title_len": int(avg_title_len),
    }


def scan_topic_trends(keywords: list[str], limit: int = 10) -> list[dict]:
    """对每个 keyword 双路扫描 XHS：
    - Pass 1 综合排序（sort="general"）→ 质量基线（baseline_saves、内容类型）
    - Pass 2 最新+一天内（sort="newest", time_filter="1day"）→ 话题活跃度（is_fresh）
    """
    from scripts.yahoo_common import call_litellm

    publisher = None
    try:
        publisher = _get_publisher()
    except Exception as e:
        logger.warning(f"CDP 未就绪，跳过趋势扫描: {e}")
        return []

    def _search_with_ratelimit_check(kw: str, **kwargs) -> dict:
        """调 search_feeds，遇到频率限制立即上抛——需要人工介入解验证码。"""
        return publisher.search_feeds(keyword=kw, **kwargs)
        # XHSRateLimitError 会自动上抛 → scan_topic_trends except → agent_runner _alert → 飞书告警

    results = []
    for keyword in keywords:
        try:
            # ── Pass 1: 综合排序，质量基线 ─────────────────────────
            feeds_general = _search_with_ratelimit_check(keyword, sort="general")
            feeds_g = feeds_general.get("feeds", [])
            if not feeds_g:
                raise ValueError(f"XHS 搜索「{keyword}」返回0条结果，可能未登录或关键词无效")
            data_g = _extract_feeds_data(feeds_g)

            # ── Pass 2: 最新排序（不限时间），手动统计真实24h内帖数 ────
            # 不用 time_filter="1day"：XHS 的"一天内"实际覆盖约40h，且页面上限22条
            # 无法区分高热话题（100篇/天）和低活话题（3篇/天）
            # 改为：抓最新22条，按发布时间精确统计24h内的数量
            import time as _time, random as _random
            _time.sleep(_random.uniform(3.0, 6.0))

            is_fresh = False
            fresh_count_24h = 0
            try:
                feeds_newest = _search_with_ratelimit_check(
                    keyword, sort="newest"  # 不加 time_filter
                )
                feeds_n = feeds_newest.get("feeds", [])
                # 用 _is_recent 精确统计真实24h内帖数
                fresh_count_24h = sum(
                    1 for f in feeds_n
                    if _is_recent(_pub_time(f), hours=24)
                )
                # is_fresh = 真实24h内 ≥3 篇（低门槛，区分有无活跃）
                is_fresh = fresh_count_24h >= 3
                logger.info(f"  [{keyword}] 真实24h帖数={fresh_count_24h} is_fresh={is_fresh}")
            except Exception as e:
                logger.warning(f"  [{keyword}] Pass2 活跃度扫描失败: {e}")

            # ── LLM 提炼推荐关键词 ────────────────────────────────
            titles = data_g["titles"]
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
                "image_ratio": data_g["image_ratio"],
                "avg_title_len": data_g["avg_title_len"],
                "baseline_saves": data_g["baseline_saves"],
                "baseline_comments": data_g["baseline_comments"],
                "is_fresh": is_fresh,
                "fresh_count_24h": fresh_count_24h,
            })
        except Exception as e:
            from scripts.cdp_publish import XHSRateLimitError
            if isinstance(e, XHSRateLimitError):
                # 安全验证需要人工介入，不重试，直接上抛让 agent_runner _alert 处理
                raise
            # 其他错误（超时等）：飞书告警 + 等待 5 分钟重试一次
            logger.warning(f"扫描 '{keyword}' 失败: {e}，5分钟后重试...")
            try:
                from scripts.feishu_bot import send_text, FEISHU_OPERATOR_OPEN_ID
                if FEISHU_OPERATOR_OPEN_ID:
                    send_text(FEISHU_OPERATOR_OPEN_ID,
                              f"⚠️ 趋势扫描失败，5分钟后自动重试\n话题: {keyword}\n原因: {e}")
            except Exception:
                pass
            import time as _time
            _time.sleep(300)  # 等待 5 分钟
            # 重试一次
            try:
                feeds_retry = _search_with_ratelimit_check(keyword, sort="general")
                feeds_r = feeds_retry.get("feeds", [])
                if feeds_r:
                    data_r = _extract_feeds_data(feeds_r)
                    is_fresh_r = False
                    fresh_count_r = 0
                    try:
                        feeds_newest_r = _search_with_ratelimit_check(keyword, sort="newest", time_filter="1day")
                        feeds_n_r = feeds_newest_r.get("feeds", [])
                        fresh_count_r = len(feeds_n_r)
                        is_fresh_r = fresh_count_r >= 5
                    except Exception:
                        pass
                    results.append({
                        "topic": keyword, "top_titles": data_r["titles"][:5],
                        "recommended_keywords": [keyword],
                        "image_ratio": data_r["image_ratio"],
                        "avg_title_len": data_r["avg_title_len"],
                        "baseline_saves": data_r["baseline_saves"],
                        "baseline_comments": data_r["baseline_comments"],
                        "is_fresh": is_fresh_r, "fresh_count_24h": fresh_count_r,
                    })
                    logger.info(f"  [{keyword}] 重试成功")
            except Exception as e2:
                logger.error(f"扫描 '{keyword}' 重试失败: {e2}")

        # 话题间随机延时，避免连续请求触发频率限制
        if keyword != keywords[-1]:
            import time as _time, random as _random
            _time.sleep(_random.uniform(5.0, 10.0))

    return results


def update_trend_signals(trend_results: list[dict]) -> None:
    """将趋势结果写入 topic_performance 表"""
    from scripts.sqlite_db import upsert_topic_performance
    for r in trend_results:
        upsert_topic_performance(
            r["topic"],
            saves=0,           # 不污染 avg_saves 滚动均值
            comments=0,        # 不污染 avg_comments 滚动均值
            trend_only=True,   # 只更新 baseline / trend_signal
            topic_baseline_saves=r.get("baseline_saves", 0),
            topic_baseline_comments=r.get("baseline_comments", 0),
            trend_signal={"is_fresh": r.get("is_fresh", False),
                          "fresh_count_24h": r.get("fresh_count_24h", 0),
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
