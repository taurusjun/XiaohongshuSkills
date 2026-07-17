# LLM Harness 全面审查报告

**审查日期：** 2026-05-21
**审查范围：** 现有代码 LLM 调用 + xhs-smart-operations-agent 系统设计 + SKILL/MCP

---

## 第一部分：现有代码 LLM 调用点审查

### 1.1 `call_litellm` — 基础调用层

**🔴 temperature 固定为 0.7，所有场景共用**

evaluate_quality 需要确定性输出，应该 0.1。translate_title 是翻译，应该 0.2。generate_content_and_comment 可以接受 0.7。三个完全不同任务共用同一 temperature，是系统性错误。

**🟡 没有 retry 机制**

单次 HTTP 调用，网络抖动或 LiteLLM 瞬时 503 时直接返回空字符串，调用方跳过整条新闻。应该至少 2 次指数退避重试（5s、15s）。

**🟡 `reasoning_content` 的 JSON 提取正则 `r'\{[^{}]*\}'` 对嵌套 JSON 无效**

应该改用 `json.loads` + 多层 try。

---

### 1.2 `translate_title` — 标题翻译

**🔴 20+ 行后处理代码是 prompt 失控的症状，不是解法**

提取 `【译文】`、检测 meta_patterns、取最后非空行、剥离 8 种前缀……这些补丁说明 prompt 设计根本有问题。`"译文："` 后缀触发补全但没有 `response_format` 强制约束。

正确做法：合并翻译+体裁判断为一次 structured output 调用，返回 `{"title_zh": "...", "summary_zh": "...", "format_suitability": [...]}` 严格 JSON，删除全部后处理代码。

**🟡 max_tokens=300 对长标题可能截断**，建议 500。

---

### 1.3 `generate_content_and_comment` — 主内容生成

**🔴 单 prompt 输出 6 个字段，依赖 `【字段名】` 分隔，无 JSON schema 约束**

一旦模型在某个字段里输出了 `【` 字符，后续所有字段都会被截断或乱序。应该用 `response_format={"type": "json_object"}` 配合完整 JSON schema：

```json
{
  "seo_title": "string",
  "summary": "string",
  "content": "string",
  "comment": "string",
  "tags": {
    "precise": ["string"],
    "vertical": ["string"],
    "broad": ["string"]
  }
}
```

同时实现 hashtag-strategy spec 的三层标签要求，一举两得。

**🟡 tsundere_mode 随机注入约 300 字额外指令，影响 prompt 一致性**

应改为参数化：`generate_content_and_comment(..., style="tsundere"|"normal")`，由调用方决定，不在 LLM 函数内部随机。

**🟡 禁用词列表 50+ 个，超出有效 attention 范围**

超过 20 个约束条目时遵守率显著下降。保留最重要的 10 个在 prompt，其余用 Python 后验规则检查。

**🟢 `max(LITELLM_MAX_TOKENS, 8000)` 可能超出部分模型上下文上限**

---

### 1.4 `evaluate_quality` — 质量评分

**🔴 18 个维度只有裸名字，无判断标准，评分不可信**

同一篇文章多次评分可能相差 2-3 分。dimension-registry spec 已提出结构化定义，但代码完全没实现。

**🟡 评分上下文只有 content[:200]，太少**

`原创度` 等维度需要看全文，截断评估会造成系统性误判。应至少提供 content[:400]。

**🟡 `response_format={"type": "json_object"}` 已用但无 schema 验证**

只告诉 LLM 输出 JSON，没告诉它结构。应在 prompt 里明确声明 schema。

---

### 1.5 `generate_video_caption` — 短配文

**🟡 `_trim_reasoning` 是 prompt 工程失败的 workaround**

GLM 的 reasoning_content 混入正式输出，说明 system_prompt 约束无效。应改用 structured output 只提取 `caption` 字段。

**🟢 max_tokens=3000 对 80-120 字配文过大**，应改为 800。

---

## 第二部分：SKILL 定义审查

### 当前 SKILL.md 评估

**🟡 SKILL 覆盖的是"手动发布工具"，没有覆盖"智能体运营"场景**

缺失的 trigger 场景：
- `今日内容规划` / `看一下今天的候选文章`
- `运行一次反思分析` / `上周的内容表现怎么样`
- `调整维度权重` / `原创度这个维度最近被纠正很多次`
- `批量发布今日审批通过的文章`

**🟡 失败处理没有覆盖 LLM 调用失败的场景**

只覆盖了 CDP/登录/图片相关，没有处理 LiteLLM API key 过期、模型切换、quota 耗尽时的处理路径。

**🟢 trigger 条件 `发布内容到小红书` 太窄**，建议拆分为两个 SKILL。

---

## 第三部分：系统设计 LLM 调用点审查

### 3.1 dimension-registry：prompt 动态构造

**🟢 好的设计**：将维度定义动态注入 prompt，方向完全正确。

**🔴 20 个维度 × 完整定义 ≈ 2000+ tokens，需要评估 token budget**

**🟡 0.5 三档缺少 `example_0_5` 字段**

spec 里只有 `example_1` 和 `example_0`，没有 `example_0_5`。LLM 在没有示例时很少使用 0.5，导致分布两极化，0.5 的精度优势名存实亡。

**🟡 多进程内存缓存 TTL 一致性问题**

Flask + cron + agent_runner 各是独立进程，内存缓存无法互通。运营者在飞书采纳建议后，最多需要等 3 个进程各自的 5min TTL 过期，期间新旧版本并存。应改用 DB 版本号时间戳判断是否需要重新加载，而不是 TTL。

---

### 3.2 content-diversity：体裁前置判断

**🟢 好的设计**：合并翻译+体裁判断为一次调用，减少成本。

**🔴 合并 prompt 的返回格式未做类型检查**

如果模型输出 `"format_suitability": "news"` 而不是 `["news"]`，解析会静默失败或 crash。需在代码层对 `format_suitability` 做 `isinstance(v, list)` 检查并 wrap。

**🟡 体裁判断 prompt 缺少各体裁的边界示例**

`story` vs `news` 的边界对 LLM 来说不显然。没有示例时 LLM 会频繁把所有内容判为 `["news"]`，体裁功能名存实亡。

**🟡 Q&A 检测依赖 `「—」` 误判率高**

`「—」` 在日文中极其常见（不一定是 Q&A 格式）。应改为「同一行内存在 `Q:` + 后续内容」的组合模式。

---

### 3.3 cover-image-scoring：DeepSeek 视觉评分

**🟡 base64 图片 token 成本未评估**

1024×768 JPEG → base64 ≈ 800KB → ~1000-1500 tokens/张。前 3 张图/篇 × 10 篇/天 = ~45,000 tokens/天仅用于图片。建议压缩到 512px 最长边以控制成本。

**🟡 减分维度方向歧义**

`promo_feel`、`multi_person_blur` 是减分维度。LLM 容易统一理解为「1=好」，把有广告感的图打 `promo_feel=1`（理解为「广告感强=1分」而非「这是减分项」）。需要在 prompt 里对减分维度做显式方向标注：「以下维度存在时扣分（值越高越差）：...」

**🟢 P0/P1 分阶段引入 w3 权重，设计审慎。**

---

### 3.4 reflection-runner：LLM 生成周报 + 提炼 edge_case

**🔴 edge_case 更新后历史数据混用不同 dim_version，相关性分析会混淆**

`dim_version` 字段记录了打分时用的版本，但相关性分析如果混用新旧版本数据，会误以为是同一维度的数据。reflection_runner 应只对同一 `dim_version` 的数据做相关性分析，或在周报中标注「包含 vX.X（N=20）和 vY.Y（N=15）混合数据」。

**🟡 edge_case 提炼 LLM 调用缺少置信度输出**

当 override_note 本身不一致时（3次侧脸+1次背光），LLM 会强行合并产生错误建议。应让 LLM 先判断「是否有可提炼的共同模式（是/否）」，只在「是」时生成建议，并附上支持证据。

**🟡 周报生成应区分「模板渲染」和「LLM 生成」部分**

数值统计（发布篇数、总浏览等）用模板填充，只有自然语言摘要和建议部分用 LLM。

---

### 3.5 hashtag-strategy：三层标签生成

**🟡 `artist_name_map.json` 全量注入 prompt 不可扩展**

如果有 500 个条目，不能全塞进 prompt。应先从文章里做命名实体识别（NER），提取涉及的艺人名，再查字典，只把「当前文章涉及的艺人 + 标准译名」注入 prompt。

**🟡 标签生成与内容生成应合并，当前两个 prompt 格式并存**

`generate_content_and_comment` 的标签输出应直接改为三层 JSON 结构，不另起一个标签生成 prompt。

---

### 3.6 low-score-handler：重生成逻辑

**🟡 修正指令只覆盖 2 种失败维度（啰嗦/离题），实际 7 种 minus 维度都需要模板**

缺少：`简单通知`、`震惊体`、`概括全部`、`主动讨赏`、`负面情绪` 的对应修正指令。

**🟡 重生成历史记录在 `score_dims.reason` 字段不结构化**

应新增 `regeneration_history` JSON 字段或单独建 `regeneration_log` 表，便于程序查询和分析。

---

## 第四部分：MCP 需求评估

当前项目没有任何 MCP 配置。评估业务需求后，推荐新建 2 个 MCP server：

### 推荐 1：`xhs-operations` MCP（优先级最高）

封装 SQLite + 运营操作，让 Claude Code 和运营者都能直接调用：

```
tools:
  - get_candidate_articles(date?, status?)
  - get_article_detail(news_key)
  - update_article_status(news_key, status, note?)
  - get_dimension_versions()
  - activate_dimension_version(version)
  - get_topic_performance(vertical?, limit?)
  - get_weekly_stats(week_start?)
  - override_dim_score(news_key, dim_name, value, note)
```

### 推荐 2：`xhs-llm` MCP（优先级中）

将 LiteLLM 的不同用途封装为语义化工具：

```
tools:
  - translate_title(title_ja) → {title_zh}
  - evaluate_content(title, content, comment, dim_version?) → {scores}
  - generate_content(title_ja, body_text, format?, style?) → {seo_title, content, tags}
  - analyze_overrides(dim_name, notes[]) → {has_pattern, edge_case, evidence[]}
  - score_cover_image(image_path) → {scores}
```

**不建议封装为 MCP：**
- XHS CDP 操作：已有完整命令行接口，MCP 封装增量价值低
- 飞书通知发送：频率低，直接 HTTP 调用即可

---

## 优先级最高的 3 个 LLM Harness 问题

1. **🔴 `evaluate_quality` 维度无定义，评分不可信** → 实现 dimension-registry spec 的 prompt 动态构造，temperature 改为 0.1
2. **🔴 `generate_content_and_comment` 无 JSON schema** → 改用 structured output，合并三层标签输出，消灭全部后处理代码
3. **🔴 `translate_title` prompt 失控** → 合并翻译+体裁判断为一次 structured output 调用，temperature 改为 0.2，删除 20+ 行后处理代码

---

## SKILL 演进建议

**拆分为两个 SKILL：**

**SKILL 1：RedBookPublish**（保留现有，收窄 scope）
- trigger 改为更宽：`发布到小红书|发图文|发视频|搜索笔记|查看评论`
- 补充 LiteLLM 失败处理路径

**SKILL 2：RedBookOps（新建）**
- trigger：`看今天的候选|运行反思|调整维度权重|今日内容规划|上周表现|查看低分文章|纠正评分`
- 能力：通过 `xhs-operations` MCP 读取 SQLite 数据，给出有数据支撑的回答
