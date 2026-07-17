## ADDED Requirements

### Requirement: 维度定义存储在数据库中，以版本快照方式管理
系统 SHALL 将所有评分维度的定义存储在 SQLite 的 `scoring_dimension_versions` 表中，以全量快照 + 版本号的方式管理历史变更。`config/scoring_dimensions.json` 仅作为初始化导入和人工编辑的草稿格式，不是运行时真相来源。

```sql
CREATE TABLE scoring_dimension_versions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    version        TEXT NOT NULL,          -- 语义版本号，如 "1.0.0"、"1.2.0"
    dimensions_json TEXT NOT NULL,         -- 全量维度定义的 JSON 数组快照
    created_at     TEXT NOT NULL,
    created_by     TEXT DEFAULT 'system',  -- 'human' | 'feishu' | 'system'
    change_note    TEXT,                   -- 本次改了什么（如「原创度 edge_case 追加直译说明」）
    is_active      INTEGER DEFAULT 0       -- 1 = 当前生效版本，同时只有一行为 1
);
```

每个维度条目（`dimensions_json` 数组中的元素）schema：
```json
{
  "name": "原创度",
  "category": "内容",
  "direction": "plus",
  "default_weight": 1.0,
  "definition": "判断标准文字",
  "example_1": "给 1 的典型样本",
  "example_0_5": "给 0.5 的典型样本（部分符合的边界情况）",
  "example_0": "给 0 的典型样本",
  "edge_case": "边界情况说明"
}
```

字段约束：
- `name`：与 `score_dims.dimension` 字段完全一致，不可随意改名
- `category`：`"标题"` / `"内容"` / `"图片"`
- `direction`：`"plus"`（加分）/ `"minus"`（减分）
- `default_weight`：默认权重，可被 `agent_strategy` 表中的 `dim_weights` 覆盖
- `definition` / `example_1` / `example_0` / `edge_case`：均为必填项

**内容维度「评论引导性」（新增，第 20 个维度）**：

```json
{
  "name": "评论引导性",
  "category": "内容",
  "direction": "plus",
  "default_weight": 1.0,
  "definition": "内容末尾或正文中是否自然引出开放式讨论，让读者产生「我想说点什么」的冲动。核心区别：评论引导是邀请分享观点/经历，而非索要点赞/关注",
  "example_1": "文末以「你最喜欢她哪个时期的造型？」收尾；或正文对比两种截然不同的粉丝观点，天然引发站队讨论",
  "example_0": "文章只是陈述事实，无任何互动邀请；或文末是「喜欢请点赞收藏」（属于主动讨赏，不是评论引导）",
  "edge_case": "含争议性事件本身（如分手/复出/整容疑云）即使无明确提问也给 1，因为争议性内容天然触发评论"
}
```

**封面图维度**（`category: "图片"`，由 `cover-image-scoring` capability 单独评分，存储在 `cover_image_scores` 表，不进入 LLM 文本评分 prompt）：
`face_clarity` / `emotion_visible` / `composition_clean` / `color_contrast` / `promo_feel` / `multi_person_blur`

这些维度通过 DeepSeek 视觉模型独立评估，不混入文本内容的 19/20 维度体系。但它们**同样纳入 `scoring_dimensions.json` 注册表**（category="图片"），享有与文本维度相同的版本管理和定义迭代机制：

- 运营者在 gallery_preview 纠正封面图维度评分时，填写 `override_note`
- reflection_runner 每周聚合图片维度的纠正记录，识别模式，建议 `edge_case` 更新
- 运营者确认后，`scoring_dimensions.json` 新版本中图片维度的 `edge_case` 更新，DeepSeek 下次评分时使用改进后的定义

这样形成了与文本维度对称的闭环：**DeepSeek 打分 → 人工纠正 → 定义版本迭代 → DeepSeek 打分更准确**。

#### Scenario: 系统启动时加载当前生效版本（多进程安全）
- **WHEN** `evaluate_quality` 需要维度定义
- **THEN** 查询 `scoring_dimension_versions WHERE is_active=1`，比较记录的 `created_at` 与本进程内存中缓存的 `cached_version_created_at`，若不同则重新加载；否则使用缓存

> **多进程一致性说明：** Flask、cron job、agent_runner 各自是独立进程，无法共享内存缓存。不使用 TTL（5 分钟 TTL 会导致版本切换后最多有 3 个进程各自等待 5 分钟），改用 DB 版本号时间戳判断：每次调用时从 DB 读取 `is_active=1` 行的 `created_at`，与内存缓存的时间戳比较，不一致时重新加载。这样版本切换后所有进程在下次调用时即刻感知，延迟仅为一次 SQLite 读（毫秒级）。

#### Scenario: 数据库无记录时从文件引导初始化
- **WHEN** `scoring_dimension_versions` 表为空
- **THEN** 系统读取 `config/scoring_dimensions.json`，以版本号 `"1.0.0"` 写入数据库并设为 `is_active=1`，后续以数据库为准

#### Scenario: 回滚到历史版本
- **WHEN** 运营者在 Web UI 选择某个历史版本并点击「设为当前版本」
- **THEN** 旧 `is_active=1` 行置 0，目标历史行置 `is_active=1`，清空内存缓存，后续 LLM 调用使用回滚后的定义

### Requirement: prompt 从当前生效版本动态构造
系统 SHALL 根据当前生效版本中每个维度的 `definition` / `example_1` / `example_0` / `edge_case` 动态生成 LLM prompt，替代原来的裸名字列表。

prompt 中每个维度的格式：
```
【维度名】判断标准：{definition}
  ✅ 给1示例：{example_1}
  ❌ 给0示例：{example_0}
  ⚠️ 边界说明：{edge_case}
```

#### Scenario: 有完整定义时注入结构化描述
- **WHEN** 当前生效版本中维度含完整四个字段
- **THEN** prompt 中该维度展示完整四段结构（标准 + 正例 + 负例 + 边界）

#### Scenario: 定义缺失时降级为裸名（向下兼容）
- **WHEN** 维度条目缺少 `definition` 字段
- **THEN** prompt 中该维度仅展示名称，行为与旧版相同，不影响其他维度

#### Scenario: LLM 输出格式支持 0 / 0.5 / 1 三档
- **WHEN** 使用新 prompt 调用 LLM
- **THEN** LLM 返回 `{"维度名": {"value": 0 或 0.5 或 1, "reason": "理由说明"}}`；0.5 表示「部分符合」，下游加权计算直接使用该浮点值

### Requirement: 运营者可人工纠正维度评分，纠正值与 LLM 判断独立存储
系统 SHALL 在 Web UI 文章详情页允许运营者对每个维度进行纠正（0 / 0.5 / 1），并填写纠正理由。纠正后综合分数实时重算。纠正值与 LLM 原始判断**并列保存**，互不覆盖。

> **设计背景**：LLM 评分基于 prompt 定义，但可能存在理解偏差（如「大段直接翻译，原创不足」本应给 0.5，LLM 给了 1）。人工纠正有两个独立价值：
> 1. **即时修正**：该文章的综合分数立刻反映纠正后的判断
> 2. **定义改进素材**：纠正记录汇集后，识别高频错误模式，推动定义版本迭代

`score_dims` 表新增字段：
- `human_override INTEGER DEFAULT 0`：是否被人工纠正
- `human_value REAL`：人工纠正值（0 / 0.5 / 1），NULL 表示未纠正
- `override_note TEXT`：运营者填写的纠正理由
- `llm_value REAL`：LLM 原始 value 的备份（纠正时将原 value 移入此字段）
- `dim_version TEXT`：打分时使用的维度定义版本号（便于追溯）

#### Scenario: 运营者设置纠正值
- **WHEN** 用户在 Web UI 将「原创度」从 1 改为 0.5，填写理由「大段直接翻译，原创不足」
- **THEN** `score_dims` 写入：`human_override=1, human_value=0.5, override_note="大段直接翻译，原创不足", llm_value=1`；综合分用 `human_value` 替换该维度贡献后重算，写回 `news` 表

#### Scenario: Web UI 区分 LLM 判断与人工纠正
- **WHEN** 维度 `human_override=1`
- **THEN** 评分面板展示双行：上行为 LLM 原始值 + reason（灰色），下行为人工值 + override_note（金色边框），标注「基于定义 v{dim_version}」

#### Scenario: 相关性分析优先使用人工纠正值
- **WHEN** `dimension_analysis.py` 计算 Pearson 相关性
- **THEN** `human_override=1` 的记录使用 `human_value`；否则使用原始 `value`

### Requirement: 纠正记录聚合推动维度定义版本迭代
系统 SHALL 在每周 `reflection_runner` 中，聚合同一维度的 `override_note`，识别高频错误模式，生成新版本维度定义建议，由运营者确认后提交为新版本入库。

> **核心选择**：不采用 few-shot 注入（token 成本随样本累积）。纠正沉淀为定义版本更新，一次修改永久有效，且历史版本完整保留可回滚。

#### Scenario: 同维度纠正次数达到阈值，触发定义建议
- **WHEN** 某维度在过去 4 周内 `human_override=1` 的记录 ≥ 3 条
- **THEN** `reflection_runner` 调用 LiteLLM 分析这批 `override_note`，提炼共同模式，生成修订建议，在飞书周报卡片中展示，例如：「「原创度」被纠正 4 次，建议 edge_case 追加：大段直译自日文原文，即使有轻微改写也给 0 或 0.5」

#### Scenario: 运营者确认后提交新版本
- **WHEN** 运营者在飞书点击「采纳定义建议」，或在 Web UI 编辑定义后点击「发布新版本」
- **THEN** 系统将修改后的全量维度 JSON 作为新行写入 `scoring_dimension_versions`（`version` 语义递增，`is_active=1`，旧行置 0，`change_note` 记录本次修改内容），清空内存缓存，后续评分使用新定义

### Requirement: 版本历史查看与回滚
系统 SHALL 在 Web UI 提供维度版本历史页面，展示所有历史版本，支持查看每个版本的完整定义和变更说明，并可一键回滚。

#### Scenario: 查看历史版本列表
- **WHEN** 用户访问 Web UI「评分维度」管理页
- **THEN** 展示 `scoring_dimension_versions` 全部记录：版本号、创建时间、创建来源、变更说明，当前生效版本高亮标注

#### Scenario: 分析报告标注定义版本
- **WHEN** `dimension_analysis.py` 生成相关性报告
- **THEN** 报告头部标注「基于维度定义 v{version}，生成于 {date}」，避免不同版本的分析结果混淆对比
