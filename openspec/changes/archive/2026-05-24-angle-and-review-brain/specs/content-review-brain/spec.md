## ADDED Requirements

- Phase 3.5 在 Phase 3 抓取完成后、Phase 4 通知前执行
- 只在 `planner_mode=llm` 时启用，rule 模式跳过
- 查询今日 `status=active, publish_xhs=0` 的文章，按 title_score 降序取 top 20
- Python 预筛：每个 `fetch_by` 取最高分 1 篇保证话题多样性，补足到 `max(plan_quota+3, 8)` 篇
- LLM 从 ≤8 篇精简候选中选出 `plan_quota` 篇，输出 `{"selected":[...], "reasoning":"..."}`
- 对选中文章执行 `update_news(key, {"publish_xhs": 1})`
- 打印每篇文章的 ✅ 选中 / ❌ 未选中 及 reasoning
- JSON 解析失败时 regex 兜底提取；完全失败时跳过，不影响 Phase 4
- 使用 `sqlite_db.DB_PATH` 而非硬编码 `data/news.db`
