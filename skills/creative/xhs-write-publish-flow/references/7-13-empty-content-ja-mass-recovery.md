# 批量内容获取：detail端点空content_ja的全量恢复经验（7/13）

## 问题表现

一次写稿session中，**5/5 key**的 `GET /api/news/<key>` 详情端点返回content_ja=null，但list端点的rows数据中有实际内容。

## 恢复模式

不要逐条从detail端点尝试（大概率全空），直接用list端点全量dump一次获取所有数据：

```bash
# 一次性获取今日所有素材的全量数据（含content_ja）
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=2026-07-13&limit=200" \
  > /tmp/full_list.json

# 从rows中按title搜索特定key，取content_ja
python3 -c "
import json
with open('/tmp/full_list.json') as f:
    d = json.load(f)
targets = ['樋口日奈', '若月佑美', '秋元真夏']
for r in d['rows']:
    for t in targets:
        if t in (r.get('title','') or ''):
            key = r['key']
            cj = r.get('content_ja','') or ''
            print(f'key={key} cj_len={len(cj)} title={r.get(\"title\",\"\")[:40]}')
            if cj:
                with open(f'/tmp/cj_{t}.txt', 'w') as f:
                    f.write(cj)
"
```

## 已验证的key差异模式

list端点返回的key与detail端点key可能不同。**始终用list端点rows里的key**：
- 读写都用list端点的key
- 不要跨session复用query.sh截断显示的短key
- 每次写稿前从list端点重新获取完整key

## 7/13验证结果

| 素材 | list端点cj_len | detail端点cj_len | 结论 |
|------|---------------|-----------------|------|
| 樋口日奈 main (10838...) | 1346 | 0 | list > detail |
| 若月佑美 (c81c...) | 845 | 0 | list > detail |
| 秋元真夏 (5dfc...) | 408 | 0 | list > detail |
| 白鸟沙怜 (767f...) | 1903 | 0 | list > detail |
| 原岚3人 (fea1...) | 869 | 0 | list > detail |
| 向井康二 (3f13...) | 2537 | 0 | list > detail |

100%命中率。说明detail端点的content_ja字段在当前API版本中**几乎不可用**。
