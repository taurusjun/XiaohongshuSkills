# 公众号稿写入DB操作流程（6/14新增，6/21更新）

## 背景

之前规则规定公众号稿不写入DB，只写到 `~/.hermes/workspace/`。6/14 session用户明确指示「公众号的内容也先写入DB」，统一管理以便后续排期发布。

## 字段设置

公众号文章使用独立的 `wechat_` 字段，不写入 `rewritten_title/content`：

| 字段 | 值 | 说明 |
|------|-----|------|
| `wechat_title` | 公众号标题 | 纯文本，去掉draft中的`##`前缀 |
| `wechat_content` | 公众号正文 | 去掉第一行标题后剩余部分，Markdown格式 |
| `channel` | `gzh` | 标记为公众号稿件 |
| `preselected` | `0` | 用户说发再设1 |
| `wechat_publish` | `0` | 0=未发 1=待发 |

**注意：** 公众号稿件不要写入 `rewritten_title` 和 `rewritten_content` 字段，避免与小红书发布队列混淆。小红书管道根据 `channel` 过滤，但保持字段干净更安全。

## 操作步骤

```bash
# 1. 从draft提取标题和正文
python3 -c "
lines = open('~/.hermes/workspace/公众号_主题.md').read().strip().split('\\n')
title = lines[0].replace('## ','').strip()
body = '\\n'.join(lines[1:]).strip()
import json
data = json.dumps({
    'wechat_title': title,
    'wechat_content': body,
    'channel': 'gzh',
    'preselected': 0,
    'wechat_publish': 0,
})
with open('/tmp/payload.json','w') as f: f.write(data)
"

# 2. 通过API PUT到已存在的素材key
curl -s -X PUT -H "Content-Type: application/json" \
  -d @/tmp/payload.json \
  "http://127.0.0.1:5000/api/news/<完整40位key>"

# 3. 验证
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT wechat_title, LENGTH(wechat_content), channel, wechat_publish FROM news WHERE key='<完整40位key>'"
```

## 注意事项

- channel=gzh 的稿件不会出现在小红书发布队列中（pipeline会过滤channel=xhs或空）
- 公众号稿只设preselected=0、wechat_publish=0，等用户确认发布时再改
- workspace保留一份纯文本副本作为本地查看、修改用
- DB内也必须有完整内容，以便后续的公众号发布pipeline读取
- **公众号稿不用跑小红书review**（review标准和渠道不同）
- **发布脚本 wechat_publisher.py 需要优先读 wechat_title/wechat_content，为空再fallback到 rewritten_title/rewritten_content**
