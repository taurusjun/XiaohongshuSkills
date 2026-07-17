## 1. 更新 SKILL.md（RedBookPublish）

- [ ] 1.1 更新 `SKILL.md` 的 trigger 描述，扩宽覆盖：`发布到小红书|发图文|发视频|搜索笔记|查看评论|检查登录`
- [ ] 1.2 在失败处理部分新增 LLM 调用失败场景（API key 失效/quota 耗尽/模型不支持 JSON mode）
- [ ] 1.3 验证：触发 `发图文` 和 `看评论` 两个场景，确认 SKILL 正确识别

## 2. 新建 RedBookOps SKILL

> 依赖：`xhs-operations-mcp` change 完成后才能充分发挥能力（MCP server 未就绪时 SKILL 仍可运行，但数据查询会失败）

- [ ] 2.1 新建 `.claude/skills/xhs-ops` 目录（或文件），创建 RedBookOps SKILL 定义
- [ ] 2.2 实现候选文章查询流程：调用 `xhs-operations.get_candidate_articles`，以 Markdown 表格展示
- [ ] 2.3 实现评分纠正流程：确认文章 → 展示当前评分 → 用户确认 → 调用 `override_dim_score`
- [ ] 2.4 实现周报查询流程：调用 `get_weekly_stats`，用自然语言摘要 + 表格呈现
- [ ] 2.5 实现话题效果查询：调用 `get_topic_performance`，展示排名
- [ ] 2.6 MCP 未连接时的清晰错误提示
- [ ] 2.7 验证：问「上周哪个话题效果最好」，确认 SKILL 调用 MCP 工具并给出有数据支撑的回答
