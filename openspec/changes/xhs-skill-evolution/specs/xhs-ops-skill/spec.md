## ADDED Requirements

### Requirement: RedBookOps SKILL 覆盖运营智能体日常操作场景，含完整执行路径
系统 SHALL 新建 `RedBookOps` SKILL，通过 `xhs-operations` MCP 读写数据，为每个 trigger 场景提供明确的执行路径。

**Trigger 场景：**
- 候选文章查询：`看今天的候选|有哪些待发布文章|今日内容计划`
- 反思与分析：`运行一次反思分析|上周表现怎么样|哪个话题效果最好`
- 评分纠正：`纠正这篇文章的评分|原创度判错了|这张封面图评分有误`
- 维度管理：`查看维度历史版本|回滚到上个版本|切换维度版本`
- 内容规划：`话题表现排名|哪些话题在观察期|上周最佳文章`
- 批量操作：`批量发布今日审批通过的文章|标记所有低分文章为跳过`

#### Scenario: 运营者查询今日候选文章
- **WHEN** 用户说「看今天的候选文章」
- **THEN** SKILL 调用 `get_candidate_articles(date="today")`，以 Markdown 表格展示（标题/评分/话题/体裁/推荐发布时间）；若列表为空则提示「今日暂无候选文章，可能原因：(1) 抓取尚未运行 (2) 所有文章因话题无聊被丢弃 (3) 所有文章处于等待图集下载状态（pending_gallery=True）——可在 Web UI 中查看各状态的文章」

#### Scenario: 运营者纠正文本维度评分
- **WHEN** 用户说「帮我把文章 [标题] 的原创度改为 0.5，因为大段直接翻译」
- **THEN** SKILL 先用 `get_article_detail` 确认文章并展示当前原创度评分和理由，询问确认后调用 `override_dim_score(news_key, "原创度", 0.5, "大段直接翻译")`，回复「已纠正，新综合分：X.X」

#### Scenario: 运营者纠正封面图评分
- **WHEN** 用户说「第一张封面图的人脸清晰度判错了，应该是 0.5，因为侧脸」
- **THEN** SKILL 先用 `get_article_detail` 获取 gallery_images[0] 的 image_url，展示当前 face_clarity 评分，确认后调用 `override_cover_score(news_key, image_url, "face_clarity", 0.5, "侧脸，主体不清晰")`

#### Scenario: 运营者触发快速反思分析
- **WHEN** 用户说「运行一次快速反思分析」或「上周数据有问题，帮我分析一下」
- **THEN** SKILL 调用 `run_reflection(mode="quick")`，回复「反思分析已启动（task_id=xxx），完成后会推送飞书通知，通常需要 2-5 分钟」

#### Scenario: 运营者查询上周表现
- **WHEN** 用户说「上周内容表现怎么样」
- **THEN** SKILL 调用 `get_weekly_stats()`，将返回的结构化数据用自然语言摘要（「本周发布 N 篇，最佳文章《X》获 Y 收藏……」）+ Markdown 表格呈现

#### Scenario: 运营者查看话题表现
- **WHEN** 用户说「话题表现排名」或「哪些话题在观察期」
- **THEN** SKILL 调用 `get_topic_performance(limit=10)`，展示表格；对 `discard_count >= 3` 的话题额外标注「⚠️ 观察期」

#### Scenario: MCP 工具返回 error
- **WHEN** 某工具返回 `{"error": true, "code": "NOT_FOUND", "message": "..."}`
- **THEN** SKILL 向用户解释错误原因（「未找到该文章，请确认标题是否正确」），不继续执行后续步骤

#### Scenario: 运营者调整维度权重
- **WHEN** 用户说「把收藏驱动的权重调高到 2.0」或「原创度权重降低一点」
- **THEN** SKILL 先调用 `get_dimension_versions()` 展示当前版本和权重，确认用户意图后调用 `update_dim_weights({"收藏驱动": 2.0})`，回复「权重已更新，下次评分立即生效」

#### Scenario: MCP server 未启动
- **WHEN** `xhs-operations` MCP server 未连接
- **THEN** SKILL 告知「xhs-operations MCP 未连接，请运行：`python mcp/xhs_operations_server.py`」

### Requirement: RedBookOps SKILL 与 RedBookPublish SKILL 的边界划分
两个 SKILL 的职责边界明确：
- **RedBookPublish**：内容发布操作（发图文/发视频/搜索笔记/查看评论/登录检查）
- **RedBookOps**：运营数据查询与分析（候选管理/评分纠正/维度管理/反思/周报）

边界冲突处理：若用户在发布流程中说「先帮我纠正这篇的评分」，SKILL 可切换到评分纠正流程，完成后提示「评分已纠正，可以继续发布」。
