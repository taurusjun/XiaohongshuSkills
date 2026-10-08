# write-api.py title `# ` 前缀问题（7/5确认）

## 症状

入库后的 `rewritten_title` 以 `# ` 开头（如 `# 前田敦子35岁前夕：...`），而不是干净的标题。

## 根因

`write-api.py` 第46行：

```python
title_line = lines[0].replace('## ', '').strip()
```

Draft 文件第一行通常写 `# 标题`（单井号+空格，Markdown H1）。脚本只替换 `## `（双井号+空格），不替换 `# `（单井号+空格）。

- 如果 draft 写 `## 标题` → `replace('## ', '')` 生效 → `标题` ✅
- 如果 draft 写 `# 标题` → `replace('## ', '')` 不生效 → `# 标题` ❌

## 临时修复

入库后立即验证并用单独 PUT 修复：

```bash
# 验证
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news/<KEY>" | python3 -c "
import json,sys; d=json.load(sys.stdin)
print(f'has_hash: {d.get(\"rewritten_title\",\"\").startswith(\"# \")}')"

# 修复
python3 -c "
import urllib.request, json
key = '<完整40位key>'
data = json.dumps({'rewritten_title': '正确的标题（不含#号）'}).encode('utf-8')
req = urllib.request.Request(f'http://127.0.0.1:5000/api/news/{key}', data=data, headers={'Content-Type': 'application/json'}, method='PUT')
urllib.request.urlopen(req, timeout=10)
"
```

## 应改脚本（待执行）

`write-api.py` 第46行改为：

```python
title_line = lines[0].replace('## ', '').replace('# ', '', 1).strip()
```

`replace('# ', '', 1)` 用 count=1 避免误伤正文内容中可能出现的 `# `。
