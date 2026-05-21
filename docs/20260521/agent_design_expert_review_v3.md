# XHS 智能运营智能体 — 第三轮架构审查报告

**审查日期：** 2026-05-21
**对比基准：** 第二轮审查（整体评分 8.2/10）
**整体评分：** 9.1 / 10（提升 0.9 分）

---

## 一、第二轮所有问题的逐项解决情况

| 问题 | 状态 |
|---|---|
| memory-layer P0/P1 分层错误 | 🟢 已解决 |
| trend_signal 无时序历史 | 🟢 已解决（超额：完整的 topic_trend_history 表） |
| candidate_score 量纲不一致 | 🟢 已解决（保守 w3=0.15 + 设计原则说明） |
| 快速实验模式与发布阈值交互语义 | 🟢 已解决 |
| content-diversity 合并 prompt 脆弱性 | 🟢 已解决（独立 fallback 路径） |
| cron 启动时间未随机化 | 🟢 已解决（0-3 分钟随机 sleep） |
| hashtag 竞争强度数据依赖未对齐 | 🟢 已解决（明确 trend_scanner 写 competition_count） |
| weighted-scoring 与 scoring-pipeline 重叠 | 🟡 文档冗余，不影响实施正确性 |
| reflection-runner 重复定义 topic_performance 更新 | 🟢 已解决（注记明确指向 memory-layer spec） |
| cover-image-scoring 降级为 P1 | 🟡 有意保留 P0（业务判断），需分阶段预案 |
| 触发式反思升级为 P1 | 🟢 已解决 |
| 多垂类 dim_weights 无垂类隔离 | 🟢 已解决（dim_weights_by_vertical，超额实现） |
| 多垂类相似度检测边界 | 🟢 已解决 |
| approval_status timeout 文章去向 | 🟢 已解决（archived_timeout + 恢复路径，超额） |

**13/14 问题已解决，1 个是有意决策（不是遗漏）。**

---

## 二、新发现的问题

### 问题 A — cover-image-scoring 保留 P0，需分阶段预案 🟡 中

**发现：** 第二轮建议降至 P1，但 proposal.md P0 表格仍保留。这是有意识的决策分歧（业务理由成立：DeepSeek 视觉可用，封面影响冷启动分发）。但若 P0 开发资源紧张，没有「最小可行交付」预案。

**建议：** 明确 P0 阶段分两步：第一步只做「评分 + 在 gallery_preview 展示分数」；第二步（P1）才将 cover_score 接入 `candidate_score` 排序公式。这样降低 P0 风险，同时保留业务价值。

---

### 问题 B — topic_performance 增量更新缺去重标记，可能重复或遗漏 🟡 中

**发现：** `memory-layer/spec.md` 规定「文章发布满 7 天（且含 72h 标记）才更新 topic_performance」。但 `agent-runner/spec.md` 没有对应 scenario，也没有数据库字段标记「已触发 7 天更新」。agent_runner 每天运行时可能对所有发布超过 7 天的文章反复触发更新，或完全遗漏这个触发器。

**建议：** `news` 表增加 `topic_perf_updated_at TEXT` 字段，agent_runner 在执行阶段检查 `pub_date <= 今日 - 7天 AND topic_perf_updated_at IS NULL AND xhs_collected_at CONTAINS '72h'` 的文章，触发一次增量更新后写入 `topic_perf_updated_at`，确保每篇文章只触发一次。

---

### 问题 C — cover_score 进入 candidate_score 时使用「哪张图」的分数未定义 🟡 中

**发现：** 一篇文章有多张候选封面图，`cover-image-scoring/spec.md` 写「飞书展示最高 cover_score 的封面缩略图」，但 `candidate_score` 计算公式里的 `cover_score` 用的是最高分还是平均分没有明说。运营者在 gallery_preview 选定的图可能不是最高分的图。

**建议：** spec 补充一句：「`candidate_score` 排序时使用该文章所有候选封面图的最高 `cover_score`；发布后，`cover_image_scores` 中以 `is_selected=1` 标记运营者选定的封面图，供相关性分析使用」。

---

### 问题 F — P1 阶段简化周报（无权重建议）的飞书卡片格式未定义 🟡 中

**发现：** P1 启用「触发式反思 + 周报（不含权重建议）」，每周仍会运行 `reflection_runner`，但样本不足时不推送权重建议卡片。`feishu-integration/spec.md` 中没有定义「有周报但无权重建议」时推送什么格式。

**建议：** 在 feishu-integration 或 reflection-runner spec 中补充「样本不足版周报卡片」：只展示本周发布统计、话题效果对比，无权重建议区域，不显示「逐条确认」按钮。

---

### 问题 D — growth_stage 升级提醒文案指向 JSON，与 SQLite 真相来源冲突 🟢 低

**发现：** `daily-planner/spec.md` 的飞书提醒文案写「请在 agent_strategy.json 中将 growth_stage 改为 growth」，但 memory-layer spec 明确「JSON 仅作初始化导入，运行时以 SQLite 为准」。运营者修改 JSON 后配置不会生效，引发困惑。

**建议：** 提醒文案改为「请在 Web UI 策略设置页面将 growth_stage 改为 growth」。

---

### 问题 E — 14:00 轻量补扫的 cron 触发机制未在任何 spec 中说明 🟢 低

**发现：** `trend-scanner/spec.md` 定义了「每日 14:00 额外触发一次轻量扫描」，但 `agent-runner/spec.md` 只定义了「每日 07:00 被 cron 触发」，14:00 的触发机制（是否独立 cron、命令是什么）没有说明。

**建议：** 补充一句：「14:00 轻量补扫由单独的 cron job 触发（`0 14 * * *`），内部加 0-3 分钟随机延迟，对应命令 `python scripts/xhs_trend_scanner.py --quick`」。

---

## 三、架构完整性评估

### 数据依赖关系（已验证无断链）

```
trend-scanner → 写 topic_performance.trend_signal / competition_count → daily-planner 读
memory-layer CRUD → 所有 spec 共用
evaluate_quality（weighted-scoring + scoring-pipeline）→ low-score-handler 触发
cover-image-scoring → 写 cover_image_scores → daily-planner 用于 candidate_score
feishu webhook → 写 publish_xhs=1 → agent-runner 触发 publish
```

关键接口全部对齐，无断链。

### P0/P1/P2 分层合理性

**一个需要注意的依赖：** `dimension-registry` 放在 P1，但 P0 的 evaluate_quality prompt 构造依赖初始 `scoring_dimensions.json` 文件存在。这个依赖关系在 spec 中有说明（数据库为空时从 JSON 引导初始化），但 proposal.md 的 P0 人工任务清单没有显式列出「scoring_dimensions.json 初始内容需要在 P0 完成」。

**严重程度：** 🟢 低（设计上覆盖了，checklist 可以更显式）

---

## 剩余高/中优先级问题汇总

| # | 问题 | 严重程度 | 改动量 |
|---|---|---|---|
| A | cover-image-scoring P0 需分阶段预案 | 🟡 中 | 极小（明确两步交付） |
| B | topic_performance 增量更新缺去重标记 | 🟡 中 | 小（新增字段 + agent-runner scenario） |
| C | cover_score 候选排序使用哪张图未定义 | 🟡 中 | 极小（spec 补一句话） |
| F | P1 简化周报卡片格式未定义 | 🟡 中 | 小（补一个 scenario） |
| D | growth_stage 升级提醒文案指向 JSON（与 SQLite 设计冲突） | 🟢 低 | 极小（改文案） |
| E | 14:00 补扫 cron 触发机制未说明 | 🟢 低 | 极小（补一句话） |

**零个🔴高优先级问题。**

---

## 一句话结论

**可以开始实施**——核心架构完整、降级路径清晰、数据依赖已对齐。建议在开始实施 cover-image-scoring 和 agent-runner 前，先用 1-2 小时将「问题 B（news 表增加 topic_perf_updated_at 字段）」和「问题 C（cover_score 候选排序使用最高分 + is_selected 标记）」补写入对应 spec，避免工程师实施时产生分歧；其余低优先级问题可在实施过程中边写代码边补充说明。
