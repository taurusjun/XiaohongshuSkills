# 批量日文假名清理实战（7/14）

## 问题

15篇改写稿中，8篇含有大量日文假名（最高156个），引语中大量保留了日文原话未翻译。用户第4次纠正。

## 根因

每次写稿时，引语直接抄原文的日文台词，没有预先全部翻译为中文。「」引号内的日文保留被认为"不影响理解"，但实际上用户不接受。

## 批量清理流程

### 1. 批量检测

```python
import urllib.request, json

url = 'http://127.0.0.1:5000/api/news?date_from=2026-07-14&limit=200'
with urllib.request.urlopen(url, timeout=10) as r:
    d = json.loads(r.read())

for idx, row in enumerate(d['rows']):
    rc = row.get('rewritten_content','') or ''
    jap = sum(1 for ch in rc if '\u3040' <= ch <= '\u309f' or '\u30a0' <= ch <= '\u30ff')
    if jap > 5:
        print(f'[!] [{idx}] {row.get("title","")[:30]} — {jap} kana')
```

### 2. 需翻译的常见模式

| 模式 | 示例 | 替换为 |
|------|------|-------|
| 日文引语在「」中 | 「精神科に行くな」 | "不准去精神科" |
| 日文节目名 | 《それSnow Manにやらせて下さい》 | 《交给Snow Man去做吧》 |
| 日文专用句式 | さすが女優さんはお顔の汗をかかないんですね | 女演员果然不出汗啊 |
| 引语内の/を/が/に等 | 全量替换为中文 | — |
| 作品原名 | 《ワタシってサバサバしてるから》 | 《我这个人就是爽快型》（用中文译名或标注） |
| 短语气词残留 | ね、よ、わ | 删除，用中文语气替代 |

### 3. 替换顺序

1. 先批量替换「→" 和 」→"
2. 再逐句翻译日文引语为纯中文
3. 最后做日文假名计数确认

### 4. 验证命令

```bash
python3 -c "c=open('/tmp/draft.md').read(); print(sum(1 for ch in c if '\u3040'<=ch<='\u309f' or '\u30a0'<=ch<='\u30ff'))"
```

输出必须≤5。
