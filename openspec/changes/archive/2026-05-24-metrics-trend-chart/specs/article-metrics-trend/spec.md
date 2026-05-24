## ADDED Requirements

- `GET /api/metrics-history/<key>` 返回该文章最近 72 条 metrics_history 快照，按 collected_at 升序
- 返回字段：`collected_at, views, likes, saves, comments, impression, click_rate`
- 当 `news.publish_xhs=1` 且数据存在时，详情页左侧显示折线图
- 支持切换指标：浏览量 / 点赞 / 收藏 / 点击率
- `click_rate` 以百分比显示（×100）
- X 轴显示 `MM-DD HH:mm` 格式
- 数据为空时显示「暂无增长数据」提示，不渲染 canvas
- Chart.js 通过 CDN 按需加载，不影响页面初始加载
- 侧边栏「数据报表」nav-item 暂时隐藏（`display:none`）
