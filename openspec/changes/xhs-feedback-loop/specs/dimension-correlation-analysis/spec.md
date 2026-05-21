## ADDED Requirements

### Requirement: 计算维度与实际收藏数的 Pearson 相关性
系统 SHALL 提供离线分析脚本 `scripts/dimension_analysis.py`，读取 SQLite 中有实发数据（`xhs_saves > 0`）的文章，计算每个评分维度与 `xhs_saves` 的 Pearson 相关系数和 p 值。

#### Scenario: 样本充足时输出分析报告
- **WHEN** 有效样本（`xhs_saves` 非空且 > 0）≥ 20 篇
- **THEN** 脚本输出每个维度的 Pearson r、p 值、样本数，按 |r| 降序排列，并标注显著性（p < 0.05 标记 ✓，否则标记「不显著」）

#### Scenario: 样本不足时给出警告
- **WHEN** 有效样本 < 20 篇
- **THEN** 脚本打印警告「样本量不足（N=X），结果仅供参考」并继续输出

#### Scenario: 运行命令
- **WHEN** 执行 `python scripts/dimension_analysis.py`
- **THEN** 在终端输出 Markdown 格式的相关性表格，并可选 `--output report.md` 写入文件

### Requirement: 同时分析 xhs_saves 和 xhs_views 两个目标变量
系统 SHALL 分别计算各维度与「收藏数」和「浏览量」的相关性，帮助区分「能带来流量」vs「能带来收藏」的维度。

#### Scenario: 输出双目标对比
- **WHEN** 脚本运行且两个目标变量均有数据
- **THEN** 输出两个表格：一个以 `xhs_saves` 为目标，一个以 `xhs_views` 为目标
