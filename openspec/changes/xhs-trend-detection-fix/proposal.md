## Why

`xhs_trend_scanner.py` 承担了智能体感知层的核心职责：每日 07:00 扫描小红书各话题，为规划层提供基准收藏数（`baseline_saves`）和话题新鲜度（`is_fresh`）信号。然而代码调查发现三个独立故障使这两个信号几乎无效：

1. `sort="最多收藏"` 是无效参数，`cdp_publish.search_feeds` 只处理 `"newest"`，其他值直接跳过——实际走综合排序，但这是无意为之，语义不清
2. 趋势扫描的均值通过 `upsert_topic_performance(saves=baseline_saves)` 写入了真实帖子的滚动均值 `avg_saves`，导致规划层的 `engagement_score` 被搜索结果污染而非反映真实发帖表现
3. `is_fresh` 的两处失真：(a) 从综合排序取发布时间，而综合排序不按时间排；(b) XHS 返回相对时间字符串（`"昨天"`/`"3小时前"`），代码只做 ISO 日期匹配，系统性低估 freshness

这些故障直接导致规划层的话题选择和配额弹性调整无法正常工作。

## What Changes

### Fix 1: 双路扫描策略（综合 + 最新）
- 综合排序（`sort="general"`）：保留，用于计算内容质量基线（baseline_saves、内容类型分布）
- 最新排序（`sort="newest"`）：新增，专门用于计算 is_fresh（话题活跃度）
- 废弃无效的 `sort="最多收藏"` 标注，改为语义明确的 `sort="general"`

### Fix 2: 分离 baseline vs 实发数据
- `scan_topic_trends` 调用 `upsert_topic_performance` 时传 `saves=0, comments=0`，只更新 `topic_baseline_saves` / `topic_baseline_comments`
- 不再污染 `avg_saves`（仅由真实文章72h数据写入）
- baseline_saves 改为截尾均值（去掉最高值后计算），避免单篇爆款拉高基准

### Fix 3: is_fresh 修复
- 新增 `_is_recent(pub_time_str, hours=48) -> bool` 辅助函数，正确处理：
  - ISO 日期字符串（已有）
  - 相对时间：`"刚刚"`, `"x分钟前"`, `"x小时前"`, `"昨天"`, `"x天前"`
- is_fresh = 最新排序结果中 >40% 帖子发布时间在 48h 内

### Fix 4: is_fresh 在规划层充分使用
- `recommend_post_times`：is_fresh=True 的话题分配最早时段
- explore 槽内 is_fresh=True 话题排序优先于 is_fresh=False

## Capabilities

### Modified Capabilities
- `agent-perception`: xhs_trend_scanner 双路扫描逻辑、is_fresh 计算正确化、baseline 数据不污染 avg_saves
- `agent-planning`: recommend_post_times 按 is_fresh 排序；explore 槽优先 fresh 话题

## Success Criteria

- `upsert_topic_performance` 在 trend scanner 调用时 `avg_saves` 不变，只有 `topic_baseline_saves` 更新
- `is_fresh=True` 正确识别 `"昨天"` / `"3小时前"` 等相对时间字符串
- 综合排序结果的 is_fresh 和最新排序结果的 is_fresh 可能不同，新逻辑用后者
- is_fresh=True 话题在当日计划中排到最早发布时段
