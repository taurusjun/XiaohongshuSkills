## Why

Phase 2 LLM 规划只决定"发哪些话题、各几篇"，内容生成时没有方向指导，导致同一话题每天随机选角度、质量不稳定。Phase 3 生成完 18 篇候选后靠评分机械排序，没有全局视角做组合决策，容易出现话题单一、体裁重复。

## What Changes

- **TopicQuota 新增 `angle` 字段**：Phase 2 LLM 为每个话题输出一句话内容切入角度
- **angle 全链路传递**：从 DailyPlan → keyword dict → `news['_angle']` → 内容生成 prompt
- **`generate_story_article` / `generate_content_and_comment` 注入 angle**：生成时有明确方向
- **Phase 3 keyword 去重**：相同 Yahoo 搜索词合并，避免重复抓取
- **新增 Phase 3.5 Content Review Brain**：抓取完成后一次 LLM 调用，Python 预筛多样性，LLM 从 ≤8 篇中选出最终发布名单，标记 `publish_xhs=1`
- Phase 3.5 只在 `planner_mode=llm` 时启用，规则模式不影响

## Capabilities

### New Capabilities
- `topic-angle-guidance`: Phase 2 LLM 为每话题输出内容角度，贯穿到内容生成 prompt
- `content-review-brain`: Phase 3.5 LLM 选稿，基于话题多样性和质量组合决策

### Modified Capabilities

## Impact

- `scripts/agent_planner.py`: TopicQuota dataclass 加 `angle` 字段
- `scripts/agent_planner_llm.py`: `plan_today_llm()` 输出 angle，新增 `content_review_brain()`
- `scripts/agent_runner.py`: Phase 3 传 angle + keyword 去重，新增 Phase 3.5
- `scripts/yahoo_news_auto_sqlite.py`: `fetch_all_articles()` 提取并传递 angle
- `scripts/yahoo_common.py`: `generate_story_article()` / `generate_content_and_comment()` / `_process_story_path()` 接收 angle 参数
