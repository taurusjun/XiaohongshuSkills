## ADDED Requirements

### Requirement: 每日扫描 XHS 话题热门内容，区分形式趋势与话题趋势
系统 SHALL 对配置中的关键词列表，调用 `search_feeds(sort="最多收藏")`，将扫描结果明确拆分为两类信号，分别存储和使用：

- **形式趋势（format_trend）**：哪种内容结构/体裁在该话题下更受欢迎（图文 vs 视频占比、平均标题长度、常见标题句式、话题标签模式）→ 用于优化内容生成 prompt 和体裁轮换
- **话题趋势（topic_trend）**：该话题下近期有哪些具体事件/人物在发酵（高频名词提取、最新发布时间集中度）→ 用于 planner 判断该话题是否有「正在发酵的新事件」值得追发

两类信号结构写入 `topic_performance.trend_signal`（JSON），格式：
```json
{
  "format_trend": {
    "note_type_dist": {"图文": 7, "视频": 3},
    "avg_title_len": 18,
    "top_title_patterns": ["...主演确定", "...首播"],
    "common_tags": ["#日本女星", "#写真"]
  },
  "topic_trend": {
    "hot_entities": ["田中美奈实", "某写真集名"],
    "recency_score": 0.8,
    "is_fresh": true
  }
}
```

`recency_score`：TOP 10 笔记中发布时间在 48 小时内的比例（0-1），越高说明话题越新鲜。
`is_fresh`：`recency_score > 0.5` 时为 true，planner 用此字段判断是否值得当日追发。

#### Scenario: 扫描单个话题，输出双类型信号
- **WHEN** `scan_topic_trends(["写真集"])` 被调用
- **THEN** 系统返回同时含 `format_trend` 和 `topic_trend` 的结构化结果，写入 `topic_performance.trend_signal`

#### Scenario: planner 使用话题趋势判断追发价值
- **WHEN** planner 在选取「探索话题」时，某话题 `is_fresh=True`
- **THEN** 该话题优先进入探索配额，并在飞书每日摘要中标注「📈 该话题近期有新事件，建议追发」

#### Scenario: planner 使用形式趋势优化生成参数
- **WHEN** 某话题 `format_trend.note_type_dist` 显示视频占比 > 70%
- **THEN** 内容生成时在 prompt 中注入提示：「该话题视频内容更受欢迎，如有视频素材优先配合使用」

#### Scenario: 提取 recommended_keywords
- **WHEN** `search_feeds()` 执行时 XHS 返回搜索联想词
- **THEN** 系统将 `recommended_keywords` 列表保存入 `topic_trend.related_keywords`，供 planner 扩展关注话题

#### Scenario: CDP 未就绪时优雅降级
- **WHEN** Chrome 未启动或 CDP 连接失败
- **THEN** 系统记录告警日志，跳过趋势扫描，planner 使用上次缓存的 `topic_performance` 数据继续运行

### Requirement: 14:00 轻量热点补扫，捕捉当日新兴话题
系统 SHALL 在每日 14:00 额外触发一次轻量扫描（仅扫描 `focus_topics` 核心话题和 `recommended_keywords` 中新出现的词），专注于 `recency_score` 变化，发现当日新兴热点。

**Cron 配置：** 14:00 轻量补扫由独立 cron job 触发，与 07:00 主扫描分开配置：
```
0 14 * * * cd /path/to/project && sleep $((RANDOM % 180)) && python scripts/xhs_trend_scanner.py --quick >> logs/trend_14.log 2>&1
```
`--quick` 模式：不写入 `topic_performance`，只生成 `intraday_hot_signal`，扫描时间 < 5 分钟。

此次扫描**不写入** `topic_performance`（不影响全量排名），仅生成临时热点信号 `intraday_hot_signal` 写入 `agent_strategy` 表（key=`intraday_hot_YYYYMMDD`）。

#### Scenario: 14:00 补扫发现当日新热点
- **WHEN** 14:00 扫描中，某话题的 `recency_score` 比 07:00 扫描时提升 > 0.3
- **THEN** 系统通过飞书推送「📢 今日热点信号：[话题] 正在发酵，可考虑插入今日发布计划」，由运营者决定是否追加一篇

#### Scenario: 运营者确认追发热点，发布时间不受 default_post_times 约束
- **WHEN** 运营者在飞书回复确认追发
- **THEN** 系统将该话题加入当日待抓取队列，触发一次临时抓取+生成流程（不影响原有计划）；追发内容的目标发布时间 = **生成完成时间 + 30 分钟**（留给运营者的审批窗口），**不受 `default_post_times` 约束**，飞书追发确认卡片中明确告知「预计发布时间：约 {HH:MM}，请在 {HH:MM-5min} 前完成审批」

### Requirement: 保存趋势历史快照，支持热度趋势判断
系统 SHALL 将每次扫描的 `recency_score` 写入独立的历史表 `topic_trend_history`，使 planner 能判断「话题热度是在上升还是下降」，而非只有当前快照。

```sql
CREATE TABLE topic_trend_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    topic       TEXT NOT NULL,
    scan_date   TEXT NOT NULL,    -- YYYY-MM-DD
    scan_time   TEXT NOT NULL,    -- 07:00 or 14:00
    recency_score REAL,
    is_fresh    INTEGER DEFAULT 0,
    UNIQUE(topic, scan_date, scan_time)
);
```

保留最近 30 天数据，超期自动清理。

`topic_performance.prev_recency_score`：在 UPSERT `trend_signal` 时，先将旧的 `recency_score` 存入 `prev_recency_score`，供 planner 做同比。

14:00 补扫的「recency_score 提升 > 0.3」判断，使用当日 07:00 的 `recency_score` 作为基线（从 `topic_trend_history` 查询 `scan_date=today, scan_time=07:00`）。

#### Scenario: 07:00 扫描后写入历史快照
- **WHEN** 07:00 全量扫描完成
- **THEN** 每个话题的 `recency_score` 写入 `topic_trend_history`（scan_time='07:00'），同时将旧值存入 `topic_performance.prev_recency_score`

#### Scenario: 14:00 补扫时使用今日 07:00 作为基线
- **WHEN** 14:00 轻量补扫完成
- **THEN** 对比「今日 14:00 recency_score」与「今日 07:00 recency_score（从 history 查询）」，差值 > 0.3 时触发热点告警

### Requirement: 竞品账号情报扫描（P1 功能）
系统 SHALL 在每周反思循环中，对 `agent_strategy.json` 中配置的 `competitor_accounts`（竞品账号 user_id 列表）执行一次内容扫描，记录其近 7 天发布内容的关键指标，写入飞书周报的「竞品动态」板块。

> **P1 说明：** 此功能在系统稳定运营一个月后启用，不属于 P0 必须项。

#### Scenario: 竞品账号周度扫描
- **WHEN** `reflection_runner` 运行，且 `competitor_accounts` 配置不为空
- **THEN** 对每个竞品账号调用 `list_profile_notes(user_id, limit=20)`，统计近 7 天发布数量、最高收藏内容标题、常用标签（通过 `get_feed_detail` 提取）、体裁分布

#### Scenario: 竞品动态写入飞书周报
- **WHEN** 竞品扫描完成
- **THEN** 飞书周报增加「竞品动态」板块：每个竞品账号一行，展示「本周发布 N 篇 | 最高收藏：《标题》(N收藏) | 常用标签：#XX #YY」

### Requirement: 趋势扫描结果持久化
系统 SHALL 将完整扫描结果（含双类型信号）写入 `topic_performance.trend_signal`，并更新 `trend_updated_at`。

#### Scenario: 写入成功
- **WHEN** 扫描完成
- **THEN** UPSERT 更新 `trend_signal` 和 `trend_updated_at`，不影响历史 `avg_saves` 等字段

#### Scenario: 新话题首次扫描
- **WHEN** 该关键词在 `topic_performance` 中不存在
- **THEN** INSERT 新记录，历史统计字段置 NULL，等待实发数据填充
