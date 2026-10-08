# 批量跨时间查询模式（6/28实践）

## 问题

第3层跨时间关联需要为多个S/A人物/关键词各做一次全量DB查询。逐条curl 200条*N个关键词=重复拉取，效率低。

## 优化方法

一次拉全量，在Python中循环检查多个关键词：

```bash
# 一次拉200条全量，多关键词过滤
for kw in "柏木由纪" "须田亚香里" "高桥恭平" "横山裕"; do
  echo "=== Searching: $kw ==="
  curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?limit=200" | python3 -c "
import sys, json
d = json.load(sys.stdin)
kw = '$kw'
matches = [r for r in d['rows'] if kw in (r.get('title','') or '') or kw in (r.get('content_ja','') or '')]
for r in matches:
    rew = r.get('rewritten_title','') or '(无)'
    pub = r.get('xhs_pub_time','') or '(未发)'
    v = r.get('xhs_views',0) or 0
    likes = r.get('xhs_likes',0) or 0
    print(f'  {r[\"key\"][:12]} | created={r.get(\"created_at\",\"\")} | pub={pub} | v={v} | likes={likes} | rew={rew[:35]} | {r[\"title\"][:50]}')
if not matches:
    print('  (无记录)')
" 2>/dev/null
  echo
done
```

## 关键点

1. 每次curl都会拉全量200条——虽然冗余，但每个关键词的查询是独立的Python进程，不互相影响
2. `2>/dev/null` 可以静默pipe到python3的安全警告
3. 使用 `rewritten_title` 和 `xhs_pub_time` 判断旧记录是否已发布：
   - `pub=(未发)` + `rewritten=(无)` = 未处理的旧素材
   - `pub=2026-06-27 09:06` = 已发布，可参考数据
   - `pub=(未发)` + `rewritten=某标题` = 已写稿但未发布
4. 返回 `likes` (xhs_likes) 用于判断旧文章的实际表现

## 适用场景

- Layer 3对5-15个S/A人物做批量关联查询
- 主进程直做模式下，耗时约15-30秒（取决于关键词数量）
