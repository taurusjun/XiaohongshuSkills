## MODIFIED Requirements

### Requirement: call_litellm 支持分场景 temperature，含 retry 机制和健壮 JSON 提取
`call_litellm` 函数 SHALL 接受 `temperature` 参数（默认 0.7），支持调用方按场景传入合适值，并内置 2 次指数退避 retry（5s、15s）和健壮的 JSON 提取逻辑。

**各场景 temperature 规定：**

| 调用场景 | temperature | 原因 |
|---|---|---|
| `evaluate_quality`（评分） | **0.1** | 需要确定性输出，同一文章多次评分结果应一致 |
| `translate_title`（翻译） | **0.2** | 翻译是结构化任务，低随机性，高 temperature 会产生不必要变体 |
| `generate_content_and_comment`（生成） | **0.7** | 内容创作需要多样性，高 temperature 合理 |
| `generate_video_caption`（配文） | **0.5** | 短文创作，适中 temperature |
| `analyze_overrides`（edge_case 提炼） | **0.3** | 分析任务，偏确定性 |

#### Scenario: 分场景传入 temperature
- **WHEN** `evaluate_quality` 调用 `call_litellm`
- **THEN** 传入 `temperature=0.1`，不使用默认值 0.7

#### Scenario: 网络抖动时自动 retry
- **WHEN** LiteLLM 返回 5xx 或网络超时
- **THEN** 等待 5 秒后重试，再次失败等待 15 秒重试，第三次失败才返回空字符串；调用方 fallback 逻辑不变

#### Scenario: reasoning_content JSON 提取使用多层 try
- **WHEN** 模型返回 `reasoning_content` 字段（GLM 等模型的推理过程）
- **THEN** 用 `json.loads()` 尝试解析整体；失败时用 `re.findall(r'\{.*?\}', text, re.DOTALL)` 遍历候选；而非原来的 `r'\{[^{}]*\}'`（对嵌套 JSON 无效）

---

### Requirement: translate_title 合并翻译+体裁判断为 structured output，删除后处理代码
`translate_title`（及体裁前置判断）SHALL 使用 `response_format={"type": "json_object"}`，合并翻译和体裁判断为一次调用，返回严格 JSON，删除全部依赖 `【译文】`、`meta_patterns` 等标记的后处理代码。

返回 schema：`{"title_zh": "string", "summary_zh": "string", "format_suitability": ["string"]}`

其他参数调整：
- `temperature=0.2`（翻译任务需要确定性）
- `max_tokens=500`（原 300 对长标题可能截断）

#### Scenario: structured output 消灭后处理代码
- **WHEN** 调用翻译+体裁判断
- **THEN** LLM 直接返回 JSON，代码只需 `result["title_zh"]`，不需要任何字符串清洗、前缀剥离、多行取最后一行等逻辑

---

### Requirement: generate_content_and_comment 使用 JSON schema 约束，输出三层标签
`generate_content_and_comment` SHALL 使用 `response_format={"type": "json_object"}` 并在 prompt 中声明完整 JSON schema，同时输出三层话题标签，消灭基于 `【字段名】` 分隔的解析逻辑。

完整返回 schema：
```json
{
  "seo_title": "string（小红书标题，≤38字）",
  "summary": "string（引流摘要，50字内）",
  "content": "string（正文，200-350字）",
  "comment": "string（编辑评语，100字内）",
  "tags": {
    "precise": ["string（艺人名/作品名，1-2个）"],
    "vertical": ["string（垂类标签，2-3个）"],
    "broad": ["string（泛流量标签，0-1个）"]
  }
}
```

其他调整：
- `style` 参数化：`generate_content_and_comment(..., style="tsundere"|"normal")`，由调用方显式传入，不在函数内部随机决定
- 禁用词精简：prompt 中只保留最关键的 **10 个**禁用词（原 50+ 个超出 attention 有效范围），其余在生成后用 Python 规则后验检查并触发单次重试

#### Scenario: JSON schema 消灭字段解析错误
- **WHEN** 模型在 `content` 字段中包含了 `【` 字符（如引用日文格式）
- **THEN** 不影响其他字段解析，返回 JSON 中各字段独立，不会截断

#### Scenario: tsundere_mode 参数化
- **WHEN** agent_runner 决定今日某篇内容用傲娇风格
- **THEN** 调用 `generate_content_and_comment(..., style="tsundere")`，prompt 追加风格指令；默认 `style="normal"` 时不追加

#### Scenario: 禁用词后验规则触发重试
- **WHEN** 生成内容中包含 prompt 外的高频 AI 惯用语（从 `ai_banned_phrases` 列表检测）
- **THEN** 触发一次静默重生成（不计入 REGENERATE 次数），最多重试 1 次

---

### Requirement: evaluate_quality 评分上下文扩展，prompt 中声明 JSON schema
`evaluate_quality` SHALL 将正文输入从 `content[:200]` 扩展至 `content[:400]`，并在 prompt 中明确声明期望的 JSON schema，而非仅靠 `response_format` 隐式约束。

prompt 中 schema 声明示例：
```
返回严格 JSON，格式为：
{"维度名": {"value": 0或0.5或1, "reason": "15-50字理由"}, ...}
所有 {N} 个维度都必须出现，value 只能是 0、0.5、1 三个值之一。
```

token budget 评估：20个维度 × 完整定义（含 example_0_5）≈ 2000 tokens + content[:400] ≈ 200 tokens = 单次评分约 2500 tokens input。按每天 10 篇 × 1.5 次平均评分 = 37,500 tokens/天，可接受。

#### Scenario: 扩展上下文提升原创度等维度准确性
- **WHEN** 文章正文前 200 字是引言，精华在后半段
- **THEN** `content[:400]` 覆盖更多内容，`原创度`、`有用信息` 等维度判断更准确

---

### Requirement: generate_video_caption 使用 structured output，降低 max_tokens
`generate_video_caption` SHALL 改用 `response_format={"type": "json_object"}`，返回 `{"caption": "string"}`，消灭 `_trim_reasoning` 推理截断 workaround；`max_tokens` 从 3000 降低至 800（目标输出为 80-120 字）。

#### Scenario: structured output 消灭 _trim_reasoning
- **WHEN** GLM 等模型的 reasoning_content 混入正式输出
- **THEN** 因为使用 JSON schema，模型只会把配文文本放在 `caption` 字段，不会把推理过程混入正式输出

---

### Requirement: evaluate_quality 使用加权维度计算综合分（原有，保留）
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
