# 系统设计专家审查报告

**审查日期：** 2026-05-21
**整体评分：** 7 / 10

---

## 一、系统抽象合理性

### scoring_dimension_versions DB 版本管理 🟡 中等
17 个实现任务中 8 个是「维度版本管理基础设施」。`scoring_dimensions.json` + Git commit 已能实现版本追踪和回滚，DB 版本管理的额外价值（多进程感知、飞书一键发布）在运营者频繁改维度之前不会体现。**建议 Phase 0/P0 用 JSON + Git，DB 版本管理推迟到 P2。**

### MCP server 架构 🟡 中等
- `xhs-operations` MCP：价值明确，解决 Claude Code 无法直接操作 SQLite 的问题，按计划实施。
- `xhs-llm` MCP：**时机偏早**。动机（强制 temperature + JSON schema）可通过直接给 `call_litellm` 加 `temperature` 参数解决，不需要 MCP。等有跨语言客户端需求时再做。

### agent_strategy KV 表混用多职责 🔴 严重
`agent_strategy` 表混用了：配置（`dim_weights`、`growth_stage`）、运行时状态（`daily_plan_YYYYMMDD`、`runner_progress`）、任务队列（`task_{task_id}`）。运行时状态无 TTL 会无限积累，无法查询「所有进行中任务」。**建议拆分为 `agent_config` 和 `agent_state` 两类，后者带 `date` 字段，7 天后可清理。**

---

## 二、系统可扩展性

### active_verticals + vertical 字段 + dim_weights_by_vertical 🔴 严重：过度设计
当前只有一个娱乐垂类（idol），但设计引入了 `active_verticals` 配置、`topic_performance.vertical` 字段、`dim_weights_by_vertical` 三层隔离逻辑。单垂类阶段所有带垂类参数的查询等价于无条件查询，但测试覆盖要额外处理垂类过滤路径。**建议完全移除，等运营第二个垂类时再加**（届时 migration 代价极小）。

### topic_performance + engagement_score 在冷启动期失效 🔴 严重
每日 2-3 篇，积累 40 篇有效样本需 14-20 周。冷启动期 avg_saves 和 avg_comments 接近 0，所有话题 engagement_score 接近 0，「70%/20%/10%」分配逻辑实际走 fallback（按 custom_keywords 平均分配）。**建议冷启动期 `plan_today()` 简化为「按 focus_topics 轮换 + 固定配额 3 篇」，等 topic_performance 有有意义数据后再启用完整逻辑。**

### 多账号/分布式 🟢 可接受
整体基于 SQLite + cron + CDP（单机），没有引入分布式设计，务实。

---

## 三、系统模块划分

### tasks.md 编号混乱 🔴 严重（影响执行跟踪）
- 存在两个编号为「2」的任务组（记忆层 vs 评分加权化）。
- 任务组 9 重复出现（长文翻页 vs 端到端验证，后者实际上是 10 但内部 item 用 9.x 编号）。
- 没有显式声明任务组之间的依赖关系，需要靠阅读 spec 推断。
**建议：修复编号，顶部增加依赖关系图。**

### yahoo_common.py 职责过载 🔴 严重（技术债）
承载：LiteLLM 调用、翻译、内容生成、评分、过滤、CDP 工具……这次重构有机会最小化拆分：
- 提取 `scripts/scoring.py`：`evaluate_quality` + `build_scoring_prompt` + `load_dim_weights`
- 提取 `scripts/content_generator.py`：`generate_content_and_comment` + `generate_video_caption` + `translate_title`
- `yahoo_common.py` 保留：过滤工具、CDP 工具、`call_litellm` 基础函数

### diagnose_low_score 放置位置 🟡 中等
`diagnose_low_score` 是纯函数（输入 scores → 输出 Action enum），放 `agent_tools.py` 会让单元测试困难（tools 混有副作用）。应放 `scoring.py`。

### memory-layer 扩展 sqlite_db.py 🟢 可接受
扩展现有文件比新建更合理，500 行不算过大。

---

## 四、模块独立交付与可测试性

### metrics_collector.py 无可离线测试路径 🔴 严重
核心依赖 CDP，在没有真实浏览器时无法测试任何内容。**建议：`fetch_note_stats` 做依赖注入参数，测试时传入 mock；`tests/fixtures/` 放 mock 数据。**

### agent_runner dry-run 定义太宽松 🟡 中等
当前 `--dry-run` 定义「不写 DB」会导致走不完整的执行路径。**建议拆分两层：**
- `--dry-run`：用 fixture 数据 + 临时内存 DB，完整走业务逻辑（可重复自动化测试）
- `--live-preview`：用真实数据，只读不写（人工验证工具）

### 维度版本管理测试复杂 🟡 中等
多进程缓存失效逻辑难以测试。若采纳「推迟 DB 版本管理」建议，此问题自动消失。

---

## 最优先需要改进的 3 个问题

1. **🔴 移除多垂类抽象**（active_verticals + vertical 字段 + dim_weights_by_vertical）— 纯净负债，减少约 20% 实现工作量
2. **🔴 拆分 agent_strategy KV 表** — agent_config（配置）+ agent_state（运行时状态，带 TTL）
3. **🔴 推迟 scoring_dimension_versions DB 版本管理** — Phase 0/P0 用 JSON + Git，省去 8 个基础设施任务

---

## 建议的模块交付顺序

| 顺序 | 模块 | 最小自测标准 |
|---|---|---|
| 1 | Phase 0 数据字段（5个 news 字段） | `init_db()` 后 `SELECT` 确认字段存在，写入 `xhs_saves=5` 后能读回 |
| 2 | scoring_dimensions.json + build_scoring_prompt | `evaluate_quality` 的 prompt 包含维度 `definition` 内容，评分返回 float 类型 |
| 3 | 记忆层建表（3 张新表 CRUD） | `upsert_topic_performance` + `get_top_topics(5)` 返回正确排序 |
| 4 | metrics_collector.py（P0.5-P0.7） | mock `fetch_note_stats` 验证 4h/24h/72h 时间点判断正确，`xhs_collected_at` 格式正确 |
| 5 | 评分加权化（call_litellm temperature + dim_weights） | 修改 `dim_weights['收藏驱动']=2.0` 后重新评分，`content_score` 差值符合加权公式 |
| 6 | xhs-operations MCP server | pytest 调用 `get_candidate_articles` 返回正确 schema；`override_dim_score` 写入后 `human_override=true` |
| 7 | feishu_bot.py + webhook | requests-mock 拦截飞书 API，发送成功；API 500 时不抛异常只记日志 |
| 8 | trend_scanner.py | mock CDP 返回 10 条笔记，`trend_signal` JSON 结构正确写入 `topic_performance` |
| 9 | agent_planner.py | 5 条 topic_performance + 内存 DB，`plan_today()` 返回有效 `DailyPlan` |
| 10 | agent_tools.py + 低分诊断 | `diagnose_low_score` 纯函数测试：`啰嗦重复=1` → REGENERATE，话题无聊 → DISCARD |
| 11 | agent_runner.py（主循环） | `--dry-run` 用 fixture 完整跑一遍不报错，打印含话题/配额/时间的计划摘要 |
| 12 | reflection_runner.py（P2） | 上线后 8 周再实施 |
