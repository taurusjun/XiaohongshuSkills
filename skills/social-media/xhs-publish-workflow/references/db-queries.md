# DB 查询备忘：发布状态区分

## 核心字段

| 字段 | 用途 | 说明 |
|------|------|------|
| `publish_xhs` | INT (0/1) | 1=标记了要发（包括待发和已发） |
| `publish_time` | TEXT | 实际发布时间（发布API成功回写） |
| `xhs_pub_time` | TEXT | 计划发布时间（用户预定时填的） |
| `xhs_note_id` | TEXT | 小红书note ID（发布成功回写） |

## 前端状态判断逻辑

```javascript
// 关键代码行（web/app.py L1210）
const locked   = n.publish_xhs && n.publish_time;   // 已发布
const pending  = n.publish_xhs && !n.publish_time;  // 待发布
```

## 查询示例

```sql
-- 已发布（有publish_time）
SELECT count(*) FROM news WHERE publish_xhs=1 AND publish_time IS NOT NULL AND publish_time != '';

-- 待发布（无publish_time）
SELECT count(*) FROM news WHERE publish_xhs=1 AND (publish_time IS NULL OR publish_time = '');

-- API查询待发队列
GET /api/news?publish_xhs=pending&status=active

-- API查询已发布
GET /api/news?publish_xhs=published&status=active
```

## 常见误区

- `publish_xhs=1` ≠ 一定已发布。它只是"标记为要发布"，实际发没发要看 publish_time
- `xhs_note_id` 只有在真实发布成功后才会回写
- API 统计面板的 `published: 463` = 有 publish_time 的记录数
- 抓取器入库时可能自动设 `publish_xhs=1`（但 publish_time 为空），这些只是"进队"状态
