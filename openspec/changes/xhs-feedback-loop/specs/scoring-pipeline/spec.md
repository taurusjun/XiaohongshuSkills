## MODIFIED Requirements

### Requirement: evaluate_quality 包含 19 个维度，prompt 从注册表动态构造
`evaluate_quality` 函数 SHALL 向 LLM 发送包含「收藏驱动」在内的 19 个维度，并从 `config/scoring_dimensions.json` 注册表读取每个维度的结构化定义（判断标准/正例/负例/边界），动态构造 prompt，替代原来的裸名字列表。

LLM 输出格式保持不变：`{"维度名": {"value": 0或1, "reason": "理由说明"}}`

#### Scenario: prompt 包含结构化维度定义
- **WHEN** 调用 `evaluate_quality(title_zh, content, comment)`，且 `scoring_dimensions.json` 存在
- **THEN** 发送给 LLM 的 prompt 中每个维度包含四段：判断标准、给1示例、给0示例、边界说明，而非裸名字

#### Scenario: 注册表缺失时降级为裸名
- **WHEN** `scoring_dimensions.json` 不存在或某维度缺少 `definition`
- **THEN** 该维度在 prompt 中仅展示名称，行为与旧版相同，不中断服务

#### Scenario: content_score 包含收藏驱动贡献
- **WHEN** LLM 返回 `收藏驱动: {"value": 1}`
- **THEN** `content_score` 按加权计算时包含该维度贡献，上限仍为 5

#### Scenario: LLM 未返回收藏驱动（兼容旧调用）
- **WHEN** LLM 响应 JSON 中缺少 `收藏驱动` 键
- **THEN** 系统不抛异常，跳过该维度，`content_score` 按其余维度计算
