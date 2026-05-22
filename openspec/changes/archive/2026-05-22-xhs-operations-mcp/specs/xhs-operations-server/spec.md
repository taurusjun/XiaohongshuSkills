## ADDED Requirements

### Requirement: xhs-operations MCP server 提供运营数据工具集，含完整输出 schema

系统 SHALL 实现基于 FastMCP 框架的 `xhs-operations` MCP server，所有工具定义完整的输入和输出 schema。

**工具列表（含输出 schema）：**

**`get_candidate_articles(date?, status?)`**
```json
// 输出: [ArticleSummary]
{
  "news_key": "str",
  "title": "str",
  "topic": "str",
  "title_score": "float",
  "content_score": "float",
  "cover_score": "float|null",
  "format": "str",
  "pub_time_recommended": "str",
  "status": "str",
  "similarity_warning": "bool"
}
```

**`get_article_detail(news_key)`**
```json
{
  "news_key": "str",
  "title": "str",
  "content": "str",
  "comment": "str",
  "tags": {"precise": [], "vertical": [], "broad": []},
  "scores": {"title_score": "float", "content_score": "float", "cover_score": "float|null"},
  "dim_scores": [{"dim_name": "str", "value": "float", "reason": "str", "human_override": "bool"}],
  "gallery_images": ["str"],
  "xhs_views": "int|null",
  "xhs_saves": "int|null",
  "xhs_comments": "int|null",
  "xhs_collected_at": "str|null"
}
```

**`update_article_status(news_key, status, note?)`**
```json
// 输出: {"ok": true} | {"error": true, "code": "str", "message": "str"}
```

**`get_dimension_versions()`**
```json
// 输出: [{"version": "str", "created_at": "str", "is_active": "bool", "change_note": "str"}]
```

**`activate_dimension_version(version)`**
```json
// 输出: {"ok": true, "version": "str"} | {"error": true, ...}
```

**`get_topic_performance(vertical?, limit?, window_days?)`**
```json
// 输出: [{"topic": "str", "avg_saves": "float", "avg_comments": "float", "engagement_score": "float", "post_count": "int", "is_fresh": "bool", "discard_count": "int"}]
```

**`get_weekly_stats(week_start?)`**
```json
{
  "week": "str",
  "total_posts": "int",
  "total_views": "int",
  "total_saves": "int",
  "total_comments": "int",
  "save_rate": "float",
  "best_article": {"title": "str", "saves": "int"},
  "by_topic": [{"topic": "str", "posts": "int", "avg_saves": "float"}],
  "by_format": [{"format": "str", "posts": "int", "avg_saves": "float"}]
}
```

**`override_dim_score(news_key, dim_name, value, note)`** — 文本维度
```json
// 输出: {"ok": true, "new_title_score": "float", "new_content_score": "float"} | {"error": ...}
```

**`override_cover_score(news_key, image_url, dim_name, value, note)`** — 封面图维度（新增）
```json
// 输出: {"ok": true, "new_cover_score": "float"} | {"error": ...}
```

**`run_reflection(mode?)`** — 新增，支持从 SKILL 触发反思
```json
// 输入: mode = "quick"（只分析，不修改权重）| "full"（完整周报+权重建议），默认 "quick"
// 输出: {"started": true, "task_id": "str"} — 异步执行，结果通过飞书推送
```

**`get_task_status(task_id)`** — 新增，查询异步任务状态
```json
// 输出: {"task_id": "str", "status": "running"|"done"|"failed", "result_summary": "str|null", "started_at": "str", "completed_at": "str|null"}
```
用途：`run_reflection` 启动后，SKILL 可通过此工具轮询状态；若飞书推送失败，也可在对话中直接查询。

**`batch_update_articles(news_keys: [str], status, note?)`** — 新增，批量状态更新
```json
// 输出: {"updated": "int", "failed": [{"news_key": "str", "reason": "str"}]}
```
> **副作用说明：** `batch_update_articles` 只做状态字段更新（`news.status`），**不触发** low-score-handler 的 DISCARD 副作用（如写回 `topic_performance.discard_count`）。要触发完整副作用需通过正常抓取评分流程。

**`update_dim_weights(weights: {dim_name: float})`** — 新增，直接更新维度权重
```json
// 输出: {"ok": true, "updated_dims": ["str"], "new_version_note": "str"}
```
将 `dim_weights` 写入 `agent_strategy` 表并写入新维度版本记录。SKILL 处理「调整维度权重」场景时调用此工具，无需运营者手动编辑 JSON。

所有工具的错误输出统一格式：`{"error": true, "code": "str", "message": "str"}`

常见 error code：`NOT_FOUND`（记录不存在）、`INVALID_VALUE`（参数值不合法）、`DB_ERROR`（数据库操作失败）

#### Scenario: Claude Code 通过工具查询今日候选文章
- **WHEN** 运营者问「今天有哪些候选文章」
- **THEN** Claude Code 调用 `get_candidate_articles(date="today", status="pending")`，用返回的结构化字段渲染 Markdown 表格（标题/评分/话题/体裁/推荐发布时间）

#### Scenario: 人工纠正文本维度评分
- **WHEN** 运营者说「这篇文章原创度判错了，应该是 0.5，因为大段直接翻译」
- **THEN** 调用 `override_dim_score(news_key, "原创度", 0.5, "大段直接翻译，原创不足")`，返回更新后的综合分

#### Scenario: 人工纠正封面图维度评分
- **WHEN** 运营者说「第一张图的人脸清晰度判错了，侧脸应该是 0.5」
- **THEN** 调用 `override_cover_score(news_key, image_url, "face_clarity", 0.5, "侧脸，主体不清晰")`

#### Scenario: 从 SKILL 触发快速反思，可追问进度
- **WHEN** 运营者说「运行一次快速反思分析」
- **THEN** 调用 `run_reflection(mode="quick")`，返回 `task_id`，SKILL 提示「已启动，task_id=xxx，完成后会推送飞书。如需查询进度，说「反思分析完了吗」」

#### Scenario: 运营者追问反思进度
- **WHEN** 运营者说「反思分析完了吗」
- **THEN** SKILL 调用 `get_task_status(task_id)`，返回当前状态；`status=done` 时展示 `result_summary`；`status=failed` 时提示查看日志

#### Scenario: 工具返回 error 时 SKILL 的处理
- **WHEN** `override_dim_score` 返回 `{"error": true, "code": "NOT_FOUND", "message": "文章 key=xxx 不存在"}`
- **THEN** SKILL 告知运营者「未找到该文章，请确认文章 key 是否正确」，不继续执行

### Requirement: MCP server 注册到 Claude Code
系统 SHALL 在 `.claude/settings.json` 中同时注册两个 MCP server。

```json
{
  "mcpServers": {
    "xhs-operations": {
      "command": "python",
      "args": ["mcp/xhs_operations_server.py"],
      "cwd": "${workspaceFolder}"
    },
    "xhs-llm": {
      "command": "python",
      "args": ["mcp/xhs_llm_server.py"],
      "cwd": "${workspaceFolder}"
    }
  }
}
```
