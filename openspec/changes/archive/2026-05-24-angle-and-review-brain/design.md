## Context

智能体运营管线分 Phase 1-4，Phase 2 LLM 规划已实现，但规划结果只有话题和配额，没有内容方向。Phase 3 内容生成是盲目的，18 篇候选靠评分机械排序，没有组合决策。

## Goals / Non-Goals

**Goals**
- Phase 2 输出 angle，引导每个话题的内容生成方向
- Phase 3.5 做全局选稿，替代纯机械评分排序
- 两个功能均有完整日志可审查

**Non-Goals**
- 不改变 Phase 1 感知逻辑
- 不改变评分系统（title_score / content_score）
- rule 模式不受影响

## Design

### 方向1：angle 传递链

```
plan_today_llm()
  └─ LLM 输出 {topic, quota, angle} per topic
       └─ TopicQuota.angle 存储
            └─ agent_runner Phase 3: keyword dict 加 angle 字段
                 └─ yahoo_news_auto_sqlite: news['_angle'] = kw['angle']
                      └─ generate_story_article(angle=angle)
                      └─ generate_content_and_comment(angle=angle)
```

angle 注入位置：prompt 开头加 `【内容角度】今日重点关注：{angle}`，仅当 angle 非空时生效。

相同 Yahoo keyword 的话题在 Phase 3 合并（取 max quota），避免重复抓取。

### 方向2：Phase 3.5 Content Review Brain

```
Phase 3 抓取完成
  └─ content_review_brain(date, plan_quota)
       └─ 查今日 status=active, publish_xhs=0 的文章（top 20 by title_score）
       └─ Python 预筛：每个 fetch_by 取最高分 1 篇（保证话题多样性）
       └─ 补足到 max(plan_quota+3, 8) 篇
       └─ LLM 从 ≤8 篇中选 plan_quota 篇
       └─ 对选中文章执行 update_news(key, {publish_xhs: 1})
       └─ 打印 ✅/❌ + reasoning
```

**LLM 输入格式**（精简，避免 CoT token 耗尽）：
```
从以下N篇文章中选出最优M篇发布。
1. [fetch_by] 标题 (title=x.xx, content=x.xx, format=news/story)
...
要求：质量优先，story/news 搭配。
直接输出：{"selected":[1,3,5],"reasoning":"一句话"}
```

**失败兜底**：JSON 解析失败时 regex 提取，全部失败时跳过（不影响 Phase 4）。只在 `planner_mode=llm` 时运行。

## Data Model

`TopicQuota` 新增字段：
```python
angle: str = ""  # Phase 2 LLM 给出的内容切入角度，空字符串表示无指导
```
