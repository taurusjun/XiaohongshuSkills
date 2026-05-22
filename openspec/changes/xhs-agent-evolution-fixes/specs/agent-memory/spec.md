## MODIFIED Requirements

### upsert_topic_performance — 滚动均值（替换原覆盖语义）

**原行为**：INSERT OR REPLACE 覆盖整行，avg_saves = 传入值（非均值），discard_count 归零。

**新行为**：
- 新话题：INSERT，post_count=1，avg_saves=传入值，discard_count=0
- 已有话题：UPDATE 使用滚动均值公式，**不修改 discard_count**
- SQL：`ON CONFLICT(topic) DO UPDATE SET avg_saves = (avg_saves * post_count + excluded.avg_saves) / (post_count + 1), post_count = post_count + 1`
- `engagement_score` 同样使用滚动均值更新

### is_fresh 字段传播

**原行为**：`update_trend_signals()` 写入的 `trend_signal` JSON 不含 `is_fresh`。

**新行为**：`trend_signal` JSON 必须包含 `is_fresh: bool` 字段，由 `scan_topic_trends()` 的返回值提供。

### Test Cases
- TC-AM-1: 同一话题更新2次（saves=10, saves=20），验证 avg_saves=15，post_count=2
- TC-AM-2: 更新话题前先 increment_topic_discard 3次，更新后 discard_count 仍为 3
- TC-AM-3: `update_trend_signals` 写入后，`topic_performance.trend_signal` 包含 `is_fresh` 字段
- TC-AM-4: `agent_planner._topic_is_fresh()` 在 `is_fresh=True` 的话题上返回 True
- TC-AM-5: post_count=0 时 upsert 不触发除零错误
