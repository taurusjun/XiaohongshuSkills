## ADDED Requirements

### Requirement: 每周反思循环，含触发式反思支持
系统 SHALL 提供 `reflection_runner.py`，每周日 23:00 由 cron 定时触发，同时支持命令行手动触发（用于异常响应）。

#### Scenario: 完整周报生成
- **WHEN** `reflection_runner.py` 运行且有 ≥ 1 篇本周发布文章含实发数据
- **THEN** 生成包含以下内容的周报：本周发布篇数、总浏览、总收藏（含各话题对比）、最佳文章标题+收藏数、与上周环比、内容体裁分布统计、话题丢弃统计

#### Scenario: 触发式反思（异常响应，冷启动期不触发）
- **WHEN** `growth_stage != cold_start`，且 `account_snapshots` 连续 3 天数据环比下滑 > 20%，或连续 3 天总收藏数为 0
- **THEN** `agent_runner` 自动调用 `reflection_runner --quick`（只运行维度分析和摘要，不修改权重），飞书告警推送摘要，标注「⚠️ 账号异常，已触发即时反思分析」

> **冷启动期说明：** `growth_stage=cold_start` 时数据基线不稳定，0 收藏是常态而非异常，不触发自动 quick 分析，避免误报。冷启动期异常需运营者手动通过「🚨 上报算法变化」触发。

#### Scenario: P1 阶段简化周报（无权重建议，样本不足时）
- **WHEN** 有效样本 < `min_sample_threshold`（默认 40）
- **THEN** 推送简化周报飞书卡片：只展示本周发布篇数、总浏览、总收藏、各话题效果对比、内容体裁分布；无权重建议区域，无「📝 逐条确认」按钮；卡片末尾标注「样本量 N={X}，达到 {min_sample_threshold} 篇后将开始生成权重建议」

#### Scenario: 本周无数据
- **WHEN** 本周无新发布文章或 `xhs_saves` 全为 0
- **THEN** 推送「本周无有效数据，跳过反思分析」，不生成维度建议

### Requirement: 每周全量重算 topic_performance
系统 SHALL 在 `reflection_runner` 运行时，重新计算所有话题的 `avg_saves / avg_comments / engagement_score`，使用时间窗口过滤（只统计近 `window_days` 天内的**成熟数据**，即 `xhs_collected_at` 包含 `72h` 标记的文章）。

> **注意：** 每日增量更新由 `agent_runner` 在文章发布满 7 天后触发（见 memory-layer spec），不在此 spec 中重复定义。

#### Scenario: 话题指标全量重算
- **WHEN** `reflection_runner` 执行
- **THEN** 对所有话题重算加权均值（近期数据权重 1.5，超过 30 天的数据权重 1.0），同时更新 `discard_count` 统计和 `topic_baseline_saves`

### Requirement: 维度权重调整建议，含统计显著性校正
系统 SHALL 调用 `dimension_analysis.py` 的分析逻辑，对结果进行 Bonferroni 校正，生成每个维度的当前权重 → 建议权重映射，并标注统计置信度。

**双目标分析**：分别对 `xhs_saves`（收藏）和 `xhs_comments`（评论）计算 Pearson 相关性，生成两张维度排名表。权重建议基于 `engagement_score` 复合目标（按 `engagement_weights` 加权后的综合相关性）。周报中对比展示「更影响收藏的维度」和「更影响评论的维度」，辅助运营者根据当前阶段目标做权重调整。

样本量阈值存储在 `agent_strategy.json` 的 `min_sample_threshold` 字段（默认 40）。算法变化期运营者可临时调低至 10，以换取更快响应（置信度降低，报告中标注）。

Bonferroni 校正：p 阈值 = 0.05 / 维度数量（如 19 个维度则 p 阈值 ≈ 0.003）。

维度权重调整规则（在通过 Bonferroni 校正的前提下）：
- r > 0.4 且 p < 0.003 → 建议权重 +0.5（上限为 `max_dim_weight`，默认 3.0）
- r < 0.1 或 p > 0.003 → 建议权重 -0.3（下限 0.1）

#### Scenario: 有效样本充足时生成权重建议，按 dim_version 分组分析
- **WHEN** 有效样本 ≥ 40 篇
- **THEN** 生成 `{dim_name: {current: 1.0, suggested: 1.5, r: 0.61, p: 0.002, bonferroni_passed: true}}` 格式建议；标注整体置信度；**按 `dim_version` 分组分析**，只对同一版本内的数据做相关性计算，避免不同定义版本的维度数据混淆；若某版本样本量不足（< 20 篇）则在报告中注明「v{X} 样本不足（N={Y}），建议积累后再分析」

> **多版本数据隔离：** `score_dims.dim_version` 字段记录了每次评分使用的维度定义版本。当维度定义更新后（如 `face_clarity` 的 edge_case 更新），新旧版本对同一张图的评分标准不同，混合分析会产生统计噪音。必须按版本隔离后分别分析。

#### Scenario: 样本不足时不生成权重建议
- **WHEN** 有效样本 < 40 篇
- **THEN** 周报中说明「样本量不足（N=X，需 40），本周跳过权重建议」，不推送权重建议卡片

#### Scenario: 权重调整建议含置信度标注
- **WHEN** 飞书推送权重建议卡片
- **THEN** 卡片头部标注「本次建议置信度：[低/中/高]，基于 N=X 样本，定义版本 v{version}」，辅助运营者判断是否采纳

#### Scenario: 权重上限保护
- **WHEN** 某维度建议权重超过 `max_dim_weight`（默认 3.0）
- **THEN** 建议权重截断至 3.0，并在报告中注明已达上限

### Requirement: 运营者可手动标注「算法疑似变化」，触发即时分析
系统 SHALL 在飞书周报卡片中提供「🚨 上报算法变化」按钮，运营者发现账号数据与内容质量不符时（如高分文章连续低流量），可手动触发一次即时 `reflection_runner --quick`，生成当前数据摘要供参考。

#### Scenario: 运营者点击「上报算法变化」
- **WHEN** 运营者在飞书周报卡片点击「🚨 上报算法变化」
- **THEN** 系统立即运行 `reflection_runner --quick`（不修改权重），生成「近 14 天维度相关性变化」报告推送飞书，同时在 `agent_strategy` 表记录 `algorithm_change_flag_YYYYMMDD=true`，提醒运营者核查后可临时调低 `min_sample_threshold`

#### Scenario: 上报后建议临时降低阈值
- **WHEN** `algorithm_change_flag` 存在且 `min_sample_threshold` 仍为默认 40
- **THEN** 在即时分析报告末尾提示：「如需更快感知算法变化，可将 min_sample_threshold 临时调至 10-15（精度降低，但响应更快）」

### Requirement: 快速实验模式，临时覆盖单篇权重观察效果
系统 SHALL 支持「快速实验」：运营者在飞书审批卡片中可将某篇文章标记为「实验发布」，临时使用一组不同的维度权重（覆盖当前 `dim_weights`），24 小时后系统自动对比该文章与同期普通文章的互动指标，生成对比报告。

#### Scenario: 运营者标记某篇为实验发布
- **WHEN** 运营者在飞书审批卡片点击「🧪 实验发布」，并在弹出的配置中调整某维度权重（如将「评论引导性」从 1.0 调至 2.0）
- **THEN** 系统用实验权重重新评分（不修改全局配置），将该文章标记 `is_experiment=True, experiment_weights={...}`，正常发布

> **实验权重与发布阈值的交互：** 实验权重**只影响候选排序**（`candidate_score`），**不影响低分处理逻辑**（DISCARD/REGENERATE 等判断仍使用全局 `title_publish_threshold` 和 `content_publish_threshold`）。实验模式下，某篇因全局阈值判定为 DISCARD 的文章不会因实验权重而进入候选池，需要运营者手动在 Web UI 将其标记为「强制候选」后才能进入实验发布流程。

#### Scenario: 24 小时后自动生成对比报告，标注混淆因素
- **WHEN** 实验文章发布满 24 小时
- **THEN** 系统查询同期（±3天内）发布的非实验文章，对比 `xhs_saves / xhs_comments / xhs_views`，推送飞书消息：「实验文章（权重X）vs 普通文章均值：收藏 N vs M，评论 N vs M，差异：±X%」；**如果实验文章的 `is_fresh=True` 或 `topic_potential >= 4`，报告中额外标注「⚠️ 注意：实验文章为热点/高潜力内容，互动表现可能受话题热度影响，而非权重变化所致，参考价值有限」**

#### Scenario: 实验结果不影响全局权重
- **WHEN** 实验文章表现更好
- **THEN** 系统不自动采纳实验权重；运营者可选择「将实验权重应用到全局」（触发正式权重更新流程）

### Requirement: 纠正记录聚合，推动维度定义版本迭代
系统 SHALL 在每周 `reflection_runner` 中，聚合同一维度的 `override_note`，识别高频错误模式，生成 `edge_case` 更新建议，由运营者确认后提交为新版本。

#### Scenario: 同维度纠正次数达到阈值
- **WHEN** 某维度在过去 4 周内 `human_override=1` 的记录 ≥ 3 条
- **THEN** 调用 LiteLLM 分析这批 `override_note`，提炼共同模式，生成建议措辞，在飞书周报中展示

#### Scenario: 运营者确认后提交新版本
- **WHEN** 运营者在飞书点击「采纳定义建议」或 Web UI 点击「发布新版本」
- **THEN** 调用 `commit_dimension_version()`，`version` 语义递增，清空内存缓存

### Requirement: 内容体裁效果分析
系统 SHALL 在周报中包含内容体裁分布统计，展示不同体裁的平均收藏率，辅助运营者判断是否需要调整 `content_format_rotation`。

#### Scenario: 体裁效果对比
- **WHEN** 本周不同体裁各有 ≥ 1 篇发布记录
- **THEN** 周报展示各体裁的平均收藏数和收藏率对比（如：资讯体 3 篇 avg 45 收藏，盘点体 1 篇 avg 120 收藏）
