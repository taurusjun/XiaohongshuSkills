# 全量素材紧凑输出脚本

当每日素材量较大（50-150条）时，通过API全量拉取后用python格式化输出，比逐条手读或query.sh分页更高效。

## 用法

```bash
curl -s "http://127.0.0.1:5000/api/news?date_from=2026-06-15&limit=200" | python3 -c "
import sys,json
data=json.load(sys.stdin)
for r in data['rows']:
    print(r.get('key')[:12], '|', r.get('title','')[:60], '|', r.get('category',''), '|', r.get('fetch_by',''), '|', r.get('format',''), '|', r.get('title_score',''))
"
```

## 参数说明

| 字段 | 说明 |
|------|------|
| key[:12] | 完整key的前12位（用于识别，完整查询时再用完整40位） |
| title[:60] | 标题截取前60字符 |
| category | 分类（可能误导，需人工判断内容本质） |
| fetch_by | 抓取来源/赛道分配 |
| format | news/story（story有长文潜质） |
| title_score | 系统标题评分（0-5，5为最高） |

## 分页处理

如果今天素材超过200条：

```bash
# 第1页
curl -s "http://127.0.0.1:5000/api/news?date_from=2026-06-15&limit=200&offset=0" | python3 -c "import sys,json; data=json.load(sys.stdin); [print(r.get('key')[:12],'|',r.get('title','')[:60],'|',r.get('category',''),'|',r.get('fetch_by','')) for r in data['rows']]"

# 第2页（offset=200）
curl -s "http://127.0.0.1:5000/api/news?date_from=2026-06-15&limit=200&offset=200" | python3 -c "import sys,json; data=json.load(sys.stdin); [print(r.get('key')[:12],'|',r.get('title','')[:60],'|',r.get('category',''),'|',r.get('fetch_by','')) for r in data['rows']]"
```
