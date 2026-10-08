# 6/24 API Key 后缀不匹配 + content_ja 空字段陷阱

## API返回的key与DB中的key后缀可能不同

发生场景：用 `curl -s http://127.0.0.1:5000/api/news/059d89806eea3e927a9afde4f0286ac5820e9cc1` 请求API，返回的JSON中 key 字段可能显示为截断或不同值。

**实际案例（6/24 小栗有以）：**
- query.sh 显示的 key: `059d89806eea3e927a9afde4f0286ac5820e9cc1`
- sqlite3 查到的实际 key: `059d89806eea5f159cd5bdb6bd24348f7094f16a`
- 两者完全不同（不仅是截短问题，是整个哈希不同）

**原因推测：** 素材可能有重复入库/多来源覆盖，API 默认返回第一条匹配记录，不一定是 DB 中最新的。

**应对措施：**
1. 永远用 `sqlite3 "SELECT key FROM news WHERE title LIKE '%关键词%'"` 获取完整 key
2. 不要信任 query.sh 输出或上一个 session 显示的 key
3. PUT 到错误的 key 会返回 `{"ok": true}` 但不更新任何记录

## API返回的content_ja为空但sqlite3中有值

发生场景：用 API GET 某条素材，`content_ja` 字段返回空字符串，但 sqlite3 直查该 key 的 content_ja 有 990+ chars。

**实际案例（6/24 小栗有以）：**
- API JSON 中 `content_ja: ""`（空字符串）
- sqlite3: `SELECT content_ja FROM news WHERE key='059d89806eea5f159cd5bdb6bd24348f7094f16a'` → 返回完整 990 chars

**原因推测：** JSON 序列化时 content_ja 含某些字符（转义符、特殊Unicode）导致 Python json module 解析后为空，或者 webapp 的数据读取逻辑中 content_ja 的处理路径有 bug。

**应对措施：**
1. API 返回 content_ja="" 时，先用 sqlite3 直查确认是否真有内容
2. 可以用 `sqlite3 db "SELECT content_ja, content FROM news WHERE key='<key>'" > /tmp/raw.txt` 导出 raw 数据
3. 如果 content_ja 真的为空（sqlite3 也返回空），则 fallback 到 title_ja 或 content 字段
