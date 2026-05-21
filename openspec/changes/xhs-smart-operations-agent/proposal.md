## Why

当前系统是一条手动流水线：运营者每天需要亲手触发抓取、逐篇勾选、手动发布，缺乏对账号目标的感知、对历史数据的学习、以及对内容质量的自主判断。随着 `xhs-feedback-loop` 建立起实发数据回收能力，现在具备了构建完整运营智能体的数据基础——让系统从「听指令执行」升级为「有目标、能规划、会反思、人工把关」。

## What Changes

- **新增** 感知层扩展：`xhs_trend_scanner.py` 每日扫描 XHS 各话题高收藏内容，提取标题模式和标签分布
- **新增** 记忆层扩展：3 张新 SQLite 表（`topic_performance` / `account_snapshots` / `agent_strategy`）+ `config/agent_strategy.json`
- **新增** 规划层（核心）：`agent_planner.py` 每日生成内容计划，基于历史表现选话题、定配额、推荐发布时间
- **新增** 低分诊断与重试逻辑：生成后立即评分，按失败原因分类处置（丢弃 / 重生成 / 暂存 / 人工队列）
- **新增** 维度注册表：`config/scoring_dimensions.json` 作为所有评分维度的单一真相来源，含定义/正例/负例/边界说明，驱动 LLM prompt 动态构造
- **新增** 人工纠正机制：Web UI 允许运营者翻转任意维度的 LLM 评分，纠正值优先用于相关性分析
- **修改** 优化目标：从单一「最大化收藏数」改为复合互动分 `engagement_score = saves_weight × avg_saves + comments_weight × avg_comments`，权重可配置（默认 0.6/0.4）
- **新增** 评论引导性维度：第 20 个评分维度，内容是否自然引出评论讨论（区别于「主动讨赏」）
- **新增** 封面图评分：DeepSeek 视觉模型对候选封面图进行 6 个客观维度评分，纳入综合候选排序
- **修改** 评分加权化：`content_score` / `title_score` 改为加权求和，权重从 `agent_strategy.json` 热读取
- **新增** 飞书 Human-in-Loop：开放平台 Bot + 交互卡片，日常审批 + 周策略审批 + 异常告警
- **新增** 话题标签三层策略：精准标签（艺人/作品名）+ 垂类标签（账号权重积累）+ 泛流量标签（谨慎使用），结合竞争强度过滤
- **新增** 形式趋势 vs 话题趋势区分：trend_scanner 拆分两类信号，`is_fresh` 标记当日新兴话题，14:00 轻量补扫捕捉日内热点
- **新增** 算法变化响应机制：`min_sample_threshold` 可配置 + 运营者手动「上报算法变化」触发即时分析 + 快速实验模式（单篇权重覆盖 + 24h 对比）
- **新增** 多垂类并行支持：`active_verticals` 配置，各垂类配额独立分配，`topic_performance` 按垂类隔离，内容生成按垂类加载不同 prompt 模板
- **修改** 冷启动期配额弹性：默认 3 篇，热点多时上限 4 篇；热点内容 2 小时 SLA
- **新增** 智能体主循环：`agent_runner.py`（每日 07:00）+ `reflection_runner.py`（每周日 23:00，**P2 实施**）
- **新增** 执行层统一封装：`agent_tools.py` 包装现有所有工具

## Capabilities

### New Capabilities

- `trend-scanner`：扫描 XHS 话题热门内容，提取结构化趋势信号
- `memory-layer`：话题表现历史、账号快照、策略参数的持久化与读写
- `daily-planner`：基于记忆层数据生成每日内容计划（话题选择、配额决策、时间推荐）
- `low-score-handler`：生成后评分不达标时的诊断分类与自动处置
- `dimension-registry`：评分维度注册表（含第 20 个维度「评论引导性」），结构化定义驱动 prompt，支持人工纠正和版本管理
- `cover-image-scoring`：使用 DeepSeek 视觉模型对封面图进行客观维度评分（清晰度/构图/情绪/色彩），纳入综合候选排序
- `weighted-scoring`：评分维度加权化，支持热更新权重配置
- `feishu-integration`：飞书 Bot 推送 + 交互卡片审批 + 回调接收
- `agent-runner`：每日主循环编排（感知→规划→执行→通知→发布→回收），含 CDP 重试降级和数据健康检查
- `reflection-runner`：每周反思循环（维度分析→更新记忆→生成建议→推送周报），含触发式反思和 Bonferroni 统计校正
- `content-diversity`：体裁适用性前置判断（LLM 先判断该内容适合哪些体裁）+ 轮换从适合体裁中选取（不强制改写）+ 相似度检测 + 账号人设一致性约束
- `hashtag-strategy`：三层话题标签策略（精准/垂类/泛流量），结合竞争强度感知，防止泛标签无效堆砌
- `risk-control`：CDP 操作行为随机化、补发上限控制、图片来源标注

### Modified Capabilities

- `scoring-pipeline`：evaluate_quality 的分数计算逻辑从等权改为加权，输入权重来自配置文件

## Impact

**新增文件：**
- `scripts/xhs_trend_scanner.py`
- `scripts/agent_planner.py`
- `scripts/agent_tools.py`
- `scripts/agent_runner.py`
- `scripts/reflection_runner.py`
- `scripts/feishu_bot.py`
- `config/agent_strategy.json`
- `config/scoring_dimensions.json`

**修改文件：**
- `scripts/yahoo_common.py` — `evaluate_quality` 加权化
- `scripts/sqlite_db.py` — 新增 3 张表的 schema + CRUD
- `web/app.py` — 新增 `/webhook/feishu` 回调路由

**外部依赖：**
- 飞书开放平台应用（App ID / App Secret / Webhook URL，配置在 `.env`）
- `scipy`（相关性分析，已在 `xhs-feedback-loop` 引入）

**前置依赖：**
- `xhs-feedback-loop` change 必须完成（依赖 `xhs_saves` 数据 + `metrics_collector.py`）

## 业务优先级

### P0 — 上线前必须完成（关乎系统能否正确运转）

| Capability | 业务理由 |
|---|---|
| `content-diversity` | 内容同质化是「越晚越难修」的风险，必须 Day 1 就有 |
| `risk-control` | 账号被封等于一切归零 |
| `agent-runner` | 系统主循环 |
| `feishu-integration` | 人机协作入口 |
| `daily-planner` | 含 growth_stage 冷启动逻辑（冷启动期保守策略会害账号） |
| `cover-image-scoring` | 封面决定点击率，DeepSeek 视觉已可用 |
| `hashtag-strategy` | 标签策略直接影响每篇内容的初始分发 |

### P0 — 上线前必须完成的人工任务（非代码，运营决策）

- **初始维度权重垂类校准**：根据娱乐垂类特性，在 `scoring_dimensions.json` 中手动设置初始权重（如「有用信息」降至 0.4，「原创度」重写定义为「视角独特性」而非「素材原创性」）。不要等 reflection 来建议——这是运营判断，不是数据驱动决策。
- **focus_topics 确认**：在 `agent_strategy.json` 中设置账号核心锚定话题（如 `["写真", "日本女星"]`），冷启动期 80% 配额集中于此。

### P1 — 上线后第一个月内

| Capability | 说明 |
|---|---|
| `low-score-handler` | 减少运营负担 |
| `dimension-registry` | 初期手动维护 JSON 即可，版本管理上线后再启用 |
| `trend-scanner` | 无历史数据时信号弱，一个月后才有意义 |
| `weighted-scoring` | 依赖一定数据量 |
| reflection-runner 的「触发式反思 + 周报生成（不含权重建议）」 | 有了数据才有意义，但不需要等 40 篇样本 |
| 竞品账号情报扫描（trend-scanner 内） | 需要先确定竞品账号名单 |

### P2 — 稳定运营后（数据积累 ≥ 8 周后）

| Capability | 说明 |
|---|---|
| reflection-runner 的「统计分析 + 权重建议生成」 | 需要 40 篇有效样本 ≈ 20 周 |
| `weighted-scoring` 的相关性校准 | 依赖 reflection-runner P2 结果 |

> **注意：** `memory-layer`（建表 + 基础 CRUD）是 **P0 必须完成项**——P0 的 `agent-runner`、`daily-planner`、`risk-control` 全部依赖 `topic_performance / account_snapshots / agent_strategy` 表存在。「数据积累」是 P1/P2 的事，「建表」是 P0 的事，两者不能混淆。
