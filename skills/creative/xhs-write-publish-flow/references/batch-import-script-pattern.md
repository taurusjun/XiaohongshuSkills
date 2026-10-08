# 批量入库三脚本模式（8/1验证，11篇批次）

≥5篇批量时，用三个独立 python 脚本替代逐篇手写 urllib PUT。
每篇以 `(完整40位key, draft文件, related_keys)` 元组驱动，避免截短key和shell引号问题。

## 脚本1：批量入库 /tmp/batch_import_<date>.py

```python
import urllib.request, json, time

# key -> (draft文件, related_keys字符串, 或'')
targets = [
    ('<完整40位key>', '/tmp/xhs_draft_xxx.md', ''),
    ('<完整40位key>', '/tmp/xhs_draft_yyy.md', '<关联key1>,<关联key2>'),
]

for key, draft_path, related in targets:
    with open(draft_path) as f:
        lines = f.read().split('\n')
    title_line = lines[0].strip().lstrip('#').strip()   # 第一行是##标题，去#（单#双#都去掉）
    content = '\n'.join(lines[1:]).strip()
    payload = {
        'rewritten_title': title_line,
        'rewritten_content': content,
        'publish_mode': 'rewritten',
        'preselected': 1,
        'publish_xhs': 0,
    }
    if related:
        payload['related_keys'] = related
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(f'http://127.0.0.1:5000/api/news/{key}',
                                 data=data, headers={'Content-Type': 'application/json'}, method='PUT')
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    print(f'{key[:8]} | {title_line[:30]} | {resp}')
    time.sleep(0.3)
```

## 脚本2：关联素材互设related_keys /tmp/batch_rel_<date>.py

单向PUT只设主稿的related_keys；关联素材自己也要回指主稿。
**先GET读当前state再合并，防覆盖已有related_keys：**

```python
import urllib.request, json, time

pairs = [
    ('<关联key>', '<主稿key>'),
]
for rel_key, main_key in pairs:
    with urllib.request.urlopen(f'http://127.0.0.1:5000/api/news/{rel_key}', timeout=15) as r:
        cur = json.loads(r.read())
    rel_list = [x.strip() for x in (cur.get('related_keys') or '').split(',') if x.strip()]
    if main_key not in rel_list:
        rel_list.append(main_key)
    payload = {'related_keys': ','.join(rel_list)}
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(f'http://127.0.0.1:5000/api/news/{rel_key}',
                                 data=data, headers={'Content-Type': 'application/json'}, method='PUT')
    with urllib.request.urlopen(req, timeout=15) as r:
        print(rel_key[:8], '|', json.loads(r.read()))
    time.sleep(0.3)
```

## 脚本3：批量密度 /tmp/batch_density_<date>.py

```python
import urllib.request, json, re, sys
sys.path.insert(0, '/Users/user/.hermes/skills/creative/xhs-write-publish-flow/scripts')
from xhs_word_count import xhs_content_len

targets = [
    ('<完整40位key>', '/tmp/xhs_draft_xxx.md', []),
    ('<完整40位key>', '/tmp/xhs_draft_yyy.md', ['<关联key1>']),
]
for key, draft_path, rel_keys in targets:
    body = open(draft_path).read()
    body_xhs = xhs_content_len(body)
    all_text = ''
    for k in [key] + rel_keys:
        with urllib.request.urlopen(f'http://127.0.0.1:5000/api/news/{k}', timeout=15) as r:
            d = json.loads(r.read())
        all_text += (d.get('content_ja','') or d.get('content','')) + '\n'
    sents = [s.strip() for s in re.split(r'[。！？]', all_text) if s.strip()]
    dedup = list(dict.fromkeys(sents))
    dedup_xhs = xhs_content_len('。'.join(dedup))
    density = body_xhs / dedup_xhs * 100 if dedup_xhs > 0 else 0
    print(f'{key[:8]} | body={body_xhs} | base={dedup_xhs} | density={density:.1f}%')
```

## 铁律（8/1验证）

1. key 一律从 `date_from=当天&limit=200` 的全量list端点取，完整40位，禁截短
2. 入库后必须 sqlite3 验证 rewritten_content 非空（11/11 OK 才算过）
3. 标题行去 # 用 `lines[0].strip().lstrip('#').strip()`（单#双#都覆盖）
4. 密度<30%时先判断关联素材是否预告/名单类 → 是则按 `multi-material-merge-density-edge-case.md` 主素材密度判定
