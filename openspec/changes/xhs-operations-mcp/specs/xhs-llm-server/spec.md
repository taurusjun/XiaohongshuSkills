## ADDED Requirements

### Requirement: xhs-llm MCP server 将 LiteLLM 调用语义化，每个工具有独立 temperature 和完整 schema

系统 SHALL 实现 `xhs-llm` MCP server，将不同用途的 LiteLLM 调用封装为独立工具。

**工具列表及完整 schema：**

| 工具名 | temperature | max_tokens | 输入参数 | 输出 schema |
|---|---|---|---|---|
| `translate_and_classify` | 0.2 | 500 | `title_ja, content_ja` | `{title_zh, summary_zh, format_suitability: [str], reason}` |
| `evaluate_content` | 0.1 | 4000 | `title, content_ja, comment, dim_version?` | `{dim_name: {value: float, reason: str}, ...}` |
| `generate_content` | 0.7 | 6000 | `title_ja, body_text, format?, style?` | `{seo_title, summary, content, comment, tags: {precise, vertical, broad}}` |
| `generate_video_caption` | 0.5 | 800 | `video_context, style?` | `{caption: str}` |
| `analyze_overrides` | 0.3 | 1000 | `dim_name, override_notes: [str]` | `{has_pattern: bool, edge_case: str\|null, evidence: [str], reason: str}` |
| `score_cover_image` | 0.1 | 500 | `image_path` | `{dim_name: {value: float, reason: str}, ...}` |

> **注意：** `translate_title` 已重命名为 `translate_and_classify`，单次调用同时完成标题翻译、正文摘要翻译和体裁适用性判断，返回 schema 对齐 content-diversity spec 要求。

所有工具共享：
- 2 次指数退避 retry（5s、15s）
- `response_format={"type": "json_object"}` 强制 JSON 输出
- 错误返回格式统一为 `{"error": true, "code": "str", "message": "str"}`（见下方错误规范）

**架构说明：** `evaluate_content` 工具需要从 SQLite `scoring_dimension_versions` 表读取当前生效的维度定义，这是有意设计的职责越界（xhs-llm-server 只读访问 SQLite）。替代方案（调用方先查版本再传入）会增加调用链，不如内部直接读更实用。xhs-llm-server 以只读方式访问同一 SQLite 文件，不进行写操作。

**调用方式：** `scripts/yahoo_common.py` 中的 `evaluate_quality`、`generate_content_and_comment`、`generate_video_caption` 等函数重构为调用 MCP 工具，通过 FastMCP 客户端调用（subprocess stdio，本地进程间通信，延迟 < 10ms）。

#### Scenario: translate_and_classify 单次返回翻译 + 体裁判断
- **WHEN** 调用 `translate_and_classify(title_ja="田中みな実...写真集...", content_ja="...")`
- **THEN** server 返回 `{"title_zh": "田中美奈实写真集...", "summary_zh": "...", "format_suitability": ["news", "story"], "reason": "有发售+粉丝反应的时间弧度"}` — 一次调用替代原来的翻译 + 体裁判断两步

#### Scenario: generate_content 支持 style 参数
- **WHEN** 调用 `generate_content(title_ja="...", body_text="...", format="story", style="tsundere")`
- **THEN** server 在 prompt 中追加傲娇风格指令；`style="normal"` 时不追加；默认 `style="normal"`

#### Scenario: generate_video_caption 使用 structured output
- **WHEN** 调用 `generate_video_caption(video_context="...", style="normal")`
- **THEN** server 返回 `{"caption": "..."}`，GLM 的 reasoning_content 不会混入 caption 字段

#### Scenario: evaluate_content 以 temperature=0.1 返回稳定评分
- **WHEN** 调用 `evaluate_content(title, content_ja[:800], comment, dim_version="1.2.0")`
- **THEN** server 从 `scoring_dimension_versions` 表读取 v1.2.0 维度定义，构造含四段结构和 example_0_5 的 prompt，返回所有维度的 `{value, reason}` JSON

#### Scenario: analyze_overrides 不一致时不强行生成
- **WHEN** 调用 `analyze_overrides("face_clarity", ["侧脸，主体不清晰", "侧面角度看不清", "背光过强"])` — 前两条关于侧脸，第三条关于背光
- **THEN** 返回 `{"has_pattern": false, "edge_case": null, "evidence": [], "reason": "纠正原因不一致（侧脸 vs 背光），无法提炼共同模式"}`

#### Scenario: 工具调用失败时返回统一 error 格式
- **WHEN** LiteLLM 返回 5xx 错误且 retry 耗尽
- **THEN** 工具返回 `{"error": true, "code": "LLM_UNAVAILABLE", "message": "LiteLLM 服务不可用，已重试 3 次"}` — 调用方（SKILL / yahoo_common.py）统一按此格式处理
