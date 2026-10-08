# Content_ja为空时的恢复流程 — 7/11经验

## 问题

`GET /api/news/<key>` detail端点返回 `content_ja=""`（或字段为null），但同一素材在list端点（`GET /api/news?date_from=...`）中有content_ja数据。

## 根因

同一素材在query.sh输出、list端点、search端点返回的key后缀（后16位）不同。如果用query.sh截断显示或search返回的key去读detail，可能拿到空content_ja。

## 恢复步骤（不要跳过素材）

```
1. 确认content_ja为空
   curl -s --noproxy '*' http://127.0.0.1:5000/api/news/<KEY> | python3 -c "import json,sys; d=json.load(sys.stdin); print('cj:', len(d.get('content_ja','') or ''))"

2. 从list端点抓取全量数据，按title搜索正确key
   curl -s --noproxy '*' 'http://127.0.0.1:5000/api/news?date_from=<DATE>&limit=200' | python3 -c "
   import json,sys
   d = json.load(sys.stdin)
   for r in d['rows']:
       if '关键词' in r.get('title','') or '关键词' in str(r.get('tags','')):
           cj = r.get('content_ja','') or ''
           print(f'KEY={r[\"key\"]} | cj={len(cj)} | {r[\"title\"][:50]}')
   "

3. 用list端点返回的key重新读detail
   curl -s --noproxy '*' 'http://127.0.0.1:5000/api/news/<LIST_RETURNED_KEY>' | python3 -c "..."

4. 确认content_ja > 0后，用此key做PUT入库
```

## 典型场景（7/11）

| 素材 | query.sh输出key | list端点正确key | 
|------|:---------------:|:---------------:|
| 板野友美亲子story | c25c31971a7ab866...47eef888... | c25c31971a7ab86647eef888ca9f00fd2396810a |
| 中村丽乃音乐剧 | d2dcf1ec... (截断) | d2dcf1ec101ff370117578ced422e3dc291c1c8f |
| ぼっちぼろまるstory | 0c297256... (截断) | 0c297256ee4834415b9492961d7ffbe253a84cc5 |

## 相关参考

- `7-8-api-key-list-vs-search-mismatch.md` — search vs list key后缀不同
- `6-28-api-key-suffix-mismatch.md` — 最早的关键字后缀不一致记录
- SKILL.md「无声失败速查」表
