# Silent Field Failure: ref_keys vs related_keys (6/5 session)

## The Bug

update.sh 的 PUT API 对**不存在于 DB schema 中的字段也会返回 `{"ok": true}`**，但实际上数据根本没有入库。

发生了6次：往 `ref_keys`（不存在的字段）写了6条不同key的关联素材，每次返回ok，但 DB 一条都没更新。

## Root Cause

DB schema 中只有 `related_keys` 字段，没有 `ref_keys`。但 Python/Flask 的 PATCH 或 UPDATE API 通常不会校验字段是否存在——你传什么字段它就接受什么字段，然后只在 SELECT 时才发现找不到。

## Symptoms

1. `{"ok": true}` 返回 ✅（虚假确认）
2. 数据库实际没有任何变化
3. 直到下次用 sqlite3 查该文章时才发现数据是空的

## Prevention

**每次写入前先确认字段名存在：**

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db ".schema news" | grep -o "related_keys\|ref_keys\|[a-z_]*_keys"
```

确认只有一个 `related_keys` 存在。

**写入后必须验证：**

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT related_keys FROM news WHERE key='<完整40位key>'"
```

如果返回空 → 字段名可能错了，或者 key 不对。

## 字段名一览（news 表）

| 正确的字段名 | 我错写的字段名 | 说明 |
|-------------|---------------|------|
| `related_keys` | `ref_keys` | 关联素材的key，逗号分隔 |
| `rewritten_title` | -- | 正确 |
| `rewritten_content` | -- | 正确，但长文本要用json文件传 |
| `preselected` | -- | 正确 |
| `publish_mode` | -- | 正确 |

---

## New (6/20): API key mismatch — returned key ≠ DB key

### The Bug

`curl -s "http://127.0.0.1:5000/api/news?limit=200&date_from=2026-06-20"` returns keys that **do not match** the keys stored in the SQLite database for the same rows.

Example:
- API returns: `92307c95b999923ed229decc74b4cf9f9634214e`
- DB stores:  `92307c95b999f69ff3c9ec7eb1a62b69d9d42966`

Both are 40-char hex strings, share the same 12-char prefix, but diverge after that.

### Symptoms

1. `PUT /api/news/<API-returned-key>` returns `{"ok": true}` ✅
2. `sqlite3 SELECT ... WHERE key='<API-returned-key>'` returns 0 rows
3. You think the data was written but nothing changed

### Why it's dangerous

The 12-char prefix match (`92307c95b999`) makes it look like the right row when you grep/scan. The API returns `{"ok":true}` which feels like confirmation. But the key doesn't exist in the DB so the update is silently dropped.

### Prevention

**Never trust API-returned keys for write operations. Always pull the real key from SQLite:**

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT key FROM news WHERE title LIKE '%关键词%'"
```

**写入后必须直接从 DB SELECT 验证，不要相信 API 的返回：**

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT preselected, LENGTH(rewritten_content) FROM news WHERE key='<完整40位key>'"
```

**If you used an API-returned key and SELECT shows no changes, re-pull the actual key from DB and retry.**
