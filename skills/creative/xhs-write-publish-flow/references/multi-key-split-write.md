# 多key分拆写稿模式（6/27 影山优佳案例）

## 问题

当一组关联素材（同一个人的多个角度事件）放在一起写作时，如果所有素材合并后原文体量过大（8114 xhs_len字），单篇正文不可能达到30%密度。强行写一篇会导致密度<10%，review退回。

## 正确做法

**拆成多篇，用不同key，各自覆盖2-3条素材。**

### 拆法

1. 看所有关联素材的content_ja，找出素材的天然分组——按「角度」而不是按「时间」
2. 每组2-3条素材，保证每组原文去重后xhs_len x 30% ≤ 正文目标字数
3. 每组分配一个不同的key（通常用该组主要素材的key）
4. 各篇的related_keys分别关联自己用到的素材

### 6/27案例拆法

| 篇 | key | 素材 | 角度 |
|---|-----|------|------|
| P1 | 10f24526（主素材：14岁裁判证） | 14岁裁判证（1904字）+ 熬夜看球起低角度丑照 | 成长线+专业度 |
| P2 | 0c3f82e1（荷兰战） | 荷兰战グータッチ（824字）+ 声カス生日歌（673字） | 现场画面·荷兰战 |
| P3 | 4d18609d（梅西追星） | 梅西进球（1684字） | 粉丝moment·追星成功 |

### 验证

```python
import sys, urllib.request, json, re
sys.path.insert(0, '/path/to/scripts')
from xhs_word_count import xhs_len

keys = [组1key, 组2key, ...]
all_text = ''
for k in keys:
    d = json.loads(urllib.request.urlopen(f'http://127.0.0.1:5000/api/news/{k}').read())
    all_text += (d.get('content_ja','') or d.get('content','')) + '\n'

sents = [s.strip() for s in re.split(r'[。！？]', all_text) if s.strip()]
dedup = list(dict.fromkeys(sents))
dedup_xhs = xhs_len('。'.join(dedup))

body_xhs = xhs_len(正文)
density = body_xhs / dedup_xhs * 100
```

### 注意事项

- 不同key之间不要覆盖同一个rewritten字段（同一个key写多次=覆盖）
- P1用主素材key，P2用荷兰战key，P3用梅西key——各独立
- 各篇都要单独标记 preselected=1 + publish_xhs=1
