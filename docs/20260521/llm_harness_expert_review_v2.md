# LLM Harness 第二轮审查报告

**审查日期：** 2026-05-21
**整体评分：** 7.5 / 10（第一轮约 4/10）

---

## 第一部分：第一轮问题核查结果

| # | 问题 | 状态 |
|---|---|---|
| R1 | temperature 固定 0.7 | ✅ 已解决（分场景规定） |
| R2 | translate_title 20+行后处理 | ✅ 已解决（改 structured output） |
| R3 | generate_content 无 JSON schema | ✅ 已解决（完整 schema + 三层标签） |
| R4 | evaluate_quality 维度无定义 | ✅ 已解决（dimension-registry 四段结构） |
| R5 | 维度 prompt 2000+ tokens 未评估 | ✅ 已解决（完整 token budget 计算） |
| R6 | dim_version 混用历史数据 | ✅ 已解决（按版本分组分析） |
| Y1 | 无 retry 机制 | ✅ 已解决（2次指数退避） |
| Y2 | reasoning_content 正则无效 | ✅ 已解决（json.loads + re.DOTALL） |
| Y3 | max_tokens=300 截断 | ✅ 已解决（改为 500） |
| Y4 | tsundere_mode 随机注入 | ✅ 已解决（参数化 style=） |
| Y5 | 禁用词 50+ 超出 attention | ✅ 已解决（10个+后验规则） |
| Y6 | content[:200] 太少 | ⚠️ 部分解决（改为 800 但文档有 [:400] 残留） |
| Y7 | response_format 无 schema 验证 | ✅ 已解决（prompt 中声明 schema） |
| Y8 | _trim_reasoning workaround | ✅ 已解决（structured output，max_tokens 800） |
| Y9 | SKILL 无智能体运营场景 | ✅ 已解决（新建 RedBookOps SKILL） |
| Y10 | SKILL 无 LLM 失败处理 | ✅ 已解决（补充 3 个 Scenario） |
| Y11 | example_0_5 字段缺失 | ✅ 已解决 |
| Y12 | 多进程内存缓存 TTL 问题 | ✅ 已解决（DB 版本号时间戳） |
| Y13 | format_suitability 无类型检查 | ✅ 已解决（isinstance wrap） |
| Y14 | 体裁 prompt 无边界示例 | ✅ 已解决（story/ranking/comparison 各 2 正反例） |
| Y15 | base64 token 成本未评估 | ✅ 已解决（512px resize，<500 tokens/张） |
| Y16 | 减分维度方向歧义 | ✅ 已解决（prompt 显式标注） |
| Y17 | edge_case 提炼无置信度 | ✅ 已解决（has_pattern 先判断） |
| Y18 | 周报生成未区分模板/LLM | ⚠️ 部分解决（tasks.md 有说明，spec 未明确） |
| Y19 | artist_name_map 全量注入 | ✅ 已解决（NER + 字典查询） |
| Y20 | 标签生成与内容生成两个 prompt | ✅ 已解决（合并到 generate_content） |
| Y21 | 修正指令只覆盖 2 种维度 | ✅ 已解决（7 种 minus 维度全覆盖） |
| Y22 | regeneration_history 不结构化 | ✅ 已解决（JSON 数组字段） |

**20/22 完全解决，2/22 部分解决，0 未解决**

---

## 第二部分：新发现问题

### 🔴 严重

**N1. translate_title 工具 schema 冲突**

xhs-llm-server 工具表：`translate_title → {title_zh: str}`
scoring-pipeline + content-diversity：返回 `{title_zh, summary_zh, format_suitability, reason}`

两个 spec 不一致，实现者会产生歧义。建议将工具重命名为 `translate_and_classify`，schema 对齐 content-diversity 的完整输出。

**N2. generate_video_caption 在 xhs-llm-server 工具表中完全缺失**

scoring-pipeline 明确要求该函数改用 structured output + temperature=0.5 + max_tokens=800，但 xhs-llm-server 的 5 个工具里没有 `generate_video_caption`，导致 temperature 管控失效。

### 🟡 中等

**N3. override_dim_score 缺少 image_url 参数，无法纠正封面图评分**

cover-image-scoring spec 定义封面图纠正写入 `cover_image_scores` 表（需要 image_url 定位），但 `override_dim_score` 工具只有 `news_key, dim_name, value, note`，无法区分文本维度和图片维度纠正。

**N4. evaluate_content 调用方式架构不清晰**

scoring-pipeline 说「通过 xhs-llm MCP 调用」，但 cron job 里的 `evaluate_quality` 是 Python 内部调用。没有说明 cron job 是否也走 MCP 协议（stdio），还是直接 import 函数。

**N5. run_reflection 触发路径缺失**

RedBookOps SKILL 有「运行一次反思分析」trigger，但 reflection_runner.py 是 CLI 脚本，xhs-operations-server 没有 `run_reflection` 工具，SKILL spec 也没有说明执行方式。

**N6. xhs-operations-server 所有工具缺少输出 schema**

工具列表只定义了输入，输出只用文字描述（「返回文章列表」「周报数据」），没有 JSON schema。LLM（SKILL）在解析返回值时会猜字段名，导致不稳定。

**N7. generate_content 工具缺少 style 参数**

scoring-pipeline 要求 `style="tsundere"|"normal"` 参数，但 xhs-llm-server 工具表的 `generate_content` 参数只有 `title_ja, body_text, format`，缺少 `style`。

**N8. MCP 工具 error 返回格式未规范**

`update_article_status` / `activate_dimension_version` / `override_dim_score` 输出标注 `ok/error`，但没有定义 error 时的具体格式（字段名、错误码等），SKILL 层无法可靠处理错误。

---

## 第三部分：MCP 设计评估

**工具粒度：** 整体合适，数据/推理职责分离清晰。

**输出 schema 缺失是最大问题**：xhs-operations-server 所有工具无输出 schema，需要补充。

**缺少的重要工具：**
1. `run_reflection(mode="quick"|"full")` — 支持从 SKILL 触发反思
2. `override_cover_score(news_key, image_url, dim_name, value, note)` — 封面图维度纠正
3. `generate_video_caption(video_context, style?)` — 覆盖配文生成

**职责边界模糊点：**`evaluate_content` 需要从 DB 读维度版本，xhs-llm-server 访问了 SQLite（按设计是方案 B），spec 中应明确声明这是架构决策。

---

## 第四部分：SKILL 设计评估

**trigger 覆盖：** 基本完整，但「调整维度权重」和「观察期话题」缺对应 Scenario。

**执行流程：** 有 Scenario 的 3 个场景流程清晰，二次确认设计合理。

**缺失流程：**
- `run_reflection` 执行方式（N5）
- MCP 工具返回 error 时的处理
- `get_candidate_articles` 返回空时的用户提示

---

## 最需关注的 3 个剩余问题

1. **N1 translate_title schema 冲突** — 直接影响实现正确性，动工前必须对齐
2. **N6 xhs-operations-server 输出 schema 缺失** — LLM 解析返回值依赖字段名，缺少 schema 会导致 SKILL 不稳定
3. **N5 run_reflection 触发路径** — SKILL 的核心运营场景之一，触发后 SKILL 无从下手
