## ADDED Requirements

### Requirement: 每日生成内容计划，感知账号成长阶段
系统 SHALL 在 `agent_runner` 感知阶段后生成 `DailyPlan`，包含今日待抓取话题列表、每话题抓取量、**发布配额**（今日目标发布篇数）、内容体裁、推荐发布时间段。规划逻辑根据 `agent_strategy.json` 中的 `growth_stage` 字段调整。

> **配额说明**：「每日配额」专指计划**发布**到 XHS 的篇数，而非抓取篇数。抓取量总是大于发布配额（通常抓取 5-8 篇，经评分筛选后取最优 N 篇推送审批）。

`growth_stage` 三种模式：
- `cold_start`（0-1000粉）：高频发布、集中话题、关闭保守降量逻辑
- `growth`（1000-1万）：质量优先、爆款识别、维持稳定节奏
- `stable`（1万+）：维持权重、多样性放开、增加互动运营意识

#### Scenario: 冷启动期专属规划逻辑
- **WHEN** `growth_stage=cold_start`
- **THEN** 每日配额为 `cold_start_quota`（默认 3 篇，上限 `cold_start_max_quota` 默认 4 篇），`focus_topics` 中核心话题占比强制 ≥ 80%，**关闭**「近7天均值 < 60% 时减量」逻辑（冷启动期数据基线不稳定，数据波动是算法随机性，不应触发减量）

#### Scenario: 冷启动期当日有多个高新鲜度话题时弹性加量
- **WHEN** `growth_stage=cold_start` 且趋势扫描显示当日有 ≥ 2 个 `is_fresh=True` 的热点话题
- **THEN** 今日配额可上调至 `cold_start_max_quota`（默认 4 篇），飞书每日摘要标注「今日热点较多，配额上调至 4 篇」

#### Scenario: 成长期增加爆款潜力识别
- **WHEN** `growth_stage=growth`，且某候选文章 title 维度中「剧情感+冲突感+名人+热点」之和 ≥ 3
- **THEN** 该文章在飞书审批卡片中标注「⭐ 爆款候选，建议优先发布」，并推荐最近可用的时间窗口（而非等待默认发布时间）

#### Scenario: 标准规划（有历史数据，非冷启动）
- **WHEN** `topic_performance` 中有 ≥ 5 个话题的历史数据，`growth_stage != cold_start`
- **THEN** 按可配置比例分配配额（默认 `exploration_ratio=0.2, baseline_ratio=0.1`），高表现话题按近 `window_days` 内 `engagement_score DESC` 排序（复合收藏+评论），同时比较 `topic_baseline_saves` 和 `topic_baseline_comments` 筛选真正优于赛道均值的话题

#### Scenario: 冷启动（历史数据不足）
- **WHEN** `topic_performance` 中有历史数据的话题 < 5 个
- **THEN** 使用 `custom_keywords.json` + `DEFAULT_KEYWORDS` 平均分配配额，标记计划为「冷启动模式」，每周强制向运营者发送「话题方向确认」卡片

#### Scenario: 近期表现差时保守（非冷启动期生效）
- **WHEN** `growth_stage != cold_start` 且近 7 天 `engagement_score` < 历史均值的 60%
- **THEN** 今日总配额下调 1 篇，飞书告警「近期互动表现低迷（收藏+评论），建议关注内容质量」

#### Scenario: 近期表现好时适度加量
- **WHEN** 近 7 天 `engagement_score` > 历史均值的 120%（各 `growth_stage` 均生效）
- **THEN** 今日总配额上调 1 篇（不超过 `max_daily_quota` 上限）

### Requirement: 推荐最优发布时间，时效性内容优先早发
系统 SHALL 基于历史数据推荐发布时间段，对「热点」维度高分的时效性内容优先推荐最近可用时间窗口，而非等待默认时间。

#### Scenario: 时效性内容（热点维度=1）—— 2小时发布 SLA
- **WHEN** 候选文章「热点」维度评分 = 1
- **THEN** 推荐发布时间为「当前时间 + 30 分钟」以内（留出审批时间），飞书卡片标注「⏰ 时效性内容，请在 HH:MM 前发布（2小时窗口）」；若当前时间为 09:00-10:00（日本事件集中发酵时段），优先级进一步提升，超时未审批时额外推送一次催促提醒

#### Scenario: 热点内容超过 2 小时未审批
- **WHEN** 热点文章（热点维度=1）从推送飞书到运营者审批超过 2 小时
- **THEN** 系统推送催促消息「⚠️ 热点内容已超 2 小时未发布，时效性降低，建议评估是否仍值得发布」；`approval_status` 标记为 `hot_expired`，不自动跳过，由运营者最终决定

#### Scenario: 发布时间数据不足时使用经验默认值
- **WHEN** 历史数据（含 `xhs_pub_time` 和 `xhs_views`）< 50 篇
- **THEN** 直接使用 `default_post_times`（默认 `["09:30", "12:00", "18:00"]`），不尝试从稀疏数据中推断最优时间（避免小样本伪精确）

#### Scenario: 有足够历史数据时分析最优时间段
- **WHEN** 历史数据 ≥ 50 篇
- **THEN** 按话题类别分组，每组内按小时统计平均浏览量，推荐 top 2 时间段（控制混淆变量：同话题类别比较）

### Requirement: 每日计划幂等持久化
系统 SHALL 将今日计划写入 `agent_strategy` 表，确保 `agent_runner` 多次运行不重复生成计划。

#### Scenario: 今日计划已存在
- **WHEN** `agent_runner` 重复运行且今日计划已写入
- **THEN** 跳过规划阶段，直接使用已有计划继续执行

### Requirement: growth_stage 升级提醒，防止运营者忘记手动更新
系统 SHALL 在每日感知阶段检查账号粉丝数是否已超过当前 `growth_stage` 的对应阈值，若超过则通过飞书推送升级建议（不自动修改，由运营者确认）。

阶段阈值：
- `cold_start` → `growth`：粉丝数超过 1000
- `growth` → `stable`：粉丝数超过 10000

#### Scenario: 粉丝数超过阶段阈值，推送升级提醒
- **WHEN** `account_snapshots.followers` ≥ 1000 且当前 `growth_stage = cold_start`
- **THEN** 飞书推送「🎉 账号粉丝已达到 {N}，当前 growth_stage 仍为 cold_start，建议切换到 growth 模式（运营策略：降低话题集中度，增加爆款尝试）。请在 **Web UI 策略设置页面** 将 growth_stage 改为 growth」

#### Scenario: 已经处于正确阶段
- **WHEN** `followers` 与 `growth_stage` 匹配（如 1500 粉对应 `growth`）
- **THEN** 不推送任何提示

#### Scenario: 粉丝数未采集时跳过检查
- **WHEN** `account_snapshots.followers` 为 null（粉丝数抓取失败）
- **THEN** 跳过阶段检查，不推送提示，在飞书摘要中注明「粉丝数未获取，跳过成长阶段检查」

### Requirement: 话题丢弃降级与观察期管理
系统 SHALL 对累计丢弃次数过高的话题自动降级到「观察期」，不再分配发布配额。

#### Scenario: 话题进入观察期
- **WHEN** 某话题 `discard_count >= 3` 且 `post_count = 0`（从未成功发布过）
- **THEN** planner 不为该话题分配任何配额；trend_scanner 继续监控该话题的趋势信号；飞书摘要通知运营者「话题 [X] 已进入观察期（连续 3 次丢弃），暂停发布」

#### Scenario: 观察期话题出现新热点，半自动提醒恢复
- **WHEN** trend_scanner 检测到某观察期话题（`discard_count >= 3`）的 `is_fresh=True`（recency_score 高于阈值）
- **THEN** 飞书推送「📢 观察期话题 [{话题名}] 出现新热点信号（recency_score={N}），建议评估是否恢复测试」，附带「一键恢复到探索配额」按钮（回调设置 `discard_count=0`，话题重回探索池）；不自动恢复，由运营者决定

#### Scenario: 话题从观察期手动恢复
- **WHEN** 运营者在 Web UI 手动标记某观察期话题「恢复测试」
- **THEN** `discard_count` 重置为 0，话题重新进入探索配额池，下次规划时可被选中
