# API Key后缀不匹配导致404（6/28经验）

## 问题

daily review存档中记录的文章key（如 `9feecef0a007802f` 截短前16位或更长片段中的后缀）与DB中实际key的后缀不一致。

**案例：** 宫馆凉太 key

- Review存档写法：`9feecef0a007802fcf66763821530e5745f8004e`
- DB实际key：`9feecef0a007802fd8a99dc7b332f997ce0aa6af`
- 后缀对不上 → GET `.../9feecef0a007802fcf66763821530e5745f8004e` 返回 `{"error":"not found"}`

## 根因

query.sh 在排序/分页不同时key可能变化。review存档和写稿session相隔时间中DB可能已有数据整理操作。**review存档中的key不能直接用于写稿。**

## 对策

写稿前从最新的API查询中重新获取完整40位key：

```bash
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=2026-06-28&limit=200" | python3 -c "
import json, sys
d = json.load(sys.stdin)
for r in d['rows']:
    if '宫馆' in r.get('title',''):
        print(f'key={r[\"key\"]} | {r[\"title\"]}')
"
```

## 验证

获取key后用完整的40位字符串调用API，确认返回200后再进入写稿。

```bash
curl -s --noproxy '*' 'http://127.0.0.1:5000/api/news/9feecef0a007802fd8a99dc7b332f997ce0aa6af' | python3 -c "
import json, sys
d = json.load(sys.stdin)
print('OK' if 'key' in d else 'NOT FOUND')
"
```
