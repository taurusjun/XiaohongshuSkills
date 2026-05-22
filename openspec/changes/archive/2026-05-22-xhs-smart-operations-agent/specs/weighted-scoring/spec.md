## ADDED Requirements

### Requirement: 评分从等权改为加权求和
系统 SHALL 将 `title_score` 和 `content_score` 的计算方式从「等权计数」改为「加权求和后归一化到 0-5」。权重优先级：`agent_strategy.json` 的 `dim_weights` > `scoring_dimensions.json` 的 `default_weight` > 默认值 1.0。

#### Scenario: 使用自定义权重计算分数
- **WHEN** `agent_strategy.json` 中 `dim_weights = {"收藏驱动": 2.0, "有用信息": 1.5, "原创度": 0.5}`
- **THEN** `content_score = clamp(sum(weight_i * value_i for i in content_dims), 0, 5)`，其中 `weight_i` 从配置读取，缺失键默认 1.0

#### Scenario: 配置文件不存在时使用默认等权重
- **WHEN** `agent_strategy.json` 不存在或 `dim_weights` 为空对象
- **THEN** 所有维度权重默认 1.0，行为与原逻辑完全一致，向下兼容

#### Scenario: 权重热更新
- **WHEN** 运营者修改 `agent_strategy.json` 后，下一篇文章调用 `evaluate_quality`
- **THEN** 新权重立即生效（1 分钟内存缓存到期后），无需重启服务

### Requirement: 权重变更不影响历史分数
系统 SHALL 仅对新生成或触发 regenerate 的文章使用新权重，已存储的 `title_score` / `content_score` 不自动重算。

#### Scenario: 历史文章分数保持稳定
- **WHEN** 权重配置更新
- **THEN** `news` 表中已有记录的 `title_score` / `content_score` 不变，除非用户手动触发 `/api/regenerate/<key>`
