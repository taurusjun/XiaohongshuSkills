## Context

`metrics_history` 表每小时采集一次，每篇文章约 72 行/3天。当前详情页左侧 XHS 实发数据只展示最新值，时序数据完全未使用。

## Goals / Non-Goals

**Goals**
- 单篇文章维度的折线图，可切换指标
- Chart.js 按需加载，不影响页面初始加载速度

**Non-Goals**
- 多篇文章对比图（后续）
- 账号级趋势图（等 account_snapshots 数据积累后）

## Design

### API: `GET /api/metrics-history/<key>`

```python
@app.route('/api/metrics-history/<key>')
def api_metrics_history(key):
    rows = db.execute(
        "SELECT collected_at, views, likes, saves, comments, impression, click_rate "
        "FROM metrics_history WHERE news_key=? ORDER BY collected_at ASC LIMIT 72",
        (key,)
    ).fetchall()
    return jsonify({
        "snapshots": [dict(r) for r in rows],
        "count": len(rows)
    })
```

### 前端：详情页左侧面板

位置：`{% if news.publish_xhs %}` XHS 实发数据 section 之后，新增 `card-section`：

```html
<div class="card-section" id="trendSection" style="display:none">
  <div class="card-section-title" style="...">📈 增长趋势</div>
  <div style="display:flex;gap:4px;margin-bottom:8px">
    <button class="trend-btn active" data-metric="views">浏览</button>
    <button class="trend-btn" data-metric="likes">点赞</button>
    <button class="trend-btn" data-metric="saves">收藏</button>
    <button class="trend-btn" data-metric="click_rate">点击率</button>
  </div>
  <canvas id="metricsChart" height="140"></canvas>
  <p id="trendEmpty" style="display:none;...">暂无数据</p>
</div>
```

JS 流程（Jinja2 中 `{% if news.publish_xhs %}`）：
1. 动态加载 Chart.js CDN
2. fetch `/api/metrics-history/<key>`
3. 如果 `count=0` 则显示空状态，隐藏 canvas
4. 否则初始化 Chart，默认 metric=views
5. badge 点击切换 `chart.data.datasets[0].data`，调用 `chart.update()`

### X 轴格式

`collected_at` 格式为 `2026-05-23 14:00`，截取为 `05-23 14:00` 显示。

### 侧边栏「数据报表」

将 `display:none` 加到该 nav-item，等功能实现后再显示。
