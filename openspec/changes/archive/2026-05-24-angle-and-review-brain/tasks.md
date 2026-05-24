## 1. Direction 1 — Topic Angle Guidance

- [x] 1.1 `agent_planner.py`: TopicQuota 加 `angle: str = ""` 字段
- [x] 1.2 `agent_planner_llm.py`: `plan_today_llm()` prompt 要求输出 angle，解析时赋值到 TopicQuota
- [x] 1.3 `agent_runner.py`: Phase 3 构建 `topic_angle_map`，加入 keyword dict；相同 Yahoo keyword 合并
- [x] 1.4 `yahoo_news_auto_sqlite.py`: `fetch_all_articles()` 提取 `kw['angle']`，设置 `news['_angle']`（主循环 + retry 队列）
- [x] 1.5 `yahoo_common.py`: `generate_story_article()` 加 `angle` 参数，注入 prompt
- [x] 1.6 `yahoo_common.py`: `generate_content_and_comment()` 加 `angle` 参数，注入 prompt
- [x] 1.7 `yahoo_common.py`: `_process_story_path()` 加 `angle` 参数，传给 `generate_story_article()`
- [x] 1.8 `yahoo_common.py`: `process_news_item()` 取 `news.get('_angle', '')`，传给两个生成函数

## 2. Direction 2 — Content Review Brain

- [x] 2.1 `agent_planner_llm.py`: 实现 `content_review_brain(date, plan_quota)` 函数
- [x] 2.2 Python 预筛逻辑：每 fetch_by 取最高分 1 篇，补足到 max(plan_quota+3, 8)
- [x] 2.3 LLM prompt 精简格式，max_tokens=1200，system_prompt 明确禁止非 JSON 输出
- [x] 2.4 JSON 解析 + regex 兜底提取
- [x] 2.5 对选中文章 `update_news(key, {"publish_xhs": 1})`，打印 ✅/❌ + reasoning
- [x] 2.6 `agent_runner.py`: 新增 Phase 3.5，只在 `planner_mode=llm` 时运行

## 3. Bug Fixes

- [x] 3.1 `scoring.py` / `agent_planner_llm.py`: 用 `sqlite_db.DB_PATH` 替换硬编码 `data/news.db`
- [x] 3.2 `agent_planner_llm.py`: LLM 规划失败日志加 raw response 便于调试
- [x] 3.3 `yahoo_news_auto_sqlite.py`: retry 路径打印 angle 日志
