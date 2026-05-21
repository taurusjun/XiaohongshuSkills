## ADDED Requirements

### Requirement: 计算维度与实际收藏数和评论数的 Pearson 相关性
系统 SHALL 提供离线分析脚本 `scripts/dimension_analysis.py`，读取 SQLite 中有实发数据的文章，计算每个评分维度与多个目标变量的 Pearson 相关系数和 p 值。

> **目标变量说明：** 支持 `xhs_saves`（收藏）、`xhs_comments`（评论）、`xhs_views`（浏览）三个目标变量。`xhs_saves` 和 `xhs_comments` 是 `xhs-smart-operations-agent` 的 `reflection_runner` 计算 `engagement_score` 权重建议的核心依据，必须同时支持。

有效样本过滤：`xhs_saves > 0` 或 `xhs_comments > 0`（至少有一项互动数据），且 `xhs_collected_at CONTAINS '72h'`（已完成 72h 稳定数据回收）。

#### Scenario: 样本充足时输出分析报告
- **WHEN** 有效样本 ≥ 20 篇
- **THEN** 脚本对每个目标变量分别输出维度相关性表格，每行含：维度名、Pearson r、p 值、样本数，按 |r| 降序排列，p < 0.05 标记 ✓，否则标记「不显著」

#### Scenario: 样本不足时给出警告并继续输出
- **WHEN** 有效样本 < 20 篇
- **THEN** 脚本打印警告「样本量不足（N=X），结果仅供参考」并继续输出现有数据

#### Scenario: 运行命令
- **WHEN** 执行 `python scripts/dimension_analysis.py`
- **THEN** 在终端输出 Markdown 格式的相关性表格，可选 `--output report.md` 写入文件

### Requirement: 同时分析 xhs_saves、xhs_comments、xhs_views 三个目标变量
系统 SHALL 分别计算各维度与三个目标变量的相关性，输出三张表格，帮助区分「能带来收藏的维度」「能带来评论的维度」「能带来浏览的维度」。

> **为何包含 xhs_comments：** `xhs-smart-operations-agent` 的 `reflection_runner` 依赖 `dimension_analysis.py` 对 `xhs_saves` 和 `xhs_comments` 的相关性结果来生成 `engagement_score` 的双目标权重建议。如果本 change 不支持 `xhs_comments`，`reflection_runner` 的双目标分析功能将无法实现。

#### Scenario: 输出三目标对比
- **WHEN** 脚本运行，`--targets` 参数默认为 `saves,comments,views`
- **THEN** 输出三个表格，分别以 `xhs_saves` / `xhs_comments` / `xhs_views` 为目标；报告头部标注「分析时间：{date}，有效样本：N={X}」

#### Scenario: 指定单一目标变量
- **WHEN** 执行 `python scripts/dimension_analysis.py --targets saves`
- **THEN** 只输出 `xhs_saves` 相关性表格，跳过其他目标变量
