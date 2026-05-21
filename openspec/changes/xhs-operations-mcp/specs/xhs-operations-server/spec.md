## ADDED Requirements

### Requirement: xhs-operations MCP server 提供运营数据工具集
系统 SHALL 实现基于 FastMCP 框架的 `xhs-operations` MCP server，暴露以下工具供 Claude Code 直接调用。

**工具列表：**

| 工具名 | 输入 | 输出 | 说明 |
|---|---|---|---|
| `get_candidate_articles` | `date?`, `status?` | 文章列表 | 返回候选/待审批文章，默认今日 |
| `get_article_detail` | `news_key` | 文章详情+维度评分 | 含 title/content/scores/cover_score |
| `update_article_status` | `news_key`, `status`, `note?` | ok/error | 更新文章状态（publish/skip/archive） |
| `get_dimension_versions` | 无 | 版本列表 | 返回所有评分维度版本历史 |
| `activate_dimension_version` | `version` | ok/error | 切换当前生效版本，清空缓存 |
| `get_topic_performance` | `vertical?`, `limit?`, `window_days?` | 话题列表 | 按 engagement_score 排序 |
| `get_weekly_stats` | `week_start?` | 周报数据 | 发布数/浏览/收藏/评论/各话题对比 |
| `override_dim_score` | `news_key`, `dim_name`, `value`, `note` | ok/error | 人工纠正维度评分，写 human_value + override_note |

#### Scenario: Claude Code 通过工具查询今日候选文章
- **WHEN** 运营者问「今天有哪些候选文章」
- **THEN** Claude Code 调用 `get_candidate_articles(date="today", status="pending")`，返回结构化列表并展示给运营者

#### Scenario: 人工纠正评分并填写理由
- **WHEN** 运营者说「这篇文章原创度判错了，应该是 0.5，因为大段直接翻译」
- **THEN** Claude Code 调用 `override_dim_score(news_key, "原创度", 0.5, "大段直接翻译，原创不足")`，确认写入后回复「已纠正」

### Requirement: MCP server 注册到 Claude Code
系统 SHALL 在 `.claude/settings.json` 中注册 xhs-operations MCP server，使其在 Claude Code 对话中自动可用。

```json
{
  "mcpServers": {
    "xhs-operations": {
      "command": "python",
      "args": ["mcp/xhs_operations_server.py"],
      "cwd": "/path/to/XiaohongshuSkills"
    }
  }
}
```

#### Scenario: Claude Code 启动时自动连接 MCP server
- **WHEN** 用户在项目目录下启动 Claude Code
- **THEN** xhs-operations MCP server 自动启动，工具列表出现在 Claude Code 的工具集中
