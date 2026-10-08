# API key后缀不匹配：list端点key能入库，detail端点短key不能（7/20经验）

## 本次发现

**之前**的记录认为search端点的key是正确key，list端点的key有问题。但7/20的实际表现**相反**：

- **list端点** `curl '.../api/news?date_from=2026-07-20&limit=200'` 返回的完整key → PUT成功，rewritten_content正确落盘
- **detail端点** `curl '.../api/news/短key'` 的短key（同一素材） → PUT返回ok:true但rewritten_content为空

## 7/20踩坑经过

1. 从list端点的rows取了短key（前16位如 `f136a89a3e49072c`）
2. 用短key做PUT → 返回 `{"ok": true}` 
3. sqlite3查DB发现 `rewritten_content=''`，12篇全部无声失败
4. **根源**：list端点每条row的key字段是完整40位key，但我复制时只取了前16位

## 正确做法

1. 写稿前从list端点取全量数据 `curl '.../api/news?date_from=...&limit=200'`
2. 从rows中提取每条的**完整key**（40位SHA1）
3. 用这个完整key做PUT
4. 入库后从sqlite3验证rewritten_content非空

## 验证方法

```sql
-- 直接查DB确认内容已写入
SELECT key, LENGTH(rewritten_content), rewritten_title 
FROM news 
WHERE key LIKE 'f136a89a3e49072c%';
```

如果 `LENGTH(rewritten_content)=0`，说明key后缀不匹配，内容没落盘。

## 从list端点提取完整40位key的Python代码

```python
import urllib.request, json
url = 'http://127.0.0.1:5000/api/news?date_from=2026-07-20&limit=200'
with urllib.request.urlopen(url, timeout=10) as r:
    data = json.loads(r.read())
for row in data['rows']:
    if '关键词' in row.get('title',''):
        full_key = row['key']  # 这是完整的40位key
        print(full_key)
```
