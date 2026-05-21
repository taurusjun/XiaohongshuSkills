## ADDED Requirements

### Requirement: RedBookOps SKILL 覆盖运营智能体日常操作场景
系统 SHALL 新建 `RedBookOps` SKILL，trigger 覆盖运营者与智能体系统交互的所有日常场景，通过 `xhs-operations` MCP 读写数据，提供有数据支撑的结构化回答。

**Trigger 场景：**
- 候选文章查询：`看今天的候选|有哪些待发布文章|今日内容计划`
- 反思与分析：`运行一次反思分析|上周表现怎么样|哪个话题效果最好`
- 评分纠正：`纠正这篇文章的评分|原创度判错了|这张封面图评分有误`
- 维度管理：`调整维度权重|查看维度历史版本|回滚到上个版本`
- 内容规划：`今日内容规划|话题表现排名|观察期话题`

#### Scenario: 运营者查询今日候选文章
- **WHEN** 用户说「看今天的候选文章」
- **THEN** SKILL 调用 `xhs-operations.get_candidate_articles(date="today")`，以表格形式展示候选文章（标题/评分/封面评分/话题/体裁/推荐发布时间）

#### Scenario: 运营者纠正评分
- **WHEN** 用户说「帮我把文章 [标题] 的原创度改为 0.5，因为大段直接翻译」
- **THEN** SKILL 先用 `get_article_detail` 确认文章，展示当前评分，询问用户确认，再调用 `override_dim_score`

#### Scenario: 运营者请求周报
- **WHEN** 用户说「上周内容表现怎么样」
- **THEN** SKILL 调用 `get_weekly_stats`，用自然语言摘要 + 结构化表格呈现数据

#### Scenario: 依赖 xhs-operations MCP
- **WHEN** `xhs-operations` MCP server 未启动
- **THEN** SKILL 告知「xhs-operations MCP 未连接，请确认 MCP server 已启动（`python mcp/xhs_operations_server.py`）」
