## MODIFIED Requirements

### Requirement: evaluate_quality prompt 从注册表动态构造，分数计算暂保持等权
`evaluate_quality` 函数 SHALL 向 LLM 发送包含「收藏驱动」在内的 19 个维度，并从 `config/scoring_dimensions.json` 注册表读取每个维度的结构化定义（判断标准/正例/负例/边界），动态构造 prompt。

> **本 change 的边界说明（避免与 xhs-smart-operations-agent 冲突）：**
> - ✅ 本 change 修改：prompt 构造逻辑（从 JSON 读维度定义，有定义则注入结构化描述，无定义则降级为裸名）
> - ❌ 本 change 不修改：分数计算逻辑（`title_score` / `content_score` 的加减法保持等权，`content_score += 1` 逻辑不变）
> - ❌ 本 change 不引入：`agent_strategy.json` 的权重配置，该文件由 `xhs-smart-operations-agent` 创建
>
> 分数计算从等权改为加权求和由 `xhs-smart-operations-agent` 的 `weighted-scoring` capability 统一实现，两个 change 的修改可顺序叠加不冲突。

LLM 输出格式：`{"维度名": {"value": 0或1, "reason": "理由说明"}}`。

`score_dims.value` 字段 SHALL 以 REAL 类型存储（不强制 INTEGER），初期值域为 {0, 1}，为后续 `xhs-smart-operations-agent` 扩展为 {0, 0.5, 1} 预留空间。应用层写入时使用 `float()` 转换，不使用 `int()`。

#### Scenario: prompt 包含结构化维度定义
- **WHEN** 调用 `evaluate_quality(title_zh, content, comment)`，且 `scoring_dimensions.json` 存在且包含该维度的 `definition`
- **THEN** 发送给 LLM 的 prompt 中该维度展示四段结构（判断标准、给1示例、给0示例、边界说明）

#### Scenario: 注册表缺失时降级为裸名（feedback-loop 实施初期的正常状态）
- **WHEN** `scoring_dimensions.json` 不存在，或某维度缺少 `definition` 字段
- **THEN** 该维度在 prompt 中仅展示名称，行为与旧版相同，不中断服务

> **注意：** `scoring_dimensions.json` 由本 change 的 task 1.2 创建，包含「收藏驱动」的完整定义和其余 18 个维度的基础字段。因此上线后，「收藏驱动」维度会立即使用结构化 prompt，其余维度暂时降级（后续由 xhs-smart-operations-agent 统一补全所有定义）。

#### Scenario: content_score 等权计入收藏驱动贡献
- **WHEN** LLM 返回 `收藏驱动: {"value": 1}`
- **THEN** `content_score` 按**等权 +1** 计算（与现有维度相同逻辑），不引入权重配置，上限仍为 5

#### Scenario: LLM 未返回收藏驱动（兼容旧调用）
- **WHEN** LLM 响应 JSON 中缺少 `收藏驱动` 键
- **THEN** 系统不抛异常，跳过该维度，`content_score` 按其余维度计算
