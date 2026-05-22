#!/usr/bin/env python3
"""agent_planner.py — 每日内容计划生成器"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class TopicQuota:
    topic: str
    quota: int
    source: str  # "high_perf" | "explore" | "baseline"
    is_fresh: bool = False
    target_format: str = "news"  # 今日目标体裁，来自 content_format_rotation


@dataclass
class DailyPlan:
    date: str
    topics: list[TopicQuota] = field(default_factory=list)
    post_times: list[str] = field(default_factory=list)
    quota_total: int = 3
    mode: str = "cold_start"
    note: str = ""


def plan_today(date: str = "") -> DailyPlan:
    """生成今日内容计划"""
    from scripts.sqlite_db import (get_config, set_state, get_state,
                                    get_top_topics, get_recent_performance)

    if not date:
        date = datetime.now().strftime("%Y%m%d")

    # 幂等：已有计划直接返回
    cached = get_state(f"daily_plan_{date}")
    if cached and isinstance(cached, dict) and "quota_total" in cached:
        # 将旧的 topics dict 列表还原为 TopicQuota
        topics = [TopicQuota(**t) if isinstance(t, dict) else t for t in cached.pop("topics", [])]
        cached["topics"] = topics
        return DailyPlan(**cached)

    growth_stage = get_config("growth_stage", default="cold_start")
    focus_topics = get_config("focus_topics", default=[]) or ["乃木坂", "AKB", "日向坂"]
    format_rotation = get_config("content_format_rotation", default=["news", "story", "news", "story", "ranking", "news", "news"])
    # 今日目标体裁：按天轮转 content_format_rotation
    day_idx = int(date) % len(format_rotation) if format_rotation else 0
    target_format = format_rotation[day_idx]
    cold_start_quota = get_config("cold_start_quota", default=3)
    cold_start_max_quota = get_config("cold_start_max_quota", default=4)
    daily_quota = get_config("daily_quota", default=2)
    max_daily_quota = get_config("max_daily_quota", default=4)
    default_post_times = get_config("default_post_times", default=["09:30", "12:00", "18:00"])
    exploration_ratio = get_config("exploration_ratio", default=0.2)
    baseline_ratio = get_config("baseline_ratio", default=0.1)

    historic_topics = get_top_topics(n=20)
    plan = DailyPlan(date=date, mode=growth_stage)

    if growth_stage == "cold_start":
        quota = cold_start_quota
        fresh_count = sum(1 for t in historic_topics if _topic_is_fresh(t))
        if fresh_count >= 2:
            quota = min(quota + 1, cold_start_max_quota)
        plan.quota_total = quota

        # 80% focus topics, 20% explore
        focus_quota = max(1, int(quota * 0.8))
        explore_quota = quota - focus_quota

        # 按天轮转 focus_topics，确保所有话题都有机会出现
        day_offset = int(date) % len(focus_topics) if focus_topics else 0
        rotated = focus_topics[day_offset:] + focus_topics[:day_offset]
        for ft in rotated[:focus_quota]:
            plan.topics.append(TopicQuota(topic=ft, quota=1, source="high_perf", target_format=target_format))

        # Explore: pick from trend data
        explore_candidates = [t for t in historic_topics if t["topic"] not in focus_topics
                              and t.get("discard_count", 0) < 3]
        for ec in explore_candidates[:explore_quota]:
            plan.topics.append(TopicQuota(
                topic=ec["topic"], quota=1, source="explore",
                is_fresh=_topic_is_fresh(ec), target_format=target_format))

        # Fallback if not enough
        fallback_topics = focus_topics or ["写真集"]
        while len(plan.topics) < quota:
            fallback = fallback_topics[len(plan.topics) % len(fallback_topics)]
            plan.topics.append(TopicQuota(topic=fallback, quota=1, source="baseline", target_format=target_format))

    else:
        # Standard planning: 70% high_perf + 20% explore + 10% baseline
        quota = daily_quota
        recent_perf = get_recent_performance(7)

        # 配额调整
        if historic_topics and recent_perf["avg_week_saves"] > 0:
            hist_avg = sum(t.get("engagement_score", 0) for t in historic_topics[:5]) / max(len(historic_topics[:5]), 1)
            if recent_perf["avg_week_saves"] > hist_avg * 1.2:
                quota = min(quota + 1, max_daily_quota)
                plan.note += "近期表现好，配额+1\n"
            elif recent_perf["avg_week_saves"] < hist_avg * 0.6:
                quota = max(1, quota - 1)
                plan.note += "近期表现下滑，配额-1\n"

        plan.quota_total = quota
        high_perf_n = max(1, int(quota * 0.7))
        explore_n = max(1, int(quota * exploration_ratio))
        baseline_n = quota - high_perf_n - explore_n

        for t in historic_topics[:high_perf_n]:
            plan.topics.append(TopicQuota(topic=t["topic"], quota=1, source="high_perf"))

        for t in historic_topics[high_perf_n:high_perf_n + explore_n]:
            if t.get("discard_count", 0) < 3:
                plan.topics.append(TopicQuota(topic=t["topic"], quota=1, source="explore"))

        for ft in focus_topics[:baseline_n]:
            plan.topics.append(TopicQuota(topic=ft, quota=1, source="baseline"))

    plan.post_times = recommend_post_times(plan.topics, date)
    set_state(f"daily_plan_{date}", asdict(plan), date=date)
    return plan


def recommend_post_times(topics: list[TopicQuota], date: str) -> list[str]:
    """推荐发布时间"""
    from scripts.sqlite_db import get_config
    defaults = get_config("default_post_times", default=["09:30", "12:00", "18:00"])
    # 简化版：历史 < 50 篇时返回默认时间；TODO: 按历史最优时间分析
    return defaults[:len(topics)] if len(defaults) >= len(topics) else defaults


def _topic_is_fresh(topic: dict) -> bool:
    import json as _json
    try:
        signal = topic.get("trend_signal", "")
        if isinstance(signal, str) and signal:
            s = _json.loads(signal) if signal.startswith("{") else {}
            return s.get("is_fresh", False)
    except Exception:
        pass
    return False


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--date", default="today")
    p.add_argument("--print", action="store_true")
    args = p.parse_args()

    date = datetime.now().strftime("%Y%m%d") if args.date == "today" else args.date
    plan = plan_today(date)
    if getattr(args, "print"):
        from pprint import pprint
        pprint(asdict(plan))
