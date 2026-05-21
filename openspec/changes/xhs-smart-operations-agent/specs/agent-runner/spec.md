## ADDED Requirements

### Requirement: 每日主循环幂等执行
系统 SHALL 提供 `agent_runner.py` 作为每日 cron 入口，按固定顺序执行感知→规划→执行→通知，任意阶段失败时通过飞书告警并停止，不影响已完成的步骤。

#### Scenario: 完整正常运行
- **WHEN** `python scripts/agent_runner.py` 在 07:00 被 cron 触发
- **THEN** 依次执行：(1) 趋势扫描 (2) 账号快照 (3) 生成今日计划 (4) 按计划抓取+生成+评分 (5) 推送飞书审批卡片，每步成功后记录进度到 `agent_strategy` 表

#### Scenario: 重复运行幂等
- **WHEN** `agent_runner.py` 在同一天被触发多次（如 cron 重叠）
- **THEN** 已完成的步骤（今日计划已生成、已推送飞书卡片）自动跳过，不重复操作

#### Scenario: 某步骤失败
- **WHEN** 趋势扫描失败（CDP 未就绪）
- **THEN** 跳过趋势扫描，使用缓存数据继续规划，飞书推送降级告警，不中断整个流程

#### Scenario: dry-run 模式
- **WHEN** `python scripts/agent_runner.py --dry-run`
- **THEN** 执行所有步骤但不写数据库、不推送飞书、不触发实际发布，仅打印计划内容到 stdout

### Requirement: 发布回调处理
系统 SHALL 在收到飞书 approve 回调后，将文章标记为已批准并写入计划发布时间，由现有 `yahoo_news_publish.py` 在到时执行发布。

#### Scenario: 审批后定时发布
- **WHEN** 运营者在飞书点击「✅ 发布」，回调写入 `publish_xhs=1, publish_time="2026-05-21 12:00"`
- **THEN** 现有 cron 调用 `yahoo_news_publish.py` 在 12:00 前后检测到待发布文章并执行发布

### Requirement: 每日检查发布满 7 天的文章并触发 topic_performance 增量更新
系统 SHALL 在每日执行阶段完成后，检查近期发布且尚未触发过 topic_performance 更新的文章，触发增量更新后打标记，避免重复或遗漏。

> **触发条件：** `news.pub_date <= 今日 - 7 天` AND `news.topic_perf_updated_at IS NULL` AND `news.xhs_collected_at CONTAINS '72h'`（已完成数据回收，数据相对稳定）

`news` 表新增字段：`topic_perf_updated_at TEXT DEFAULT NULL`（记录已触发增量更新的时间戳，防止重复触发）。

#### Scenario: 检测到符合条件的文章，触发增量更新
- **WHEN** agent_runner 每日运行，检测到存在 `pub_date <= 今日-7天` 且 `topic_perf_updated_at IS NULL` 且含 72h 数据的文章
- **THEN** 对这些文章调用 `upsert_topic_performance(topic, saves_7d, comments_7d)`，更新对应话题的 `avg_saves / avg_comments / engagement_score`，写入 `topic_perf_updated_at = 今日`

#### Scenario: 已更新过的文章不重复触发
- **WHEN** 某文章 `topic_perf_updated_at IS NOT NULL`
- **THEN** 跳过该文章，不重复更新

### Requirement: 冷启动期每日摘要包含数据预期上下文
系统 SHALL 在 `growth_stage = cold_start` 时，在飞书每日摘要中附加冷启动期的数据预期说明，避免运营者将正常波动误判为异常。

#### Scenario: 冷启动期飞书摘要附加上下文说明
- **WHEN** `growth_stage = cold_start`，agent_runner 推送每日摘要
- **THEN** 摘要末尾追加：「📌 冷启动期说明（当前 {N} 粉丝）：此阶段 0 收藏、个位数浏览是算法探测期的正常现象，无需担心。数据将在 2-4 周后趋于稳定。本账号至今发布 {X} 篇，累计收藏 {Y}。」
