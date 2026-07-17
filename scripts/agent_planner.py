#!/usr/bin/env python3
"""agent_planner.py — 每日内容计划生成器"""

import sys
import logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import dataclass, field, asdict
from datetime import datetime

logger = logging.getLogger("agent_planner")


@dataclass
class TopicQuota:
    topic: str
    quota: int
    source: str  # "high_perf" | "explore" | "baseline"
    is_fresh: bool = False
    target_format: str = "news"  # 今日目标体裁，来自 content_format_rotation
    angle: str = ""              # Phase 2 LLM 给出的内容切入角度


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
    # 构建 topic → trend 数据的快速查找表（用于 focus topics 读取 is_fresh）
    topic_trend_map = {t["topic"]: t for t in historic_topics}

    plan = DailyPlan(date=date, mode=growth_stage)

    if growth_stage == "cold_start":
        quota = cold_start_quota

        # is_fresh 计算：用今日轮转的 focus_topics 里有 trend 数据的话题
        day_offset_tmp = int(date) % len(focus_topics) if focus_topics else 0
        rotated_tmp = focus_topics[day_offset_tmp:] + focus_topics[:day_offset_tmp]
        fresh_count = sum(
            1 for ft in rotated_tmp
            if _topic_is_fresh(topic_trend_map.get(ft, {}))
        )
        if fresh_count >= 2:
            quota = min(quota + 1, cold_start_max_quota)
        plan.quota_total = quota

        # 80% focus topics, 20% explore
        focus_quota = max(1, int(quota * 0.8))
        explore_quota = quota - focus_quota

        # 热点Top3 + 轮转补充策略
        import json as _json

        def _fresh_count(ft: str) -> int:
            td = topic_trend_map.get(ft, {})
            ts_raw = td.get("trend_signal", "")
            if isinstance(ts_raw, str) and ts_raw:
                try:
                    ts = _json.loads(ts_raw)
                    return int(ts.get("fresh_count_24h") or 0)
                except Exception:
                    pass
            return 0

        def _baseline(ft: str) -> float:
            td = topic_trend_map.get(ft, {})
            return float(td.get("topic_baseline_saves") or 0)

        # 1. 按（真实24h帖数，baseline_saves打平局）降序，取Top3热点话题
        HOT_SLOTS = 3
        sorted_by_heat = sorted(
            focus_topics,
            key=lambda ft: (_fresh_count(ft), _baseline(ft)),
            reverse=True
        )
        hot_topics = sorted_by_heat[:HOT_SLOTS]

        # 2. 轮转补充剩余槽位（排除已选热点）
        day_offset = int(date) % len(focus_topics) if focus_topics else 0
        rotated = focus_topics[day_offset:] + focus_topics[:day_offset]
        rotation_pool = [ft for ft in rotated if ft not in hot_topics]

        # 3. 合并：热点优先 + 轮转补充，截取到 focus_quota
        selected = hot_topics + rotation_pool
        selected = selected[:focus_quota]

        logger.info(f"  热点Top3: {hot_topics}  轮转补充: {rotation_pool[:focus_quota-HOT_SLOTS]}")

        for ft in selected:
            trend_data = topic_trend_map.get(ft, {})
            ft_is_fresh = _topic_is_fresh(trend_data) if trend_data else False
            plan.topics.append(TopicQuota(topic=ft, quota=1, source="high_perf",
                                          is_fresh=ft_is_fresh, target_format=target_format))

        # Explore: 只考虑最近30天有趋势扫描记录的非focus话题，is_fresh=True 优先
        from datetime import datetime as _dt, timedelta as _td
        cutoff = (_dt.now() - _td(days=30)).strftime("%Y-%m-%d")
        explore_candidates = sorted(
            [t for t in historic_topics
             if t["topic"] not in focus_topics
             and t.get("discard_count", 0) < 3
             and t.get("trend_updated_at", "") >= cutoff],  # 必须有近期趋势数据
            key=lambda t: (0 if _topic_is_fresh(t) else 1, -t.get("engagement_score", 0))
        )
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
    """推荐发布时间。is_fresh=True 的话题优先分配最早时段。"""
    from scripts.sqlite_db import get_config
    defaults = get_config("default_post_times", default=["09:30", "12:00", "18:00"])
    n = len(topics)
    slots = (defaults * ((n // len(defaults)) + 1))[:n] if defaults else ["12:00"] * n
    # is_fresh 话题排前面，占最早时段
    order = sorted(range(n), key=lambda i: (0 if topics[i].is_fresh else 1))
    result = [""] * n
    for slot_idx, topic_idx in enumerate(order):
        result[topic_idx] = slots[slot_idx]
    return result


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
