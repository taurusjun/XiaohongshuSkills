## MODIFIED Requirements

### scan_topic_trends — 双路扫描

**原行为：** 单次搜索，`sort="最多收藏"` 实际无效，走综合排序。

**新行为：**
- Pass 1：`sort="general"`（明确综合排序）→ 计算 baseline_saves（截尾均值）、image_ratio、avg_title_len、recommended_keywords
- Pass 2：`sort="newest"` → 计算 is_fresh（基于 `_is_recent` 解析后的 pub_time）
- 两路结果合并后写入 DB

### _is_recent(text, hours=48) — 新增

- 处理 XHS 所有时间格式：`"刚刚"`, `"x分钟前"`, `"x小时前"`, `"昨天"`, `"x天前"`, ISO 日期字符串
- 返回 bool：该帖是否在 `hours` 小时内发布

### is_fresh 计算规则

- **基于最新排序结果（Pass 2）**，而非综合排序结果
- `is_fresh = (fresh_count / len(newest_feeds)) > 0.40`
- `fresh_count` = 用 `_is_recent(pub_time, hours=48)` 判断为 True 的帖子数

### baseline_saves 计算规则

- 基于综合排序结果（Pass 1）
- **截尾均值**：去掉最高值后计算均值（防单篇爆款拉高基准）
- 写入 `topic_baseline_saves`，**不写入** `avg_saves`

### upsert_topic_performance 调用规范（trend scanner 路径）

- 调用时 `saves=0, comments=0`（不更新 avg_saves/avg_comments 滚动均值）
- 传入 `trend_only=True` 跳过 rolling average 更新
- 只更新：`topic_baseline_saves`, `topic_baseline_comments`, `trend_signal`, `trend_updated_at`

### Test Cases

- TC-P1: `_is_recent("昨天")` → True；`_is_recent("3天前")` → False（hours=48）
- TC-P2: `_is_recent("2小时前")` → True；`_is_recent("50小时前")` → False
- TC-P3: trend scanner 调用后，`avg_saves` 不变，`topic_baseline_saves` 更新
- TC-P4: 最新排序结果中 8/20 帖 ≤48h → is_fresh=True（40%门槛）
- TC-P5: sort验证测试：调用 search_feeds(sort="newest") 后返回结果的前5条 pub_time 均为近期（手动验证）
