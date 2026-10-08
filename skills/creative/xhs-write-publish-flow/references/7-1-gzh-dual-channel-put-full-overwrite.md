# 公众号双通道API PUT全量覆盖陷阱（7/1实证）

## 场景

同一DB key已有 `rewritten_title` / `rewritten_content`（小红书版），需要再写 `wechat_title` / `wechat_content`（公众号版）。

## 错误的做法（会清空小红书字段）

```bash
# 第一步：写小红书版 ✅
curl -s -X PUT -H "Content-Type: application/json" --noproxy "*" \
  -d '{"rewritten_title":"XHS标题","rewritten_content":"XHS正文..."}' \
  "http://127.0.0.1:5000/api/news/<key>"

# 第二步：写公众号版 ❌ —— 这会清空 rewritten_ 字段！
curl -s -X PUT -H "Content-Type: application/json" --noproxy "*" \
  -d '{"wechat_title":"GZH标题","wechat_content":"GZH正文...","channel":"gzh"}' \
  "http://127.0.0.1:5000/api/news/<key>"
```

**为什么？** API PUT 是全量替换，不是增量合并。第二个PUT只传了 wechat_字段，其他字段被默认为空。

## 正确做法：合并payload

```python
import json, urllib.request

key = '...完整40位key...'

# 1. 读当前state
with urllib.request.urlopen(f'http://127.0.0.1:5000/api/news/{key}') as r:
    d = json.loads(r.read())

# 2. 读公众号draft
with open('/tmp/gzh_draft.md') as f:
    lines = f.read().strip().split('\n')
gzh_title = lines[0].replace('## ','').strip()
gzh_body = '\n'.join(lines[1:]).strip()

# 3. 合并payload：保留现有字段，追加公众号字段
merged = {
    'rewritten_title': d.get('rewritten_title', ''),
    'rewritten_content': d.get('rewritten_content', ''),
    'publish_mode': d.get('publish_mode', 'rewritten'),
    'preselected': 1,
    'wechat_title': gzh_title,
    'wechat_content': gzh_body,
    'channel': 'gzh',
    'wechat_publish': 0,
}

# 4. 写文件再curl
with open('/tmp/merged_payload.json', 'w') as f:
    json.dump(merged, f, ensure_ascii=False)
```

## 验证方法

```bash
python3 -c "
import json, urllib.request
with urllib.request.urlopen(f'http://127.0.0.1:5000/api/news/{key}') as r:
    d = json.loads(r.read())
print(f'rewritten: {len(d.get(\"rewritten_content\",\"\"))} chars')
print(f'wechat:    {len(d.get(\"wechat_content\",\"\"))} chars')
print(f'channel:   {d.get(\"channel\",\"\")}')
# 两个都应有值
"
```

## 备用方案：sqlite3直接写公众号字段

当API PUT行为不确定时，用sqlite3直接UPDATE公众号字段，不影响rewritten_字段：

```bash
python3 -c "
title = open('/tmp/gzh_draft.md').readline().strip()
body = ''.join(open('/tmp/gzh_draft.md').read().split('\\n')[1:]).strip()
safe_t = title.replace(\"'\", \"''\")
safe_b = body.replace(\"'\", \"''\")
print(f\"UPDATE news SET wechat_title='{safe_t}', wechat_content='{safe_b}', channel='gzh', wechat_publish=0 WHERE key='<完整key>';\")
" > /tmp/update_gzh.sql

sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db < /tmp/update_gzh.sql
```

## 教训来源

7/1 session：写了769字xhs版 → 写了1591字公众号版 → 差点用第二个PUT清空已有数据。通过先读当前state+合并payload的方式成功保留双通道数据。
