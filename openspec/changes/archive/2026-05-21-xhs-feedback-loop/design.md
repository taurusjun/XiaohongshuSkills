## Context

项目是一条全自动 XHS 内容发布流水线。内容发布后，目前没有任何机制回收实际表现数据，导致 AI 评分与流量表现脱钩。现有评分体系（`scripts/yahoo_common.py:evaluate_quality`）产出 18 个二元维度和 0-5 分的标题/内容分，全部存储在 SQLite `score_dims` / `news` 表。`cdp_publish.py` 已具备完整的 Chrome CDP 能力，可复用来抓取已发布笔记的互动数据。

## Goals / Non-Goals

**Goals:**
- 建立「发布 → 收数据 → 分析 → 校正」的完整闭环
- 新增「收藏驱动」维度，覆盖 XHS 算法最核心的信号
- 提供离线分析工具，用数据驱动维度权重决策
- Web UI 可视化实发数据，支持按收藏率排序

**Non-Goals:**
- 不实现自动调整维度权重（仍由人工根据分析报告决策）
- 不实现实时数据推送或 Webhook
- 封面图多模态评分本期不实现（低优先级，单独规划）
- 不支持 Notion 后端的数据回收（仅 SQLite）

## Decisions

### D1：数据回收方式用 CDP 而非 XHS API

XHS 无公开 API。选择复用 `cdp_publish.py` 中已有的 CDP WebSocket 基础设施，通过 Chrome 访问已发布笔记页面抓取数据。

**备选方案**：requests + cookie 模拟。风险更高（易被检测），且 CDP 已有完整的 tab 管理和 JS 执行能力，复用成本低。

### D2：三时间点回收（4h / 24h / 72h）

- **4h**：初始推量结束，反映封面+标题 CTR
- **24h**：第一波分发完成，互动信号稳定，是主要分析依据
- **72h**：算法是否持续扩量、搜索长尾流量

实现：`metrics_collector.py` 读取 `xhs_pub_time` 字段，计算各文章应回收的时间点，由外部 cron 每小时触发一次。

### D3：「收藏驱动」作为第 19 个维度加入现有 prompt

直接在 `evaluate_quality` 的 18 维度 prompt 末尾追加。不重构评分结构，保持与已有 `score_dims` 表兼容，向下兼容（老数据该维度为 null）。

**评分规则**：`收藏驱动 +1` 计入 `content_score`（与有用信息类似）。

### D4：相关性分析用 pandas + scipy，输出 Markdown 报告

`dimension_analysis.py` 是离线脚本，不依赖 Flask。对积累了足够样本（建议 ≥40 篇有实发数据）的情况下运行，输出各维度与 `xhs_saves` 的 Pearson r 和 p 值，排序后打印 / 存文件。

### D5：schema migration 用 ALTER TABLE + try/except 兼容模式

与现有 `sqlite_db.py` 中已有的迁移模式一致（见 `_ensure_columns`），无需引入 Alembic 等迁移框架。

## Risks / Trade-offs

- **CDP 抓取脆弱性** → XHS 页面结构变更可能导致选择器失效。缓解：抓取逻辑集中在 `metrics_collector.py` 单一文件，修复成本低；同时记录原始 HTML 片段便于调试。
- **账号风控** → 频繁访问自己发布的笔记页面可能触发 XHS 风控。缓解：每篇文章每个时间点只抓一次，回收间隔加随机抖动（±5min），非高峰时段运行（建议凌晨）。
- **样本量不足** → 初期数据少，相关性分析结论不可靠。缓解：`dimension_analysis.py` 输出 p 值，p > 0.05 的维度标记为「不显著」，避免误判。
- **24h 数据最重要但 72h 更全面** → 优先保证 24h 数据质量；72h 回收失败不影响主流程。

## Migration Plan

1. 部署前运行 `ALTER TABLE` migration（自动在 `sqlite_db.py` 初始化时执行）
2. 修改 `evaluate_quality` prompt，已有文章的新维度数据通过 `/api/regenerate/<key>` 手动补充
3. `metrics_collector.py` 加入 crontab：`0 * * * * cd /path && python scripts/metrics_collector.py`
4. 积累 40+ 篇数据后运行 `dimension_analysis.py` 生成首份分析报告

## Open Questions

- XHS 笔记详情页的互动数字是否需要登录才能看到？（初步判断无需登录，但需实测）
- `xhs_pub_time` 字段已有哪些文章填充？需核查覆盖率再决定是否需要补历史数据
