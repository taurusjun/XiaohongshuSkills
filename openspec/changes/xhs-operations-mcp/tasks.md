## 1. xhs-llm MCP server（优先，解决 temperature 和 JSON schema 根本问题）

- [ ] 1.1 新建 `mcp/` 目录，安装 `fastmcp` 依赖（`pip install fastmcp`）
- [ ] 1.2 新建 `mcp/xhs_llm_server.py`，实现 `translate_title` 工具：temperature=0.2，max_tokens=500，response_format=json_object，schema `{title_zh: str}`，2 次 retry
- [ ] 1.3 实现 `evaluate_content` 工具：temperature=0.1，从 `scoring_dimension_versions` 读活跃版本定义构造 prompt（含 definition/example_1/example_0/example_0_5），schema 含所有维度的 `{value: float, reason: str}`
- [ ] 1.4 实现 `generate_content` 工具：temperature=0.7，schema 含 `seo_title/summary/content/comment/tags{precise/vertical/broad}`，精准标签调用前查询 `artist_name_map.json` NER
- [ ] 1.5 实现 `analyze_overrides` 工具：temperature=0.3，先判断 `has_pattern`，有模式时生成 `edge_case` + `evidence`，无模式时直接返回 `{has_pattern: false}`
- [ ] 1.6 实现 `score_cover_image` 工具：Pillow resize 到 512px 最长边 → base64 → DeepSeek 视觉；减分维度 prompt 显式标注方向；temperature=0.1
- [ ] 1.7 修改 `scripts/yahoo_common.py`：`call_litellm` 增加 `temperature` 参数（默认 0.7），`evaluate_quality` 调用时传 `temperature=0.1`，`translate_title` 调用时传 `temperature=0.2`
- [ ] 1.8 验证：分别调用 `translate_title` 和 `evaluate_content`，确认 temperature 生效，返回严格 JSON

## 2. xhs-operations MCP server

- [ ] 2.1 新建 `mcp/xhs_operations_server.py`，实现 `get_candidate_articles(date?, status?)` 工具，查询 SQLite `news` 表返回结构化列表
- [ ] 2.2 实现 `get_article_detail(news_key)` 工具，JOIN `news + score_dims + cover_image_scores`，返回完整文章详情
- [ ] 2.3 实现 `update_article_status(news_key, status, note?)` 工具，更新 `news.status` 并写操作日志
- [ ] 2.4 实现 `get_dimension_versions()` 工具，查询 `scoring_dimension_versions` 表
- [ ] 2.5 实现 `activate_dimension_version(version)` 工具，切换 `is_active`，清空 SQLite 中的版本缓存标记
- [ ] 2.6 实现 `get_topic_performance(vertical?, limit?, window_days?)` 工具
- [ ] 2.7 实现 `get_weekly_stats(week_start?)` 工具，聚合本周发布/浏览/收藏/评论/各话题
- [ ] 2.8 实现 `override_dim_score(news_key, dim_name, value, note)` 工具，写 `human_override=1 / human_value / override_note / llm_value`，重算综合分
- [ ] 2.9 验证：在 Claude Code 对话中调用 `get_candidate_articles`，确认返回今日候选文章列表

## 3. MCP 注册配置

- [ ] 3.1 新建 `.claude/settings.json`，注册两个 MCP server（`xhs-operations` 和 `xhs-llm`），路径使用项目相对路径
- [ ] 3.2 验证：重启 Claude Code，确认两个 MCP server 的工具出现在工具列表中
- [ ] 3.3 端到端测试：通过 Claude Code 问「帮我纠正文章 key=xxx 的原创度为 0.5，理由是大段直接翻译」，确认 `override_dim_score` 被调用且数据写入正确

## 4. generate_content_and_comment JSON schema 迁移

- [ ] 4.1 修改 `yahoo_common.py:generate_content_and_comment`：改用 `response_format={"type": "json_object"}`，在 prompt 里声明精确 JSON schema（含三层标签）
- [ ] 4.2 删除基于 `【字段名】` 分隔的后处理代码（`last_section()` 相关调用）
- [ ] 4.3 同步修改 `translate_title`：改为 structured output `{title_zh: str}`，删除全部后处理代码（20+ 行）
- [ ] 4.4 在 `content-diversity` 的合并 prompt 中，对 `format_suitability` 做 `isinstance(v, list)` 类型检查，字符串时自动 wrap 为列表
- [ ] 4.5 验证：对 10 篇文章运行 generate_content，确认三层标签结构正确，无解析错误
