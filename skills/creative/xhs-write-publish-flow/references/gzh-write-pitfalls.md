# 公众号入库常见错误（6/21新增）

## Pitfall 1：用了 rewritten_ 字段而不是 wechat_ 字段

**症状：**
API返回 `{"ok": true}`，但检查DB发现公众号内容写进了 `rewritten_title` / `rewritten_content`，而 `wechat_title` / `wechat_content` 为空。

这样会导致两个问题：
1. channel=gzh 的稿件混入小红书发布队列读到的 `rewritten_` 字段
2. 公众号发布脚本优先读 `wechat_` 字段，读完为空发现没有内容

**根因：**
小红书写稿用 `rewritten_title` + `rewritten_content`，公众号也用了同样的字段名。习惯性复用payload结构，忘记公众号有独立字段。

**正确做法：**
```python
# 公众号 — 用 wechat_ 字段
payload = {
    'wechat_title': '公众号标题',
    'wechat_content': '公众号正文（Markdown）',
    'channel': 'gzh',
    'preselected': 0,
    'wechat_publish': 0,
}

# 小红书 — 用 rewritten_ 字段
payload = {
    'rewritten_title': '小红书标题',
    'rewritten_content': '小红书正文',
    'publish_mode': 'rewritten',
    'preselected': 1,
}
```

**验证方法：**
```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT wechat_title, LENGTH(wechat_content), LENGTH(rewritten_content), channel FROM news WHERE key='<key>'"
# wechat_ 字段应有值，rewritten_content 应为 0
```

**修复（如果已经写错）：**
```bash
curl -s -X PUT -H "Content-Type: application/json" \
  -d '{"rewritten_title":"","rewritten_content":"","en_title":"","en_content":"","en_tweet":""}' \
  "http://127.0.0.1:5000/api/news/<完整40位key>"
```
