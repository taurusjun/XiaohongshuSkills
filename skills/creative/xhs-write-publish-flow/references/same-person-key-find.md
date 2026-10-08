# 查找同一人物的完整文章key

## 问题

`GET /api/news?search=野吕佳代` 返回的 key 可能被截短或不完整。
直接用截短 key 请求 `GET /api/news/<key>` 会返回空数据。

## 正确做法

用 SQLite 全库搜索，获取完整 40 位 key：

```bash
cd ~/PG/XiaohongshuSkills
sqlite3 data/news_dev.db \
  "SELECT key, substr(title,1,50) FROM news 
   WHERE title LIKE '%关键词%' 
   ORDER BY created_at;"
```

## 判断每条是否有实际内容

有些记录只有 100-170 字的自动摘要（AI生成的 content），没有 content_ja。
用 SQLite 看字段长度：

```bash
sqlite3 data/news_dev.db \
  "SELECT key, substr(title,1,30), LENGTH(content), LENGTH(content_ja) 
   FROM news WHERE title LIKE '%关键词%';"
```

- `content > 0` 且 `content_ja > 0` → 有实际内容，可读
- `content > 0` 但 `content_ja = 0` → 只有 AI 摘要，没原文
- `content = 0` 且 `content_ja = 0` → 空壳记录，跳过

## 6/10 session 案例

野吕佳代有 12 条记录，但只有 3 条有实际内容（content_ja > 0）。其他 9 条是自动抓取时产生的空壳（只有标题和 100-170 字的 content）。浪费时间去读它们 = 浪费时间。
