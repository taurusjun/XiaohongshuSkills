## Why

架构审查（2026-05-22）评定智能体系统为 5.5/10，核心诊断为：**系统能运行但无法学习**。五个关键缺口共同导致反馈闭环断裂——实发数据永远无法流回记忆层，反思层缺少自动编排器，记忆层数据被静默覆盖。修复这五个缺口可将系统从静态流水线升级为具备真正自进化能力的智能体。

## What Changes

1. **Gap 1 — xhs_collected_at 写入修复**：`metrics_collector.collect_all()` 新增写入 `news.xhs_collected_at` 时间戳，格式 `"YYYY-MM-DD HH:MM (Nh)"` 标记数据窗口（4h/24h/72h），解锁 `_update_topic_performance_for_mature_articles` 和 `dimension_analysis` 的样本筛选。

2. **Gap 2 — reflection_runner.py 创建**：新建 `scripts/reflection_runner.py`，每周日 23:00 自动执行：加载维度分析 → 计算 Pearson 相关性 → 生成权重建议 → 通过飞书推送周报卡片 → 等待运营者采纳/拒绝权重更新。

3. **Gap 3 — upsert_topic_performance 滚动均值修复**：将 `INSERT OR REPLACE` 改为读取当前值后执行增量更新，`avg_saves = (old_avg * count + new_val) / (count + 1)`，同时保留 `discard_count` 而非归零。

4. **Gap 4 — is_fresh 传播修复**：`update_trend_signals()` 在写入 `trend_signal` JSON 时包含 `is_fresh` 字段，使 `agent_planner._topic_is_fresh()` 可正确读取，冷启动配额弹性逻辑首次实际生效。

5. **Gap 5 — HUMAN_REVIEW 分支实现**：`process_news_item` 增加对 `Action.HUMAN_REVIEW` 的处理：设置 `_needs_review` 标志并写入 DB `status = 'needs_review'`，Web UI 列表新增"待审核"筛选，飞书发送异步通知。

6. **测试套件**：`tests/` 目录新建5个 pytest 测试文件，覆盖每个 Gap 的单元测试 + 集成测试，端到端测试在 `user@192.168.0.70` 运行完整 `agent_runner.py`。

## Capabilities

### New Capabilities
- `feedback-loop`: xhs_collected_at 写入 + topic_performance 滚动均值更新，打通实发数据→记忆层的完整通路
- `reflection-runner`: 每周自动反思编排器，从数据到权重建议的闭环自动化
- `human-review-queue`: 低分边界文章的人工审核队列，飞书通知 + Web UI 浮出

### Modified Capabilities
- `agent-memory`: upsert_topic_performance 改为增量累积（非覆盖），is_fresh 字段传播

### Removed Capabilities
None.

## Success Criteria

- `metrics_collector` 运行后，被匹配文章的 `xhs_collected_at` 非空
- `_update_topic_performance_for_mature_articles` 在有72h数据的文章上正确更新 `topic_performance`
- `agent_planner._topic_is_fresh()` 在新鲜话题上返回 `True`
- `reflection_runner.py` 可独立运行并输出权重建议 JSON
- `pytest tests/` 全部通过
- 端到端：完整 `agent_runner.py` 运行后，DB 中 `topic_performance.engagement_score` 有意义的非零值
