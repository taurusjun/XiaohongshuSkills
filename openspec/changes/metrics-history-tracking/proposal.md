## Why

当前 `metrics_collector` 每次覆盖 `xhs_*` 字段，看不到趋势。改成每小时追加历史快照。

## What Changes

### 回收模型：从固定窗口 → 每小时轮询

```
旧：发布后 4h/24h/72h 触发一次，覆盖 news 表
新：每小时对所有"发布<7天且今天还没回收过"的文章采集一次，追加历史表
```

### 表结构

```sql
CREATE TABLE IF NOT EXISTS metrics_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    news_key    TEXT NOT NULL,
    collected_at TEXT NOT NULL,   -- "2026-05-21 14:00"（精确到小时）
    views       INTEGER DEFAULT 0,
    likes       INTEGER DEFAULT 0,
    saves       INTEGER DEFAULT 0,
    comments    INTEGER DEFAULT 0,
    UNIQUE(news_key, collected_at)  -- 同一小时不重复
);
```

### 采集逻辑

1. CDP 导航到 creator 数据看板页面
2. 页面自动调用 `analyze/list`（自带 X-s 签名），CDP Network 拦截响应
3. 提取 `note_infos`，按 `post_time` 过滤最近 7 天
4. 按 `note_id` 匹配 DB 中 `publish_xhs=1` 的文章
5. 批量写入 `metrics_history` + 更新 `news` 最新值
6. 每篇文章每天最多 24 行，7 天 = 168 行，500 篇 = 8.4 万行

### 回收按钮：从详情页 → 列表页

在管理页面列表顶部加「🔄 回收数据」按钮，点击后异步批量回收所有已发布文章的本小时数据。不再是单篇回收。

### 数据清理

`cleanup_old_states` 风格：`cleanup_old_metrics(days=90)` 删除 90 天前的记录，crontab 可每周触发。

## Impact

| 文件 | 改动 |
|------|------|
| `scripts/sqlite_db.py` | 新增 `metrics_history` DDL + `record_metrics()` + `cleanup_old_metrics()` |
| `scripts/metrics_collector.py` | 去掉固定窗口逻辑，改为每小时全量采集 |
| `web/app.py` | 详情页「立即回收」按钮移除；列表页新增批量回收按钮 + API |
