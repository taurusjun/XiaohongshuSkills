## 1. 验证 cdp_publish sort 参数行为（先做，避免基于错误假设实现）

- [x] 1.1 在服务器上测试 search_feeds(sort="newest")，打印前5条的 pub_time，确认是否真的按最新排序
- [x] 1.2 在服务器上测试 search_feeds(sort="general")，确认与默认行为相同
- [x] 1.3 如果 sort="newest" 实际无效，调查 _select_sort_newest() 是否需要先调用才能生效
- [x] 1.4 记录验证结论，决定实现方案
      结论：sort="newest" 有效（30min→1h→2h...），sort="general" = 综合排序。方案可直接实施。

## 2. _is_recent 辅助函数（scripts/xhs_trend_scanner.py）

- [ ] 2.1 实现 `_is_recent(text: str, hours: int = 48) -> bool`，处理所有 XHS 时间字符串格式
- [ ] 2.2 编写 `tests/test_gap_trend_detection.py`：TC-P1, TC-P2

## 3. upsert_topic_performance trend_only 路径（scripts/sqlite_db.py）

- [ ] 3.1 新增 `trend_only: bool = False` 参数；当 True 时跳过 rolling average 更新，只写 baseline 和 trend_signal
- [ ] 3.2 编写测试：TC-P3（trend scanner 调用后 avg_saves 不变）

## 4. scan_topic_trends 双路扫描重构（scripts/xhs_trend_scanner.py）

- [ ] 4.1 Pass 1：综合排序（sort="general"），计算 baseline_saves（截尾均值）、image_ratio、avg_title_len
- [ ] 4.2 Pass 2：最新排序（sort="newest"），计算 is_fresh（用 _is_recent + 40%门槛）
- [ ] 4.3 废弃 sort="最多收藏"，改为明确的 sort="general"
- [ ] 4.4 调用 upsert_topic_performance 时传 saves=0, comments=0, trend_only=True
- [ ] 4.5 编写测试：TC-P4（is_fresh 40%门槛验证）

## 5. agent_planner.py 规划层使用 is_fresh

- [ ] 5.1 修改 recommend_post_times：is_fresh=True 话题优先分配最早时段
- [ ] 5.2 修改 explore 候选排序：is_fresh=True 优先
- [ ] 5.3 编写测试：TC-PL1, TC-PL2

## 6. 集成验证（user@192.168.0.70）

- [ ] 6.1 运行 `pytest tests/test_gap_trend_detection.py -v`
- [ ] 6.2 在服务器上手动运行 scan_topic_trends(["乃木坂"])，验证：
      - baseline_saves > 0
      - is_fresh 基于最新排序正确计算
      - upsert 后 avg_saves 不变，topic_baseline_saves 更新
- [ ] 6.3 运行 agent_runner.py --dry-run，验证今日计划中 is_fresh 话题排到最早时段
