#!/usr/bin/env python3
"""agent_planner_llm.py — LLM 驱动的规划器（可替换规则版或作 Shadow 验证）"""

import json
import logging
from datetime import datetime
from dataclasses import asdict

logger = logging.getLogger("agent_planner_llm")


def plan_today_llm(date: str = "") -> "DailyPlan":
    """LLM 驱动的今日规划，返回 DailyPlan（与规则版 plan_today 接口一致）。"""
    from scripts.agent_planner import DailyPlan, TopicQuota, recommend_post_times
    from scripts.sqlite_db import get_config, get_top_topics, get_recent_performance, get_state, set_state
    from scripts.yahoo_common import call_litellm

    if not date:
        date = datetime.now().strftime("%Y%m%d")

    # 幂等：已有计划直接返回
    cached = get_state(f"daily_plan_{date}")
    if cached and isinstance(cached, dict) and "quota_total" in cached:
        topics = [TopicQuota(**t) if isinstance(t, dict) else t for t in cached.pop("topics", [])]
        cached["topics"] = topics
        return DailyPlan(**cached)

    focus_topics  = get_config("focus_topics", default=[]) or []
    growth_stage  = get_config("growth_stage", default="cold_start")
    quota_min     = get_config("cold_start_quota", default=3)
    quota_max     = get_config("cold_start_max_quota", default=6)
    format_rotation = get_config("content_format_rotation", default=["news","story","news","story","ranking","news","news"])
    day_idx       = int(date) % len(format_rotation) if format_rotation else 0
    target_format = format_rotation[day_idx]

    historic      = get_top_topics(n=30)
    topic_map     = {t["topic"]: t for t in historic}
    recent_perf   = get_recent_performance(7)

    topic_lines = []
    for ft in focus_topics:
        td = topic_map.get(ft, {})
        ts = {}
        raw = td.get("trend_signal", "")
        if isinstance(raw, str) and raw:
            try: ts = json.loads(raw)
            except Exception: pass
        topic_lines.append(
            f"- {ft}: fresh_count={int(ts.get('fresh_count_24h') or 0)}, "
            f"is_fresh={ts.get('is_fresh', False)}, "
            f"baseline_saves={round(float(td.get('topic_baseline_saves') or 0), 1)}, "
            f"avg_saves={round(float(td.get('avg_saves') or 0), 1)}"
        )

    prompt = f"""你是小红书内容运营决策者。根据今日数据制定发布计划。

【账号状态】成长阶段：{growth_stage}，近7天avg_saves={recent_perf.get('avg_week_saves', 0):.1f}
【配额范围】{quota_min}～{quota_max} 篇
【今日话题数据】
{chr(10).join(topic_lines)}

决策要求：优先热度高（fresh_count大）且质量潜力高（baseline_saves大）的话题，冷启动阶段集中资源而非雨露均沾。
每个话题还需给出一句话内容角度（angle）：今天这个话题应该聚焦什么切入点，引导内容生成方向。

输出 JSON：{{"topics":[{{"topic":"话题名","quota":1,"angle":"内容切入角度一句话"}}],"quota_total":5,"reasoning":"策略说明"}}"""

    result = call_litellm(
        prompt,
        system_prompt="你是内容运营决策者。直接输出JSON，不要前置说明。",
        temperature=0.3,
        max_tokens=1000,
        response_format={"type": "json_object"},
    )

    llm_out = {}
    if result:
        try:
            llm_out = json.loads(result)
        except Exception:
            import re as _re
            for m in reversed(list(_re.finditer(r'\{[\s\S]*\}', result))):
                try:
                    c = json.loads(m.group())
                    if c.get("topics"):
                        llm_out = c; break
                except Exception:
                    pass

    if not llm_out.get("topics"):
        logger.warning(f"  LLM 规划失败，回退到规则版 (raw={str(result)[:200]})")
        from scripts.agent_planner import plan_today
        return plan_today(date)

    plan = DailyPlan(date=date, mode=f"{growth_stage}+llm")
    plan.quota_total = llm_out.get("quota_total", quota_min)
    plan.note = llm_out.get("reasoning", "")

    for t in llm_out["topics"]:
        td = topic_map.get(t["topic"], {})
        ts = {}
        raw = td.get("trend_signal", "")
        if isinstance(raw, str) and raw:
            try: ts = json.loads(raw)
            except Exception: pass
        plan.topics.append(TopicQuota(
            topic=t["topic"], quota=t.get("quota", 1),
            source="llm", is_fresh=bool(ts.get("is_fresh")),
            target_format=target_format,
            angle=t.get("angle", ""),
        ))

    plan.post_times = recommend_post_times(plan.topics, date)
    set_state(f"daily_plan_{date}", asdict(plan), date=date)
    logger.info(f"  LLM规划: quota={plan.quota_total}, topics={[t.topic for t in plan.topics]}")
    logger.info(f"  理由: {plan.note[:120]}")
    return plan


def content_review_brain(date: str, plan_quota: int) -> list[str]:
    """Phase 3.5: 看今日全部候选，选出最终发布名单，标记 publish_xhs=1。

    返回被选中的 key 列表。
    """
    import sqlite3
    from scripts.yahoo_common import call_litellm
    from scripts.sqlite_db import update_news, DB_PATH

    db_path = DB_PATH
    today = date[:4] + '-' + date[4:6] + '-' + date[6:8]  # 20260523 → 2026-05-23

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT key, title, title_score, content_score, fetch_by, summary, format_suitability "
        "FROM news WHERE DATE(created_at)=? AND status='active' AND publish_xhs=0 "
        "ORDER BY title_score DESC LIMIT 20",
        (today,)
    ).fetchall()
    conn.close()

    if not rows:
        logger.info("[review] 今日无候选文章，跳过")
        return []

    # ── Step 1: 标题去重（字符重合度 >50% 视为同一事件，保留高分）──────────
    deduped: list[dict] = []
    for r in rows:
        r = dict(r)
        title_a = set(r["title"] or "")
        duplicate = False
        for kept in deduped:
            title_b = set(kept["title"] or "")
            overlap = len(title_a & title_b) / max(len(title_a | title_b), 1)
            if overlap > 0.5:
                duplicate = True
                logger.info(f"[review] 去重: 「{r['title'][:25]}」← 重复于「{kept['title'][:25]}」({overlap:.0%})")
                break
        if not duplicate:
            deduped.append(r)

    # ── Step 2: 预筛——每个话题取最高分 2 篇 ─────────────────────────────
    topic_counts: dict[str, int] = {}
    diverse: list[dict] = []
    PER_TOPIC = 2
    for r in deduped:
        fb = r["fetch_by"] or "other"
        if topic_counts.get(fb, 0) < PER_TOPIC:
            diverse.append(r)
            topic_counts[fb] = topic_counts.get(fb, 0) + 1

    logger.info(f"[review] 原始候选 {len(rows)} 篇 → 去重后 {len(deduped)} 篇 → 预筛后 {len(diverse)} 篇")

    candidates_text = "\n".join(
        f"{i+1}. [{r['fetch_by']}] {r['title']} "
        f"(title={r['title_score']:.2f}, content={r['content_score']:.2f}, "
        f"format={r['format_suitability']})"
        for i, r in enumerate(diverse)
    )
    keys_by_idx = {i+1: r["key"] for i, r in enumerate(diverse)}

    prompt = f"""从以下 {len(diverse)} 篇文章中选出最优 {plan_quota} 篇今日发布。

{candidates_text}

选择标准：质量优先（title_score高），故事体(story)和资讯体(news)搭配，话题覆盖多样。
必须输出 reasoning 字段（一句话说明选稿逻辑）。
直接输出：{{"selected":[1,3,5],"reasoning":"选稿理由一句话"}}"""

    result = call_litellm(
        prompt,
        system_prompt="只输出一个JSON对象，禁止输出任何解释或分析文字。",
        temperature=0.2,
        max_tokens=1200,
        response_format={"type": "json_object"},
    )

    selected_keys = []
    reasoning = ""
    if result:
        out = {}
        try:
            out = json.loads(result)
        except Exception:
            import re as _re
            for m in reversed(list(_re.finditer(r'\{[\s\S]*\}', result))):
                try:
                    c = json.loads(m.group())
                    if "selected" in c:
                        out = c; break
                except Exception:
                    pass
        if not out:
            logger.warning(f"[review] JSON 解析失败 — {result[:200]}")
        else:
            reasoning = out.get("reasoning", "")
            for idx in out.get("selected", [])[:plan_quota]:
                key = keys_by_idx.get(int(idx))
                if key:
                    selected_keys.append(key)

    logger.info(f"[review] 今日候选 {len(rows)} 篇 → 选中 {len(selected_keys)} 篇")
    logger.info(f"[review] 理由: {reasoning}")
    for i, r in enumerate(rows):
        key = dict(r)["key"]
        if key in selected_keys:
            logger.info(f"[review]   ✅ 选中: [{r['fetch_by']}] {r['title'][:35]} (score={r['title_score']:.2f})")
            update_news(key, {"publish_xhs": 1})
        else:
            logger.info(f"[review]   ❌ 未选: [{r['fetch_by']}] {r['title'][:35]} (score={r['title_score']:.2f})")

    return selected_keys


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
        max_tokens=1500,
        response_format={"type": "json_object"},
    )

    llm_plan = {}
    if result:
        # 提取最后一个完整 JSON 对象（防止 LLM 前置思考文字）
        import re as _re
        matches = list(_re.finditer(r'\{[\s\S]*\}', result))
        for m in reversed(matches):
            try:
                candidate = json.loads(m.group())
                if candidate.get("topics"):
                    llm_plan = candidate
                    break
            except Exception:
                continue
        if not llm_plan:
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
