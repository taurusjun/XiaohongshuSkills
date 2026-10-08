# 内联Python入库：注意标题不要带`#`前缀

## 问题来源

当用Markdown写draft时，第一行用 `# 标题` 格式。然后在内联Python中这样读：

```python
with open('/tmp/xhs_draft.md') as f:
    lines = f.read().strip().split('\n')
title = lines[0]                    # ❌ "`# 龟梨和也结婚当爸...`" 
title = lines[0].replace('## ', '').strip()  # 这是错的！应该去除 '# ' 不是 '## '
```

## 正确写法

draft格式：第一行 `# 标题`，之后是正文（`## 小标题`分段）。

```python
with open('/tmp/xhs_draft.md') as f:
    lines = f.read().strip().split('\n')
title = lines[0].replace('# ', '').strip()     # ✅ 去掉 '# ' 前缀
body = '\n'.join(lines[1:]).strip()             # ✅ 保留 ## 小标题
```

注意区别：
- `# 标题`（全文标题）= 去除 `# `
- `## 小标题`（段落标题）= 保留在body中

## 入库后立即验证

```bash
curl -s --noproxy '*' http://127.0.0.1:5000/api/news/<KEY> | python3 -c "
import json,sys
d=json.load(sys.stdin)
print('TITLE:', repr(d.get('rewritten_title','')))
if d.get('rewritten_title','').startswith('#'):
    print('❌ 标题含#前缀！需要修复')
"
```

如果带 `#` 前缀，单独修复：
```python
python3 -c "
import urllib.request, json
key = '<完整40位key>'
data = json.dumps({'rewritten_title': '正确的标题（不含#号）'}).encode('utf-8')
req = urllib.request.Request(f'http://127.0.0.1:5000/api/news/{key}', data=data, headers={'Content-Type': 'application/json'}, method='PUT')
urllib.request.urlopen(req, timeout=10)
"
```
