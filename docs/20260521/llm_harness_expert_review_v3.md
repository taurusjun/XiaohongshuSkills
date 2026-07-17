# LLM Harness 第三轮审查报告

**审查日期：** 2026-05-21
**整体评分：** 9.0 / 10（v1≈4 → v2=7.5 → v3=9.0）

---

## 第一部分：N1-N8 逐条核查

| # | 问题 | 状态 |
|---|---|---|
| N1 | translate_title schema 冲突 | ✅ 重命名为 translate_and_classify，schema 完整对齐 |
| N2 | generate_video_caption 缺失 | ✅ 工具表已补充（temperature=0.5, max_tokens=800）|
| N3 | override_dim_score 缺 image_url | ✅ 新增独立工具 override_cover_score |
| N4 | evaluate_content 架构不清晰 | ✅ 明确 FastMCP stdio，延迟 <10ms |
| N5 | run_reflection 触发路径缺失 | ✅ 新增 run_reflection 工具，返回 task_id |
| N6 | 工具输出 schema 缺失 | ✅ 所有工具补充完整 JSON schema |
| N7 | generate_content 缺 style 参数 | ✅ 已补充 style? 参数 |
| N8 | MCP error 格式未规范 | ✅ 统一 {error, code, message} + 3种常见 code |

**8/8 完全解决。**

Y6（content[:400] 残留）**✅ 完全清除**，统一为 content_ja[:800]。
Y18（周报模板/LLM 分离）**✅ 明确声明**，数值模板渲染 + 摘要 LLM 生成。

---

## 第二部分：新发现问题

### 🟡 中等

**P1. run_reflection 异步结果查询工具缺失**

`run_reflection` 返回 `{started, task_id}`，但 xhs-operations-server 没有 `get_task_status(task_id)` 工具。飞书推送失败时或运营者追问进度时，SKILL 无从响应。

**P2. get_candidate_articles 空结果原因不完整**

SKILL 给出「可能原因：抓取未运行/文章已丢弃」，遗漏第三种：所有文章处于 `pending_gallery=True` 状态等待图集下载。

**P3. batch_update_articles 与低分处理副作用语义未定义**

DISCARD 分支有副作用（写回 `topic_performance.discard_count`），batch 接口直接改 status 时是否触发这些副作用没有说明。

**P4. evaluate_content dim_version 默认值语义未声明**

`dim_version?` 为可选参数，不传时的 fallback 行为（使用 is_active=1 版本）未在 spec 中声明。

### 🟢 轻微

**P5. 「调整维度权重」场景无执行路径**

SKILL trigger 列表有此场景，但 xhs-operations-server 没有 `update_dim_weights` 工具，spec 应明确此场景只能手动编辑 JSON。

**P6. score_cover_image 的 image_path 格式未约定**

本地路径格式（绝对/相对/基于 cache 目录）未在 spec 中声明，容易踩坑。

---

## 第三部分：可实施性评估

| 模块 | 状态 | 说明 |
|---|---|---|
| scoring-pipeline | ✅ | temperature/schema/token budget/retry 均完整 |
| dimension-registry | ✅ | 表结构/版本管理/多进程缓存/example_0_5 均完整 |
| content-diversity | ✅ | 合并 prompt/类型检查/边界示例 均完整 |
| cover-image-scoring | ✅ | resize/减分方向/P0P1分阶段 均清晰 |
| reflection-runner | ✅ | 模板/LLM分离/版本隔离/has_pattern 均完整 |
| hashtag-strategy | ✅ | NER+字典/三层 schema/竞争强度 均完整 |
| low-score-handler | ✅ | 优先级链/7种修正指令/regeneration_history 均完整 |
| xhs-llm-server | ✅ | 6个工具/schema/error格式/架构决策 均明确 |
| xhs-operations-server | ⚠️ | 缺 get_task_status(P1)，batch 副作用语义(P3) 未定义 |
| xhs-ops-skill | ⚠️ | 核心场景完整，「调整权重」无执行路径(P5)，空结果原因不全(P2) |
| redbook-publish-skill | ✅ | trigger/LLM失败处理 均完整 |

---

## 结论

前两轮 30 个问题全部解决，剩余 P1-P6 均为中等及以下严重程度，不阻塞核心链路实施。系统设计已达到生产级可实施标准。
