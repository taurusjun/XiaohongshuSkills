## ADDED Requirements

### Requirement: xhs-llm MCP server 将 LiteLLM 调用语义化，每个工具有独立 temperature 和 schema
系统 SHALL 实现 `xhs-llm` MCP server，将不同用途的 LiteLLM 调用封装为独立工具，从根本上解决「一个 temperature 用于所有场景」的问题。

**工具列表及参数配置：**

| 工具名 | temperature | max_tokens | JSON schema | 说明 |
|---|---|---|---|---|
| `translate_title` | 0.2 | 500 | `{title_zh: str}` | 日文标题→中文，强制单字段输出 |
| `evaluate_content` | 0.1 | 4000 | `{dim_name: {value: float, reason: str}}` | 19/20 维度评分，确定性输出 |
| `generate_content` | 0.7 | 6000 | `{seo_title, summary, content, comment, tags: {precise, vertical, broad}}` | 完整内容生成含三层标签 |
| `analyze_overrides` | 0.3 | 1000 | `{has_pattern: bool, edge_case: str, evidence: [str]}` | 分析纠正记录，提炼 edge_case |
| `score_cover_image` | 0.1 | 500 | `{dim_name: {value: float, reason: str}}` | DeepSeek 视觉评分 |

所有工具共享：
- 2 次指数退避 retry（5s、15s）
- `response_format={"type": "json_object"}` 强制 JSON 输出
- 调用方无需关心 temperature 和 retry——工具内部已正确配置

#### Scenario: evaluate_content 以 temperature=0.1 返回稳定评分
- **WHEN** 调用 `evaluate_content(title, content, comment, dim_version="1.2.0")`
- **THEN** server 从 `scoring_dimension_versions` 表读取 v1.2.0 的维度定义，构造包含四段结构的 prompt，以 temperature=0.1 调用 LiteLLM，返回所有维度的 `{value, reason}` JSON

#### Scenario: generate_content 返回三层标签结构
- **WHEN** 调用 `generate_content(title_ja="...", body_text="...", format="story")`
- **THEN** server 返回 `{"seo_title": "...", "content": "...", "tags": {"precise": ["田中美奈实"], "vertical": ["日本写真"], "broad": []}}`，精准标签优先查询 artist_name_map.json 后注入

#### Scenario: analyze_overrides 判断是否有共同模式
- **WHEN** 调用 `analyze_overrides("face_clarity", ["侧脸，主体不清晰", "侧面角度，看不清表情", "半侧脸，面部被遮挡"])`
- **THEN** 返回 `{"has_pattern": true, "edge_case": "侧脸或背对镜头时，即使面部可见也给 0.5 而非 1", "evidence": ["侧脸，主体不清晰", ...]}`

#### Scenario: 网络错误时自动 retry
- **WHEN** LiteLLM 返回 503
- **THEN** 等待 5 秒重试，再次失败等待 15 秒重试，第三次失败返回 error，调用方做 fallback 处理

### Requirement: 图片评分前自动 resize 控制 token 成本
系统 SHALL 在 `score_cover_image` 工具中，先对本地图片做 resize（最长边 512px），再 base64 编码传给 DeepSeek，控制单张图片 token 消耗 < 500 tokens。

减分维度（`promo_feel`、`multi_person_blur`）在 prompt 中显式标注方向：「以下维度存在时扣分，值越高表示问题越严重」，防止 LLM 误解为「1=好」。

#### Scenario: 减分维度方向正确
- **WHEN** 图片有明显品牌 logo
- **THEN** DeepSeek 返回 `promo_feel: {"value": 1, "reason": "含品牌文字水印"}` — 该值在计算 cover_score 时作为减分
