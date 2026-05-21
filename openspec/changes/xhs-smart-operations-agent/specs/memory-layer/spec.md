## ADDED Requirements

### Requirement: topic_performance 表存储话题历史表现，以复合互动分为核心优化目标
系统 SHALL 维护 `topic_performance` 表，记录每个话题的历史实发表现指标。**优化目标为复合互动分 `engagement_score`，而非单一收藏数**。

> **设计背景**：单纯优化收藏数会系统性偏向「清单/攻略」类内容，而小红书在娱乐垂类的增长信号更依赖评论互动（用户情感共鸣、争议性话题）。不同阶段的权重应可调整：冷启动期评论权重更高（算法扶持新号），成长期收藏权重更高（搜索长尾）。

`engagement_score = saves_weight × avg_saves + comments_weight × avg_comments`

权重存储在 `agent_strategy.json` 的 `engagement_weights`，默认：`{"saves": 0.6, "comments": 0.4}`。

新增/修改字段：
- `avg_saves REAL`：近 window_days 内该话题已发布文章的平均收藏数
- `avg_comments REAL`：近 window_days 内该话题已发布文章的平均评论数（新增）
- `engagement_score REAL`：复合互动分（计算值，每次更新时重算）
- `topic_baseline_saves REAL`：同话题 XHS TOP 10 笔记的平均收藏数（竞品基准）
- `topic_baseline_comments REAL`：同话题 XHS TOP 10 笔记的平均评论数（新增）
- `discard_count INTEGER DEFAULT 0`：该话题累计被 DISCARD 的次数
- `last_discard_reason TEXT`：最近一次丢弃的原因
- `vertical TEXT`：所属垂类标签
- `window_days INTEGER DEFAULT 90`：计算均值时使用的时间窗口

#### Scenario: 文章发布后 7 天数据更新 topic_performance（成熟数据机制，带去重保护）
- **WHEN** 文章发布满 7 天，`xhs_collected_at` 包含 `72h` 标记，且 `news.topic_perf_updated_at IS NULL`
- **THEN** 调用 `upsert_topic_performance(topic, saves_7d, comments_7d)`，用 7 天后的稳定数据更新 `avg_saves` 和 `avg_comments`，重算 `engagement_score`；写入 `news.topic_perf_updated_at = 今日`，防止后续重复触发

`news` 表新增字段：`topic_perf_updated_at TEXT DEFAULT NULL`（由 agent_runner 写入，记录已触发增量更新的时间戳）。

> **设计说明：** T+0（立即）的数据只反映初始分发，24-48 小时破圈期后的数据才更稳定。使用过早的数据会系统性低估在破圈期后才爆发的内容。`engagement_score` 应基于成熟数据，不应基于噪声数据。

#### Scenario: 24h 数据仅用于热点告警，不用于排名更新
- **WHEN** 文章发布满 24 小时，且 `xhs_saves` 24h 数据远超同期其他文章（> 2 倍均值）
- **THEN** 仅触发飞书告警「📈 《标题》24h 收藏 N，表现突出，可考虑追加相关话题发布」；不使用 24h 数据更新 `topic_performance`，等待 7 天稳定数据

#### Scenario: 读取话题排名时按 engagement_score 排序，按垂类隔离
- **WHEN** `get_top_topics(n=10)` 被调用
- **THEN** 只统计近 `window_days`（默认 90 天）内有发布记录的话题，**先按 `vertical` 过滤**（只取 `active_verticals` 配置中已激活垂类的话题），再按 `engagement_score DESC` 排序；超出窗口无新发布的话题自动降级到「探索配额」

不同垂类的 `engagement_score` 不可跨类比较（娱乐资讯和美食攻略的收藏基准完全不同），必须在各自垂类内部排名。

#### Scenario: 新话题无历史数据
- **WHEN** planner 请求一个 `post_count=0` 的话题数据
- **THEN** 返回该记录并标记 `is_new=True`，planner 将其归入「探索配额」

#### Scenario: 话题基准收藏数写入
- **WHEN** `trend_scanner` 扫描某话题并提取 TOP 10 笔记的收藏数
- **THEN** 计算均值写入 `topic_baseline_saves`，planner 比较「自身 avg_saves vs topic_baseline_saves」来判断是否真正优于同话题平均水平

### Requirement: account_snapshots 表存储每日账号状态，含数据健康度标记
系统 SHALL 每日记录一次账号快照，并携带数据完整性标记。粉丝数通过 `get_profile_snapshot(user_id=XHS_MY_USER_ID)` 抓取，优先读 `profile.followers`，为空时解析 `dom_stat_texts` 中的「X.X万」格式。

新增字段：
- `followers INTEGER`：粉丝数（可为 null）
- `data_completeness TEXT`：数据完整性标记（`full` / `partial_no_followers` / `stale_trend`）

#### Scenario: 快照写入
- **WHEN** `agent_runner` 每日感知阶段调用 `take_account_snapshot()`
- **THEN** INSERT 一条记录含 `snapshot_date / followers / week_views / week_saves / week_likes / top_note_key / data_completeness`

#### Scenario: 粉丝数解析降级
- **WHEN** `get_profile_snapshot()` 返回的 `profile.followers` 为 null
- **THEN** 遍历 `dom_stat_texts`，找到含「万」的字符串并转换（如 `"12.3万"` → `123000`），仍为空时存 null，`data_completeness` 置为 `partial_no_followers`

#### Scenario: 读取近 7 天趋势时跳过 null 行
- **WHEN** planner 调用 `get_recent_performance(days=7)`
- **THEN** 跳过 followers 为 null 的行计算环比，返回值携带 `data_completeness` 字段；调用方判断完整性不足时在规划摘要中标注

### Requirement: 规划前执行感知数据健康度检查
系统 SHALL 在 `agent_runner` 规划阶段开始前，验证关键感知数据的时效性，过期数据触发飞书告警并在计划摘要中标注。

#### Scenario: 趋势数据过期
- **WHEN** `topic_performance` 中最近一条 `trend_updated_at` 距今超过 48 小时
- **THEN** 飞书推送告警「⚠️ 趋势数据已 X 天未更新（上次：日期），今日计划基于陈旧数据，建议检查 trend_scanner」，规划继续执行但降级为「使用上次数据」

#### Scenario: 当日账号快照缺失
- **WHEN** `account_snapshots` 中没有今日的记录
- **THEN** 跳过需要账号数据的配额动态调整逻辑，使用默认配额，飞书摘要中注明「账号快照获取失败，今日使用默认配额」

### Requirement: active_verticals 多垂类并行配置
系统 SHALL 支持在 `agent_strategy.json` 中配置 `active_verticals`（激活的垂类列表），各垂类的 `topic_performance` 数据独立统计，互不污染。

`active_verticals` 配置示例：
```json
{
  "active_verticals": [
    {"id": "idol", "label": "日本女星/写真", "quota_share": 0.8, "prompt_template": "idol"},
    {"id": "food",  "label": "日本美食",      "quota_share": 0.2, "prompt_template": "food"}
  ]
}
```

`quota_share`：该垂类在每日总配额中的占比（多垂类时生效，单垂类时为 1.0）。
`prompt_template`：该垂类使用的内容生成 prompt 模板标识，对应 `config/prompts/<template>.txt`。

#### Scenario: 单垂类模式（默认）
- **WHEN** `active_verticals` 只有一个条目
- **THEN** 行为与无垂类配置时完全相同，`topic_performance` 所有话题默认归属该垂类

#### Scenario: 多垂类并行，配额按占比分配
- **WHEN** `active_verticals` 包含多个条目（如 idol 80% + food 20%）
- **THEN** planner 先按 `quota_share` 分配每个垂类的配额（如总配额 3 篇 → idol 2篇 + food 1篇），再在各垂类内部独立排名话题

#### Scenario: 内容生成按垂类加载不同 prompt 模板
- **WHEN** 某文章属于 `food` 垂类
- **THEN** 内容生成时加载 `config/prompts/food.txt` 模板（而非默认的 idol 模板），确保语气和结构适配美食攻略而非娱乐资讯风格

#### Scenario: 跨垂类数据污染防护
- **WHEN** `reflection_runner` 运行维度相关性分析
- **THEN** 仅对同一垂类（`vertical` 字段相同）的文章做 Pearson 相关性计算，不混合不同垂类数据

### Requirement: 维度权重支持垂类专属覆盖
系统 SHALL 在 `agent_strategy.json` 中支持 `dim_weights_by_vertical`（垂类专属权重），评分时优先使用垂类专属权重，垂类未配置的维度 fallback 到全局 `dim_weights`，再 fallback 到默认值 1.0。

```json
{
  "dim_weights": {"收藏驱动": 1.5},
  "dim_weights_by_vertical": {
    "idol": {"有用信息": 0.4, "收藏驱动": 0.5},
    "food": {"有用信息": 1.5, "收藏驱动": 2.0}
  }
}
```

#### Scenario: 娱乐垂类使用垂类专属权重
- **WHEN** 评分一篇 `vertical=idol` 的文章
- **THEN** `有用信息` 权重使用 `0.4`（垂类专属），`收藏驱动` 使用 `0.5`（垂类专属），其他维度使用全局 `dim_weights` 或默认 1.0

#### Scenario: reflection_runner 按垂类分别生成权重建议
- **WHEN** `reflection_runner` 运行维度相关性分析
- **THEN** 对每个激活的垂类分别计算 Pearson 相关性，权重建议也按垂类分别生成并写入 `dim_weights_by_vertical` 的对应条目

### Requirement: agent_strategy 以 SQLite 为单一真相来源
系统 SHALL 以 `agent_strategy` SQLite 表为策略参数的单一真相来源。`config/agent_strategy.json` 仅作为初始化导入格式，运行时不直接读取 JSON 文件。所有参数修改通过 Web UI 或 CLI 写入数据库。

#### Scenario: 读取维度权重
- **WHEN** `evaluate_quality` 调用 `load_dim_weights()`
- **THEN** 从 `agent_strategy` 表读取（内存缓存 1 分钟 TTL），表为空时读 `agent_strategy.json` 作为一次性初始化导入，导入后以数据库为准

#### Scenario: 飞书确认权重调整后写入
- **WHEN** 运营者在飞书卡片点击「采纳建议」
- **THEN** 系统写入 `agent_strategy` 表，清空内存缓存，不写 JSON 文件（JSON 仅在人工导出时生成）
