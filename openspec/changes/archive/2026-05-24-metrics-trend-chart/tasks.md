## 1. Backend API

- [x] 1.1 `web/app.py`: 新增 `GET /api/metrics-history/<key>` 路由，查询 metrics_history 最近72条，返回 JSON

## 2. 详情页前端

- [x] 2.1 DETAIL_HTML CSS: 添加 `.trend-btn` 样式（badge 风格，active 状态）
- [x] 2.2 DETAIL_HTML HTML: 在 XHS 实发数据 section 后添加「📈 增长趋势」card-section（含 canvas + 空状态 p）
- [x] 2.3 DETAIL_HTML JS: `{% if news.publish_xhs %}` 块内动态加载 Chart.js，fetch API，初始化折线图
- [x] 2.4 DETAIL_HTML JS: badge 点击切换指标（views/likes/saves/click_rate），`chart.update()`
- [x] 2.5 空状态处理：count=0 时隐藏 canvas，显示「暂无增长数据」

## 3. 侧边栏清理

- [x] 3.1 INDEX_HTML: 「数据报表」nav-item 不存在（未实现），无需操作
