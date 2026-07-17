# 审查问题处置记录

**日期：** 2026-05-21
**来源：** 智能体设计专家审查 + 小红书运营专家审查

---

## 一、已纳入 OpenSpec 修复的问题

| 问题 | 来源 | 修复位置 |
|---|---|---|
| CDP 单点故障无降级路径 | 架构 4.1 | `risk-control/spec.md`、`feishu-integration/spec.md` |
| 飞书单点 + 自我封闭告警通道 | 架构 6.1 | `feishu-integration/spec.md`（Web UI 兜底） |
| DISCARD 不写回 topic_performance | 架构 7.2 | `memory-layer/spec.md`、`low-score-handler/spec.md` |
| Pearson 相关性无多重检验校正 | 架构 5.2 | `reflection-runner/spec.md`（Bonferroni 校正） |
| topic_performance 每周更新太慢 | 架构 2.1 | `memory-layer/spec.md`（每日增量 + 每周全量） |
| 感知数据健康度无检查 | 架构 1.3 | `memory-layer/spec.md`（data_completeness 字段） |
| 低分诊断分支优先级冲突 | 架构 4.2 | `low-score-handler/spec.md`（显式优先级链） |
| 发布时间统计混淆变量 | 架构 3.3 | `daily-planner/spec.md`（<50篇时用经验值） |
| 飞书审批超时状态不透明 | 架构 6.2 | `feishu-integration/spec.md`（approval_status + T-60提醒） |
| agent_strategy 双写一致性风险 | 架构 2.2 | `memory-layer/spec.md`（SQLite 为单一真相来源） |
| 记忆数据缺乏时间窗口过滤 | 架构 2.3 | `memory-layer/spec.md`（window_days=90 过滤） |
| 70/20/10 比例不可配置 | 架构 3.1 | `daily-planner/spec.md`（exploration_ratio 可配置） |
| 内容同质化风险 | 运营 1.4 | `content-diversity/spec.md`（新 spec） |
| 账号成长阶段适配缺失 | 运营 2.3 | `daily-planner/spec.md`（growth_stage 三模式） |
| 平台风控意识不足 | 运营 2.5 | `risk-control/spec.md`（新 spec） |
| 话题竞品基准缺失 | 架构 1.1 | `memory-layer/spec.md`（topic_baseline_saves 字段） |
| 每周反思频率不足（触发式） | 架构 5.1 | `reflection-runner/spec.md`（触发式反思） |
| 优化目标单一（只优化收藏） | 运营 1.1 | `memory-layer/spec.md`（engagement_score 复合指标）、`daily-planner/spec.md`、`reflection-runner/spec.md` |
| 评论数未进入优化目标 | 运营 1.1 | `memory-layer/spec.md`（avg_comments 字段 + engagement_score）、`reflection-runner/spec.md`（双目标分析） |
| 评论引导性维度缺失 | 运营 1.2 | `dimension-registry/spec.md`（第 20 个维度）|
| 封面图评分缺失（业务关键） | 运营 1.2 | `cover-image-scoring/spec.md`（新 spec，DeepSeek 视觉）|
| 收藏驱动不适配娱乐垂类 | 运营 1.2 | `dimension-registry/spec.md`（按垂类分区权重说明） |
| 原创度定义不适配翻译账号 | 运营 1.2 | `dimension-registry/spec.md`（重新定义 edge_case） |
| 话题热度感知滞后（14:00补扫） | 架构 1.2 | `daily-planner/spec.md`（时效性内容优先早发逻辑） |
| 飞书「一键采纳」权重建议不安全 | 运营 2.4 | `feishu-integration/spec.md`（改为逐条确认） |
| 封面图缺失数据积累 | 运营 1.2 | `feishu-integration/spec.md`（审批卡片展示图片来源） |
| 爆款识别逻辑缺失 | 运营 2.3 | `daily-planner/spec.md`（爆款潜力分标注） |

---

## 二、暂无法修复的问题

### 2.1 ~~封面图多模态评分~~（已纳入本次 change）

**已解决：** DeepSeek 视觉模型支持图片分析，已创建 `cover-image-scoring/spec.md`，使用客观可描述维度（人脸清晰度/构图/色彩/情绪），不尝试评估主观审美或预测流量。详见 spec。

---

### 2.2 外部热点信号源（微博/Bilibili 监控）

**问题（运营 1.5）：** 日本娱乐资讯的热点源头在微博日娱超话、豆瓣日剧条目、Bilibili 日娱区，仅监控小红书内部会错过跨平台热度信号。

**无法修复原因：**
- 微博 API 有速率限制和实名认证要求，爬取存在法律灰色地带
- Bilibili 公开搜索 API 稳定性待验证
- 新增外部数据源需要评估维护成本（外部平台页面结构频繁变化）

**后续计划：**
- **触发条件：** 系统第一个月运营后，评估当前 Yahoo Japan + XHS 扫描是否存在明显的热点遗漏
- **Phase：** 独立 change `xhs-external-signal-integration`
- **降低成本方案：** 先手动监控，运营者在飞书每日报告中通过文字补充外部热点，系统记录在 `agent_strategy` 表，积累规律后再考虑自动化

---

### 2.3 垂类扩展时的数据隔离

**问题（运营 2.2）：** 如果运营者决定从「日本女星/写真」扩展到其他垂类（如美食/旅游），`topic_performance` 中不同垂类的历史数据会相互污染，相关性分析会产生跨垂类混淆。

**无法修复原因：**
- 当前处于单垂类阶段，引入 `vertical` 字段会增加复杂度但无实际数据可分析
- 垂类扩展是未来的运营决策，现在设计可能是过度设计

**后续计划：**
- `memory-layer/spec.md` 中已在 `topic_performance` 表预留 `vertical TEXT` 字段定义
- **触发条件：** 运营者明确决定扩展垂类时，激活该字段并回填历史数据的垂类标签

---

### 2.4 图片版权合规体系

**问题（运营 2.5）：** 日本娱乐图片版权通常归属于经纪公司或摄影师，直接抓取使用可能触发侵权投诉。

**无法修复原因：**
- 这是法律/运营层面的问题，不是技术问题
- 自动化版权检测（如逆向图搜）成本高且误报率高
- 需要运营者制定并执行图片使用政策

**后续计划（非技术）：**
- 建议运营者优先使用：(1) 艺人官方账号发布的宣传图；(2) 新闻稿配图（通常为 PR 授权图）；(3) 公开演唱会/活动的媒体记者拍摄图（通常有转载授权）
- 系统已在 `risk-control/spec.md` 中添加「展示图片来源域名」作为辅助信息

---

### 2.5 账号人设与粉丝关系运营

**问题（运营 1.1）：** 粉丝转化（路人 → 关注者）依赖「持续人设感知」——用户要感知到账号有专属视角、有独特调性。系统全是单篇优化，没有跨篇策略一致性评分，也没有粉丝互动（回复评论、@粉丝）的自动化设计。

**无法修复原因：**
- 「账号调性」和「人设一致性」难以用数字化维度量化，需要运营者主观定义
- 评论回复的自动化（xhs_wander.py 已有基础）需要单独设计安全边界，以免触发风控

**后续计划：**
- **阶段一（当前）：** `content-diversity/spec.md` 的「账号人设一致性约束」（focus_topics 集中度）作为基础保障
- **阶段二（稳定后）：** 独立 change `xhs-community-engagement`，设计评论管理和粉丝互动的安全自动化方案

---

### 2.6 Pearson r 样本量在冷启动期长期不足的问题

**问题（架构 5.2）：** 即使将样本量阈值调整到 40 篇，在每日配额 2 篇的情况下需要 20 周才能触发第一次权重建议。20 周内系统基于初始默认权重运行，可能积累大量方向性偏差。

**无法修复原因：**
- 提高每日配额会带来平台降权风险（运营专家已指出）
- 降低阈值（如 20 篇）会引入统计假阳性（架构专家已指出）
- 这是样本量和统计可靠性之间的内在矛盾

**后续计划：**
- **冷启动期替代方案：** 依靠运营者的人工纠正（`human_override`）而非相关性分析来校准初期权重。运营者在前 20 周每周纠正 2-3 个维度，积累 40-60 条纠正记录，比相关性分析更可靠
- **触发条件：** 进入成长期（`growth_stage=growth`）后，配额提升可加速样本积累，届时相关性分析才有实际意义
