# Search Endpoint Blind Spot — API `search=` 搜不到已有素材

## 问题

素材中 `title_score=5.0`、`content_ja` 几千字，在API全量list端点中可见，但用 `query.sh --search "关键词"` 返回空结果。

### 7/9 实例

| 素材 | title_score | content_ja | 全量list是否可见 | search是否可搜 |
|------|-----------|-----------|----------------|---------------|
| 七海奈奈「毕业辟谣」 | 5.0 | 3810字 | ✅ | ❌ (query.sh --search "七海奈奈"/"七海なな"/"nanami"均空) |
| 森田光「麦当劳CM」 | 5.0 | 1921字 | ✅ | ❌ (query.sh --search "森田光" 返回JSONDecodeError空) |
| 田边桃菜「道歉」 | 5.0 | 514字 | ✅ | ❌ (query.sh --search "田边桃菜" 空) |

## 根因推测

`query.sh` 的 `--search` 参数对应 `/api/news?search=` 接口，其底层SQLite查询可能在中文/日文混合文本索引上有截断或分词问题。不是key后缀不匹配（那是另一个问题）。

## 影响

- 写稿前查同人物关联素材时，可能漏掉已有的高质素材
- 以为素材不存在或不可用，浪费了已抓取的内容

## 解决方案

### 方法A：从全量list JSON提取key（推荐）

```bash
# 先用 dump 全量
bash ~/.hermes/workspace/query.sh --date_from <DATE> --limit 200 --sort_by title_score --sort_dir DESC > /tmp/full_list.json

# 然后从JSON中直接按title/key查找
python3 -c "
import json
with open('/tmp/full_list.json') as f:
    d = json.load(f)
for r in d['rows']:
    k = r['key']
    t = r.get('title', '')
    cj = len(r.get('content_ja', '') or '')
    if '七海' in t or 'ななみ' in t:
        print(f'完整key={k}')
        print(f'content_ja={cj}字')
        print(f'title={t}')
"
```

### 方法B：sqlite3直接查DB

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT key, title, length(content_ja) FROM news WHERE title LIKE '%七海%'"
```

### 方法C：用全量list已返回的数据

如果已有 `/tmp/query_xxxx.json` 的数据在session上下文中，直接从该dict提取key，不需要再调用API：

```python
# 从已有的rows[]中找到目标key
for r in rows:
    if '七海' in r.get('title', ''):
        full_key = r['key']  # 这是完整40位key
        # 直接用这个key调用 detail 端点
```

## 操作流程调整

当 `query.sh --search` 返回空但review分级结果中有该素材时：

1. **不要假设素材不存在**
2. 用全量list dump (`query.sh --date_from <DATE> --limit 200`) 获取JSON
3. 在Python中从rows数组搜索title字段
4. 提取完整40位key，调用detail端点获取content_ja
5. 如果detail端点也返回空（format=None），说明key是list特有的伪key——跳过
