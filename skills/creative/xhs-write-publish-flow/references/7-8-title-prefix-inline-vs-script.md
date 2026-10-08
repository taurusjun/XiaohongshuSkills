# 标题 `#` 前缀陷阱 — 内联入库 vs 脚本修复（7/8第3次踩坑）

## 问题

写稿skill入库步骤要求从draft.md第一行提取标题为`rewritten_title`。典型内联代码：

```python
lines = open('/tmp/draft.md').read().strip().split('\n')
title = lines[0].replace('## ', '', 1).strip()  # ❌ 只处理双井号
```

**致命问题：** 只有当draft第一行是 `## 标题`（双井号）时才生效。如果第一行是 `# 标题`（单井号），`replace('## ', '', 1)` 不匹配，`# ` 保留在入库标题中。

## 根因

写稿时可能用 `#` 或 `##` 写标题，取决于模板。每次写稿前不知道会用哪个。

## 修复方法

### 正确内联写法

```python
lines = open('/tmp/draft.md').read().strip().split('\n')
title = lines[0].strip()
while title.startswith('#'):
    title = title.lstrip('#').strip()
body = '\n'.join(lines[1:]).strip()
```

### 或者用稳健的rstrip

```python
title = lines[0].strip().lstrip('#').strip()
```

### 完成后验证

```python
# 入库后立即验证
d = json.loads(urllib.request.urlopen(f'http://127.0.0.1:5000/api/news?limit=200').read())
for r in d.get('rows',[]):
    if r['key'] == key:
        rt = r.get('rewritten_title','')
        assert not rt.startswith('#'), f"标题含#前缀: {rt}"
```

## 本次会话踩坑记录

- 今田美樱ECMO稿：`replace('## ', '', 1)` 不处理 `# `，标题存为 `#今田美樱...`
- 龟梨SHOGUN稿：同样问题
- 虎面人引退稿：同样问题

## 写入参考

write-api.py中已有 `strip_md_title()` 函数统一处理行首#，但内联python入库时一直没用这个函数。如果用write-api.py脚本入库就没有此问题。如果内联入库，必须用 `lstrip('#')` 替代 `replace('... ', '')`。
