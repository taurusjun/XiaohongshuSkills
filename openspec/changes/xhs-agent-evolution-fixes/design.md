## Context

系统当前架构：Yahoo抓取 → AI生成 → 评分 → 发布小红书 → metrics_collector回收实发数据。理论上数据应流回 `topic_performance` 并驱动规划层决策，但 `xhs_collected_at` 从未写入使整个反馈路径断裂。`reflection_runner.py` 缺失使权重优化完全手动。`upsert_topic_performance` 覆盖语义使历史均值无效。

## Goals / Non-Goals

**Goals:**
- 打通 metrics → topic_performance → planner 的数据流
- `is_fresh` 信号从感知层正确传递到规划层
- 创建可自动触发的反思层编排器
- 人工审核队列可见可操作
- pytest 覆盖所有修复点，端到端验证

**Non-Goals:**
- 不改变现有 metrics_collector 的数据采集逻辑（只加写入）
- 不引入新的外部依赖
- 不修改 XHS 发布流程

## Decisions

### D1: xhs_collected_at 写入时机与格式

在 `metrics_collector.collect_all()` 中，每次成功匹配并更新文章指标后，根据 `now - xhs_pub_time` 计算窗口标签：
- < 6h → `"(4h)"`
- 6h–36h → `"(24h)"`
- 36h+ → `"(72h)"`

写入格式：`"2026-05-22 14:30 (72h)"`。现有 `_update_topic_performance_for_mature_articles` 的 WHERE 条件 `LIKE '%72h%'` 无需修改即可匹配。

**为何不用独立的时间戳字段**：现有代码和查询都依赖字符串匹配 `'%72h%'`，保持兼容比新增字段成本更低。

### D2: upsert_topic_performance 改为 UPSERT 增量模式

```sql
INSERT INTO topic_performance (topic, avg_saves, avg_comments, engagement_score, post_count, discard_count, ...)
VALUES (?, ?, ?, ?, 1, 0, ...)
ON CONFLICT(topic) DO UPDATE SET
  avg_saves = (avg_saves * post_count + excluded.avg_saves) / (post_count + 1),
  avg_comments = (avg_comments * post_count + excluded.avg_comments) / (post_count + 1),
  engagement_score = (engagement_score * post_count + excluded.engagement_score) / (post_count + 1),
  post_count = post_count + 1,
  discard_count = discard_count,  -- 保留，不覆盖
  updated_at = excluded.updated_at
```

纯 SQL UPSERT，无需应用层 read-modify-write，原子性更好。`discard_count` 通过单独的 `increment_topic_discard()` 管理，不在此处覆盖。

### D3: is_fresh 传播路径

`xhs_trend_scanner.update_trend_signals()` 中，`trend_signal` dict 新增 `is_fresh` 键：
```python
trend_signal = {
    "is_fresh": r.get("is_fresh", False),
    "top_titles": [...],
    "recommended_keywords": [...],
    "format_trend": {...},
}
```
`agent_planner._topic_is_fresh()` 已正确读取此字段，无需修改。

### D4: reflection_runner.py 架构

```
reflection_runner.run()
  ├── load_analysis_data() → DataFrame
  ├── compute_correlations() → {dim: pearson_r}
  ├── generate_weight_suggestions(correlations, current_weights) → {dim: new_weight}
  ├── build_weekly_report_card(report, weight_suggestions) → Feishu card
  ├── send_card(FEISHU_OPERATOR_OPEN_ID, card) → message_id
  └── wait for webhook callback (async, non-blocking)
```

权重建议算法：`new_weight = max(0.1, min(3.0, current_weight * (1 + 0.3 * pearson_r))`，变化幅度上限30%，防止单次大幅偏移。无显著相关性（|r| < 0.1）的维度保持不变。

运营者通过飞书卡片"采纳"按钮触发 webhook → `set_config("dim_weights", suggestions)` → 热重载。

### D5: HUMAN_REVIEW 分支

`process_news_item` 在 `diagnose_low_score` 返回 `Action.HUMAN_REVIEW` 时：
1. 设置 `news["_needs_review"] = True`
2. `insert_news()` 写入，`status` 默认为 `'active'`，但在 `score_dims` 中记录 `action='HUMAN_REVIEW'`
3. 发送飞书异步通知（fire-and-forget，失败不影响主流程）
4. Web UI `/api/news` 新增 `status=needs_review` 筛选参数

### D6: 测试策略

- **单元测试**：使用 `tmp_path` fixture 创建临时 SQLite DB，不依赖 `data/news_dev.db`
- **集成测试**：在 `user@192.168.0.70` 运行，使用 `SQLITE_PATH=data/news_dev.db`
- **端到端测试**：`agent_runner.py --dry-run` 跑完整流程，验证关键 DB 状态

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| metrics_collector 匹配率低时 `xhs_collected_at` 写入量不足 | topic_performance 仍然更新稀疏 | 同时修复 fuzzy 匹配阈值（0.85 → 0.7），扩大匹配覆盖 |
| 滚动均值在 post_count=0 时除零 | crash | INSERT 时 post_count 初始化为 1，ON CONFLICT 后才递增 |
| reflection_runner 在数据不足时生成噪声建议 | 错误权重更新 | 要求最少 30 篇有完整数据的文章才生成建议，否则跳过并通知 |
| 飞书通知在 HUMAN_REVIEW 分支失败阻塞文章处理 | 漏发内容 | fire-and-forget 包装，异常只记录日志不抛出 |
