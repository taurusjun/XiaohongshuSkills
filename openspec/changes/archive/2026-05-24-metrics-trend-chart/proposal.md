## Why

已发布文章有逐小时的 metrics_history 数据（10,766 行，3天），但目前详情页只显示最新快照数值，无法看到增长趋势。运营需要直观判断一篇文章发布后的数据走势（浏览量爬升曲线、收藏增长节奏），以决定是否追加发布或调整策略。

## What Changes

- 新增 `GET /api/metrics-history/<key>` API，返回文章最近 72 个快照的时间序列数据
- 详情页左侧面板新增「📈 增长趋势」折叠区域，含 Chart.js 折线图
- 默认展示浏览量，可切换至点赞/收藏/点击率
- 只在 `publish_xhs=1` 时显示，空数据有友好提示
- 侧边栏「数据报表」死链暂时隐藏

## Capabilities

### New Capabilities
- `article-metrics-trend`: 文章维度的 metrics_history 时间序列图表

### Modified Capabilities

## Impact

- `web/app.py`: 新增 API 路由 + DETAIL_HTML 左侧面板改动
- 无数据库 schema 改动，无新依赖（Chart.js CDN 动态加载）
