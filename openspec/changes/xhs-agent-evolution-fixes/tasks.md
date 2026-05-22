## 1. Gap 3 — upsert_topic_performance 滚动均值修复（sqlite_db.py）

- [x] 1.1 将 `upsert_topic_performance` 的 INSERT OR REPLACE 改为 INSERT ON CONFLICT DO UPDATE，使用滚动均值公式
- [x] 1.2 确保 ON CONFLICT 分支不修改 `discard_count`，保留历史累计值
- [x] 1.3 `engagement_score` 同样使用滚动均值更新（不覆盖）
- [x] 1.4 处理 post_count=0 边界情况，防止除零
- [x] 1.5 编写 `tests/test_gap3_topic_performance.py`：TC-AM-1 到 TC-AM-5

## 2. Gap 4 — is_fresh 传播修复（xhs_trend_scanner.py）

- [x] 2.1 在 `update_trend_signals()` 的 `trend_signal` dict 中加入 `is_fresh` 字段
- [x] 2.2 确认 `agent_planner._topic_is_fresh()` 读取路径无需修改
- [x] 2.3 编写 `tests/test_gap4_is_fresh.py`：TC-AM-3、TC-AM-4

## 3. Gap 1 — xhs_collected_at 写入修复（metrics_collector.py）

- [x] 3.1 在 `collect_all()` 成功匹配文章后，根据时间差计算窗口标签（4h/24h/72h）
- [x] 3.2 写入 `news.xhs_collected_at`：首次写入用 `"YYYY-MM-DD HH:MM (Nh)"`，重复写入追加（`| YYYY-MM-DD HH:MM (Nh)`）
- [x] 3.3 模糊匹配阈值从 0.85 降低到 0.70
- [x] 3.4 编写 `tests/test_gap1_collected_at.py`：TC-FL-1、TC-FL-2、TC-FL-3

## 4. Gap 5 — HUMAN_REVIEW 分支实现（yahoo_common.py + web/app.py）

- [x] 4.1 在 `process_news_item` 中增加 `Action.HUMAN_REVIEW` 分支：设置 `_needs_review`，调用飞书通知
- [x] 4.2 飞书通知封装为 fire-and-forget（try/except + logger.warning）
- [x] 4.3 `web/app.py` `/api/news` 支持 `needs_review=1` 查询参数，查询含 `action='HUMAN_REVIEW'` 的文章
- [x] 4.4 详情页对 `_needs_review` 文章显示"⚠️ 需要人工审核"标签
- [x] 4.5 编写 `tests/test_gap5_human_review.py`：TC-HRQ-1 到 TC-HRQ-4

## 5. Gap 2 — reflection_runner.py 创建（新文件）

- [x] 5.1 创建 `scripts/reflection_runner.py`，实现 `run(dry_run=False, min_samples=30)` 主函数
- [x] 5.2 集成 `dimension_analysis.load_analysis_data()` + `compute_correlations()`
- [x] 5.3 实现权重建议算法：`clip(w * (1 + 0.3 * r), 0.1, 3.0)`，|r| < 0.1 保持不变
- [x] 5.4 集成 `feishu_bot.build_weekly_report_card()` + 发送卡片
- [x] 5.5 实现 `--dry-run` 和 `--min-samples` CLI 参数
- [x] 5.6 输出 JSON 报告到 stdout（包含 correlations、current_weights、suggested_weights）
- [x] 5.7 编写 `tests/test_gap2_reflection_runner.py`：TC-RR-1 到 TC-RR-4

## 6. 集成验证（user@192.168.0.70）

- [x] 6.1 在服务器上运行 `pytest tests/` 确认所有单元测试通过（28/28）
- [ ] 6.2 手动插入一篇 `xhs_pub_time = 4天前` 的测试文章，运行 `metrics_collector`，验证 `xhs_collected_at LIKE '%72h%'`
- [ ] 6.3 验证 `_update_topic_performance_for_mature_articles` 被该文章触发，`topic_performance.engagement_score > 0`
- [ ] 6.4 运行 `python scripts/reflection_runner.py --dry-run`，验证 JSON 输出格式正确
- [ ] 6.5 构造一篇低分边界文章（title_score=2.5, content_score=2.0），验证 `_needs_review=True` 且飞书通知触发

## 7. 端到端测试（user@192.168.0.70）

- [ ] 7.1 运行 `python scripts/agent_runner.py --dry-run`，验证完整主循环无报错
- [ ] 7.2 运行完整 `agent_runner.py`（非 dry-run），验证 Phase 1–5 均正常执行
- [ ] 7.3 检查 DB：`topic_performance` 有非零 `engagement_score`，`account_snapshots` 有今日记录
- [ ] 7.4 检查 `is_fresh` 传播：`topic_performance.trend_signal` JSON 中 `is_fresh` 字段存在
- [ ] 7.5 运行 `python scripts/reflection_runner.py`（非 dry-run），验证飞书卡片发出
