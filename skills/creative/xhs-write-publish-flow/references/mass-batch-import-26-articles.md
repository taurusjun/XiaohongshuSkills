# 批量入库26篇——Python urllib 一次PUT完成（7/27验证）

## 背景

2026-07-27 session中，一次性入库26篇稿件（6篇story lf=1 + 15篇短news + 5篇A级news），全部通过一个Python heredoc脚本完成，0失败。

## 前置条件

- 所有draft存放在 `/tmp/<name>_draft.md`，第一行为 `## 标题`
- 已从 `GET /api/news?date_from=YYYY-MM-DD&limit=500` 拿到所有rows
- 已建立 `draft_name → 40位full_key` 的映射字典

## 关键技术点

### 1. Python heredoc 避免shell引号问题

```bash
ALL_PROXY="" python3 << 'PYEOF'
import urllib.request, json, os

# key map
key_map = { 'name': '40char_full_key', ... }

for name, key in key_map.items():
    draft_path = f'/tmp/{name}_draft.md'
    if not os.path.exists(draft_path):
        continue
    
    with open(draft_path) as f:
        content = f.read()
    
    lines = content.strip().split('\n')
    title = lines[0].strip().lstrip('#').strip()
    body = '\n'.join(lines[1:]).strip()
    
    payload = json.dumps({
        'rewritten_title': title,
        'rewritten_content': body,
        'publish_mode': 'rewritten',
        'preselected': 1,
        'publish_xhs': 0,
    }).encode('utf-8')
    
    req = urllib.request.Request(
        f'http://127.0.0.1:5000/api/news/{key}',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='PUT'
    )
    with urllib.request.urlopen(req, timeout=10) as r:
        resp = json.loads(r.read())
    
    if resp.get('ok'):
        ok += 1
        print(f'  OK {name}')
PYEOF
```

### 2. `ALL_PROXY=""` 防止SOCKS5劫持

始终在最外层带上 `ALL_PROXY=""`。不加会导致 `Empty reply from server`。

### 3. 标题提取安全写法

```python
lines[0].strip().lstrip('#').strip()
```

这个比 `.replace('## ', '', 1)` 安全——能处理 `# `、`## `、`### ` 等各种前缀。

### 4. 入库后验证方法

```python
import urllib.request, json
key = '40char_key'
url = f'http://127.0.0.1:5000/api/news/{key}'
with urllib.request.urlopen(url, timeout=10) as r:
    d = json.loads(r.read())
rc = d.get('rewritten_content','') or ''
rt = d.get('rewritten_title','') or ''
pre = d.get('preselected', 0)
print(f'rt={bool(rt)} rc={len(rc)}字 preselected={pre}')
```

验证要点：
- `rewritten_title` 非空
- `rewritten_content` 非空且字数合理
- `preselected=1`

### 5. 重写入库（覆盖已有rewritten_content）

```python
# 重写同一条key
same_payload = json.dumps({...}).encode('utf-8')
req = urllib.request.Request(f'http://127.0.0.1:5000/api/news/{key}', data=same_payload, ...)
```

PUT是全量覆盖，所以重新PUT会完全替换 `rewritten_title` 和 `rewritten_content`。重写后需重新跑renwei和review。

## 7/27实测数据

- 26篇全部 `{"ok": true}`
- 验证：所有 `rewritten_title` 非空，字数正确，`preselected=1`
- 0失败，0无声错误
- 单次脚本运行时间 < 5秒
