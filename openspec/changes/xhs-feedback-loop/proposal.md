> ⚠️ **已合并**：本 change 的全部内容已合并至 `xhs-smart-operations-agent` 的 Phase 0 任务组，不再单独实施。本文件保留作历史参考。

## Why

当前评分体系（18 个二元维度）衡量的是内容「写得好不好」，但 XHS 算法分发依据的是「用户收藏/评论/点赞」等互动信号——两者相关但不等价，导致高评分文章未必获得高流量。现在已有足够的发布历史，是时候建立数据反馈闭环，用实际分发结果来校正评分维度的有效性。

## What Changes

- **新增** XHS 实发数据回收模块：发布后 4h/24h/72h 自动从 XHS 抓取浏览量/点赞/收藏/评论数，写回 SQLite `news` 表
- **新增** 评分维度「收藏驱动」：判断内容是否含清单/攻略/知识点，让用户产生"以后用得到"的想法（XHS 算法最重视收藏）
- **新增** 维度相关性分析工具：计算现有 18 维度与实际 `xhs_saves` 的 Pearson 相关系数，输出有效/无效维度报告
- **修改** SQLite `news` 表：新增 `xhs_views / xhs_likes / xhs_saves / xhs_comments / xhs_collected_at` 字段
- **修改** Web UI 列表页：新增「收藏率」列和排序；文章详情页展示实发数据面板
- **新增**（低优先级）封面图评分：通过多模态 LLM 对 gallery 首图评分（人脸清晰度/情绪感染/构图）

## Capabilities

### New Capabilities

- `xhs-metrics-collector`: 定时从已发布文章的 XHS 页面抓取实发互动数据，写回 SQLite
- `save-drive-dimension`: 新增「收藏驱动」评分维度，集成到现有 `evaluate_quality` 流程
- `dimension-correlation-analysis`: 离线分析工具，计算各评分维度与实际收藏数的相关性，辅助维度权重调整
- `metrics-ui`: Web UI 对实发数据的展示和排序增强

### Modified Capabilities

- `scoring-pipeline`: `evaluate_quality` prompt 新增「收藏驱动」维度，`_DIM_DEFS` 新增该维度定义

## Impact

- `scripts/sqlite_db.py` — schema migration，新增 5 个字段
- `scripts/yahoo_common.py` — `evaluate_quality` prompt 修改，新增维度
- `scripts/cdp_publish.py` — 新增 `fetch_note_stats(note_url)` 方法抓取已发布笔记数据
- `web/app.py` — 列表页新增收藏率列，详情页新增实发数据面板，新增 `/api/collect-metrics` 端点
- 新增 `scripts/metrics_collector.py` — 定时回收脚本
- 新增 `scripts/dimension_analysis.py` — 相关性分析工具
- 无外部依赖变更（CDP 已有，pandas/scipy 用于分析）
