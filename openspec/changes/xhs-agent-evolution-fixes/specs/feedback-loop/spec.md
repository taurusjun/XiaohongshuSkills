## ADDED Requirements

### FR-FL-1: xhs_collected_at 写入
- `metrics_collector.collect_all()` 每次成功匹配并更新文章指标后，必须写入 `news.xhs_collected_at`
- 格式：`"YYYY-MM-DD HH:MM (Nh)"` 其中 N 为数据窗口（4/24/72）
- 窗口判断：根据 `now - xhs_pub_time` 计算；< 6h → 4h，6h–36h → 24h，36h+ → 72h
- 若同一文章被重复采集，**追加**新窗口（保留历史记录），格式：`"existing | YYYY-MM-DD HH:MM (72h)"`

### FR-FL-2: topic_performance 在72h数据后自动更新
- `_update_topic_performance_for_mature_articles()` 的触发条件 `xhs_collected_at LIKE '%72h%'` 必须可被满足
- 每篇满足条件的文章触发一次 `upsert_topic_performance`，更新对应话题的滚动均值
- 更新后在文章上设置 `topic_perf_updated_at` 防止重复触发

### FR-FL-3: 模糊匹配阈值放宽
- `metrics_collector` 中 fuzzy title 匹配阈值从 0.85 降低至 0.70
- 增加二次匹配：若 title 不匹配，尝试用 `news.key` 的前12位与 XHS note_id 匹配

### Test Cases
- TC-FL-1: 插入一篇 `xhs_pub_time = 3天前` 的文章，运行 metrics mock，验证 `xhs_collected_at LIKE '%72h%'`
- TC-FL-2: 运行两次 metrics，验证 xhs_collected_at 包含两条记录而非覆盖
- TC-FL-3: `xhs_collected_at` 含 `72h` 的文章触发后，`topic_performance.engagement_score > 0`
