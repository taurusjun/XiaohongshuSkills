# XHS 智能运营智能体 — 第二轮架构审查报告

**审查日期：** 2026-05-21
**对比基准：** 第一轮审查（整体评分 6.5/10）
**整体评分：** 8.2 / 10（提升 1.7 分）

---

## 一、第一轮🔴高优先级问题解决情况

### CDP 单点故障 — 🟢 已解决
risk-control/spec.md 定义了 3 次重试（30 秒间隔）+ 降级模式（跳过扫描和发布，继续规划+生成+飞书推送）。降级路径清晰，CDP 失败不再导致完全中断。

### 飞书单点故障 — 🟢 已解决
feishu-integration/spec.md 明确三层降级：① API 失败 → 写入 `pending_approvals_YYYYMMDD` + Web UI 红点徽章；② 凭证未配置 → 打印 WARNING，全量降级为 Web UI 模式；③ Web UI 审批与飞书审批效果等价。Web UI 已成为功能等价的备用通道，不再单点。

### DISCARD 不写回 topic_performance — 🟢 已解决（比建议更完整）
low-score-handler/spec.md 明确：DISCARD 时调用 `upsert_topic_performance(topic, discard=True)` 写 `discard_count += 1`。daily-planner/spec.md 明确：`discard_count >= 3` 且 `post_count = 0` → 话题进入「观察期」，不分配配额；运营者可在 Web UI 手动恢复。

### Pearson 统计可靠性 — 🟢 已解决
reflection-runner/spec.md 明确：样本阈值提升至 40（可配置 `min_sample_threshold`）；Bonferroni 校正（p ≈ 0.003）；权重上限 `max_dim_weight` 默认 3.0；置信度三档标注；新增双目标分析（收藏 + 评论分别计算 Pearson）。

### 感知数据可靠性验证缺失 — 🟢 已解决
memory-layer/spec.md 定义了 `data_completeness` 字段 + 数据健康度检查 requirement：趋势数据 > 48h 未更新触发飞书告警，当日快照缺失则使用默认配额并在摘要中注明。

### topic_performance 每周才更新 — 🟢 已解决
双层更新机制：文章发布后立即触发增量更新（近 7 日移动平均），reflection_runner 每周全量重算作为修正。

---

## 二、第一轮🟡中优先级问题解决情况

| 问题 | 状态 | 说明 |
|---|---|---|
| 热点扫描盲窗 | 🟢 已解决 | 14:00 轻量补扫 + recency_score 变化检测 |
| agent_strategy 双写一致性 | 🟢 已解决 | SQLite 为单一真相来源，JSON 降级为导入格式 |
| 旧数据污染均值 | 🟢 已解决 | window_days=90 时间窗口过滤 |
| 70/20/10 硬编码 | 🟢 已解决 | exploration_ratio/baseline_ratio 可配置 |
| 低分诊断分支冲突 | 🟢 已解决 | 显式优先级链：DISCARD > WAIT_GALLERY > REGENERATE > HUMAN_REVIEW |
| 审批与发布竞态 | 🟢 已解决 | 审批回调立即触发一次 publish 脚本检查 |
| 触发式反思 | 🟢 已解决 | 连续 3 天下滑 > 20% 或收藏数为 0 时触发 --quick |
| 超时审批状态不透明 | 🟢 已解决 | approval_status 五态 + T-60min 提醒 + hot_expired |
| 发布时间统计混淆 | 🟢 已解决 | 历史数据 < 50 篇时使用经验默认值 |
| 规划层对账号目标无感知 | 🟡 合理简化 | growth_stage 三段代替了 current_goal 枚举，等价但更收敛 |

---

## 三、新 Capabilities 的设计合理性评估

### content-diversity — 合理，一处脆弱性

**合理：** 体裁适用性前置判断 + 轮换从适用体裁选取 + 降级不推进索引，逻辑自洽。

**🟡 新发现：合并 prompt 的脆弱性**

体裁适用性判断与翻译合并在一次 LLM 调用中，任何一个输出不完整（截断、格式错误）导致两个功能同时失败，需要全部重试。

**建议：** prompt 需要明确分区，解析时分别 fallback——翻译失败重试，体裁判断失败降级为 `["news"]` 不触发重试。

---

### risk-control — 合理，一处遗漏

**🟢 合理：** 发布时间随机抖动、补发上限、图片来源标注，实用且克制。

**🟢 遗漏（低）：** cron 触发时间本身未随机化。`0 7 * * *` 精确触发是固定模式特征。

**建议：** `agent_runner.py` 内部增加 `time.sleep(random.randint(0, 180))` 启动随机偏移。

---

### cover-image-scoring — 合理，一处结构性问题

**🟢 能力边界说明诚实：** 只定义客观维度，明确不预测点击率，设计克制。

**🟡 新发现：candidate_score 量纲不一致**

`title_score`（维度之和 clamp [0,5]）和 `cover_score`（加减分 clamp [0,5]）满分语义不同。`cover_score` 实际分布区间可能是 [0,3]，而文本分数 [1.5,4]，混合排序时封面分被系统性低估。

**建议：** 计算 `candidate_score` 前对三个分数做 z-score 归一化，或初始 `w3` 设为保守值（0.15 而非 0.3），等数据积累后校准。

---

### hashtag-strategy — 🟢 合理，轻量实用

三层结构与实际运营经验一致，竞争强度过滤逻辑合理。

**🟢 小问题：** 竞争强度数据（7 天新发笔记数）依赖 trend_scanner 额外抓取，但两个 spec 之间未显式对齐这一数据依赖。

**建议：** 在 hashtag-strategy/spec.md 中明确「竞争强度数据由 `trend_scanner` 写入 `topic_performance.competition_count`（新字段）」。

---

### trend-scanner 双信号 — 合理，一处时序问题

**🟢 合理：** 形式趋势与话题趋势拆分清晰，`is_fresh` 客观可计算。

**🟡 新发现：trend_signal 被覆盖，历史信号无法追溯**

每次扫描覆盖 `trend_signal` 字段，planner 无法判断「话题热度在上升还是下降」。14:00 补扫的「recency_score 提升 > 0.3」判断需要基线值，但没有存储机制。

**建议：** 新增 `topic_trend_history` 表（`topic_key + scan_date + recency_score + is_fresh`，保留 30 天），或增加 `prev_recency_score` 字段。

---

### 快速实验模式 — 合理，一处对照组问题

**🟡 新发现：** 如果实验文章恰好是热点内容（`is_fresh=True`），互动高于同期普通文章是热点效应而非权重效应，造成假阳性结论。

**建议：** 对比报告中标注实验文章的 `topic_potential` 分和 `is_fresh` 状态，注明「热点内容对比结果参考价值有限」。

---

### 多垂类支持 — 合理，一处权重隔离遗漏

**🟡 新发现：** `scoring_dimensions.json` 是全局共享的，`dim_weights` 全局被 reflection_runner 更新。多垂类模式下「有用信息」对娱乐类和美食类权重理应不同，但系统无法区分。

**建议：** `agent_strategy` 的 `dim_weights` 增加垂类维度：`dim_weights_by_vertical`（如 `{"idol": {"有用信息": 0.4}, "food": {"有用信息": 1.5}}`），评分时先查垂类专属权重，fallback 到全局权重。

---

## 四、整体复杂度评估

### Specs 职责重叠（3 处）

**重叠 1：🟡 weighted-scoring 与 scoring-pipeline 内容几乎重复**

两个 spec 都描述了「evaluate_quality 加权维度，从 agent_strategy.json 读取 dim_weights，clamp 到 [0,5]，配置缺失降级为等权」，scenario 措辞几乎一致。

**建议：** 合并两个 spec，或明确分工：`scoring-pipeline` 负责「评分流程编排（如何合并 cover_score 和文本 score）」，`weighted-scoring` 负责「维度加权计算的内部实现」。

**重叠 2：🟢 feishu-integration 和 risk-control 中的「飞书失败兜底」重复定义**

建议：risk-control 中加一句「（详见 feishu-integration/spec.md）」，明确以 feishu-integration 为准。

**重叠 3：🟡 reflection-runner 包含了 topic_performance 更新逻辑**

memory-layer 和 reflection-runner 都声明了增量更新的 source of truth，建议删除 reflection-runner 中的「每日增量更新」requirement（或简化为引用 memory-layer spec）。

---

## 五、P0/P1/P2 分层合理性评估

### 🔴 高：memory-layer 放在 P1 是逻辑错误

P0 里的 `agent-runner`、`daily-planner`、`risk-control` 全部需要读写 `topic_performance / account_snapshots / agent_strategy` 表。「建表 + 基础 CRUD」是 P0 必须完成的，与「数据积累」是两件事。

**建议：** memory-layer（建表 + CRUD）必须调整为 P0 必完成项。

### 🟡 中：cover-image-scoring 放在 P0 理由不充分

- P0 阶段运营者本身在人工审批，可以自己判断封面质量
- 需要额外配置 DeepSeek 视觉模型 + 新表 + 候选排序公式调整，工作量相当
- `cover_score` 分布校准问题在 P0 没有历史数据时更难处理

**建议：** 将 `cover-image-scoring` 从 P0 降级到 P1。

### 🟡 中：reflection-runner 中的「触发式反思」部分应提前至 P1

P2 定位意味着前 5 个月遇到账号急剧下滑也没有即时分析能力。

**建议：** 将「触发式反思 + 周报生成（不含权重建议）」升级到 P1；「统计分析 + 权重建议生成」保持 P2。

---

## 六、第二轮新发现的问题

### 新问题 A：🟡 快速实验模式与发布阈值交互语义未定义

实验权重可能让本该 DISCARD 的文章得分提升到发布线以上，绕过低分处理逻辑进入候选池。实验权重与发布阈值的交互未在 spec 中定义。

**建议：** 明确「快速实验模式下，发布阈值是否也使用实验权重重算？」——如否，说明「实验权重只影响候选排序，不影响低分丢弃逻辑」。

### 新问题 B：🟢 多垂类时相似度检测边界未定义

相似度检测是跨垂类还是仅在同一垂类内进行？跨垂类检测（娱乐 vs 美食）几乎不会触发，没有实际意义。

**建议：** 明确相似度检测仅在同一 `vertical` 的已发布内容中进行。

### 新问题 C：🟡 approval_status='timeout' 后的文章去向不明确

`timeout` 的文章状态悬空，可能被次日 planner 误判为待处理。

**建议：** 明确 timeout 文章在次日规划前被标记为 `status='archived'`，飞书摘要中列出「N 篇昨日超时文章已归档」。

---

## 最需要关注的 3 个剩余问题

1. **🔴 memory-layer 的 P0/P1 分层是逻辑错误**：P0 组件依赖 memory-layer 表存在，放 P1 意味着 P0 无法运行。必须在动工前修正。

2. **🟡 trend_signal 无时序历史，planner 无法判断话题热度趋势**：只有「现在热不热」，没有「比昨天热了还是凉了」。14:00 补扫的判断逻辑依赖基线值但无存储机制。应在 P1 前补充。

3. **🟡 快速实验模式下发布阈值与实验权重的交互语义未定义**：实现时会让工程师困惑的设计空白，在 reflection-runner spec 中补充一句话即可澄清。

---

## 一句话总结

这个设计从第一轮「有方向但漏洞明显的草稿」已升级为**一个结构完整、降级路径清晰、统计层可信的可实施设计**，第一轮所有高优先级问题均已解决，剩余问题集中在分层次序的一处逻辑错误和两个实现细节的语义空白，整体上已具备进入实现阶段的条件，但需要在动工前修正 memory-layer 的 P0/P1 分类。
