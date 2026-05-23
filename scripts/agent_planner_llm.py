#!/usr/bin/env python3
"""agent_planner_llm.py — LLM 驱动的 Shadow 规划器（与规则版并行，用于验证）"""

import json
import logging
from datetime import datetime
from dataclasses import asdict

logger = logging.getLogger("agent_planner_llm")


def shadow_plan(date: str, rule_plan) -> dict:
    """用 LLM 重新规划今日计划，与规则版对比后写入日志。

    不影响执行——Phase 3 仍用规则版 rule_plan。
    返回对比摘要 dict，供调用方打印/存档。
    """
    from scripts.sqlite_db import get_config, get_top_topics, get_recent_performance, set_state
    from scripts.yahoo_common import call_litellm

    focus_topics = get_config("focus_topics", default=[]) or []
    growth_stage = get_config("growth_stage", default="cold_start")
    quota_min = get_config("cold_start_quota", default=3)
    quota_max = get_config("cold_start_max_quota", default=6)

    historic = get_top_topics(n=30)
    topic_map = {t["topic"]: t for t in historic}
    recent_perf = get_recent_performance(7)

    # ── 组装话题数据段 ───────────────────────────────────────────────
    topic_lines = []
    for ft in focus_topics:
        td = topic_map.get(ft, {})
        ts = {}
        raw = td.get("trend_signal", "")
        if isinstance(raw, str) and raw:
            try:
                ts = json.loads(raw)
            except Exception:
                pass
        fresh_count = int(ts.get("fresh_count_24h") or 0)
        is_fresh = ts.get("is_fresh", False)
        baseline = round(float(td.get("topic_baseline_saves") or 0), 1)
        avg_saves = round(float(td.get("avg_saves") or 0), 1)
        trend_at = td.get("trend_updated_at", "无数据")
        topic_lines.append(
            f"- {ft}: fresh_count={fresh_count}, is_fresh={is_fresh}, "
            f"baseline_saves={baseline}, avg_saves={avg_saves}, "
            f"trend_updated={trend_at}"
        )

    # ── 规则版结果摘要 ───────────────────────────────────────────────
    rule_topics = [asdict(t) if hasattr(t, '__dataclass_fields__') else t
                   for t in (rule_plan.topics if hasattr(rule_plan, 'topics') else [])]
    rule_summary = ", ".join(
        f"{t.get('topic','?')}({'🔥' if t.get('is_fresh') else ''})"
        for t in rule_topics
    )

    prompt = f"""你是一名小红书内容运营决策者。请根据今日感知数据，制定今日内容发布计划。

【账号状态】
成长阶段：{growth_stage}
今日日期：{date}
近7天账号表现：avg_saves={recent_perf.get('avg_week_saves', 0):.1f}

【各话题今日数据】
{chr(10).join(topic_lines)}

【规则算法给出的参考计划】（可以同意、调整或推翻，需说明理由）
话题：{rule_summary}
配额：{rule_plan.quota_total if hasattr(rule_plan, 'quota_total') else '?'} 篇
配额范围：{quota_min}～{quota_max} 篇

【决策要求】
1. 分析每个话题的当前热度和质量潜力
2. 决定今日发布哪些话题、各几篇、总配额多少
3. 明确说明与规则版的差异及理由

严格按如下 JSON 输出：
{{"topics": [{{"topic": "话题名", "quota": 1, "reason": "理由"}}], "quota_total": 5, "reasoning": "整体策略说明，重点说明与规则版的差异"}}"""

    result = call_litellm(
        prompt,
        system_prompt="你是严肃的内容运营决策者。直接输出JSON，不要任何前置说明。",
        temperature=0.3,
        max_tokens=800,
        response_format={"type": "json_object"},
    )

    llm_plan = {}
    if result:
        try:
            llm_plan = json.loads(result)
        except Exception:
            logger.warning(f"  LLM Shadow 规划 JSON 解析失败: {result[:200]}")

    # ── 生成对比日志 ─────────────────────────────────────────────────
    llm_topics = ", ".join(
        f"{t.get('topic','?')}×{t.get('quota',1)}"
        for t in llm_plan.get("topics", [])
    )
    comparison = {
        "date": date,
        "rule_topics": rule_summary,
        "rule_quota": rule_plan.quota_total if hasattr(rule_plan, 'quota_total') else None,
        "llm_topics": llm_topics,
        "llm_quota": llm_plan.get("quota_total"),
        "llm_reasoning": llm_plan.get("reasoning", ""),
        "topic_details": llm_plan.get("topics", []),
    }

    set_state(f"shadow_plan_{date}", comparison, date=date)

    logger.info(f"  [Shadow] 规则版: {rule_summary} (quota={comparison['rule_quota']})")
    logger.info(f"  [Shadow] LLM版:  {llm_topics} (quota={comparison['llm_quota']})")
    logger.info(f"  [Shadow] 理由: {comparison['llm_reasoning'][:100]}")

    return comparison
