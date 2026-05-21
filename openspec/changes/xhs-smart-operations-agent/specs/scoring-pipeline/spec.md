## MODIFIED Requirements

### Requirement: evaluate_quality 使用加权维度计算综合分
`evaluate_quality` 函数 SHALL 从 `agent_strategy.json` 读取 `dim_weights`，使用加权求和替代原有等权计数，计算 `title_score` 和 `content_score`。权重缺失时默认 1.0，结果 clamp 到 [0, 5]。

#### Scenario: 有权重配置时使用加权计算
- **WHEN** `agent_strategy.json` 中 `dim_weights = {"收藏驱动": 2.0, "有用信息": 1.5}`
- **THEN** `content_score = clamp(sum(dim_weights.get(d, 1.0) * v for d, v in content_dims.items()), 0, 5)`

#### Scenario: 无权重配置时行为与原版一致
- **WHEN** `dim_weights` 为空或文件不存在
- **THEN** 所有维度权重为 1.0，`title_score` 和 `content_score` 结果与原版等权计算完全相同

#### Scenario: LLM 未返回某维度时不报错
- **WHEN** LLM 响应 JSON 中缺少某个维度键（网络截断或模型省略）
- **THEN** 该维度视为 0，不抛异常，不影响其他维度的计算
