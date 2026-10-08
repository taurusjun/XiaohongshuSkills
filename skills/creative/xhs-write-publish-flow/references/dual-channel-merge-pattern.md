# 双通道稿件合并入库模式

## 场景

同一素材需要同时出小红书版 + 公众号版，写入同一条DB记录的不同字段：

| 渠道 | 字段 | preselected | channel |
|------|------|-------------|---------|
| 小红书 | `rewritten_title` + `rewritten_content` | `1` | `''` |
| 公众号 | `wechat_title` + `wechat_content` | `0` | `gzh` |

## API PUT 是全覆盖，不是增量

`PUT /api/news/<key>` 的 body 是所有字段的全量更新。**如果你先PUT小红书字段，再PUT公众号字段，第二个PUT会把第一个清空**，除非你在第二个PUT里也带上第一个的字段值。

## 正确方法一：合并payload（推荐 — 7/5验证）

先读当前状态，合并为一个payload一次PUT：

```python
import urllib.request, json

key = '<完整40位key>'

# 1. 读当前状态
req = urllib.request.Request(f'http://127.0.0.1:5000/api/news/{key}')
with urllib.request.urlopen(req) as resp:
    current = json.loads(resp.read().decode())

# 2. 读取公众号稿
draft_path = '/tmp/gzh_draft.md'
with open(draft_path) as f:
    lines = f.read().strip().split('\n', 1)
    gzh_title = lines[0].replace('# ', '').strip()
    gzh_body = lines[1].strip() if len(lines) > 1 else ''

# 3. 构建合并payload
payload = {
    'channel': 'gzh',
    'wechat_title': gzh_title,
    'wechat_content': gzh_body,
    'wechat_publish': 0,
    'preselected': 0,  # 公众号走wechat_publish控制
}

# 保留小红书字段不变
for field in ['publish_xhs', 'publish_mode', 'rewritten_title', 'rewritten_content', 'preselected']:
    if field in current:
        payload[field] = current[field]

# 4. 一次PUT
data = json.dumps(payload).encode('utf-8')
req2 = urllib.request.Request(
    f'http://127.0.0.1:5000/api/news/{key}',
    data=data,
    headers={'Content-Type': 'application/json'},
    method='PUT'
)
with urllib.request.urlopen(req2, timeout=10) as resp:
    print('OK:', resp.read().decode())

# 5. 验证
req3 = urllib.request.Request(f'http://127.0.0.1:5000/api/news/{key}')
with urllib.request.urlopen(req3) as resp3:
    d = json.loads(resp3.read().decode())
    print(f'xhs: {d.get("rewritten_title","")}')
    print(f'gzh: {d.get("wechat_title","")} (len={len(d.get("wechat_content",""))})')
    print(f'channel: {d.get("channel","")}')
```

**注意事项：**
- ⚠️ `ALL_PROXY=socks5h://...` 环境变量会劫持 Python `urllib.request`（报错 `unknown url type: socks5h`）。如果终端设了 `ALL_PROXY`，用 `ALL_PROXY=""` 前缀调用，或用 `--noproxy '*'` curl
- `preselected` 在两个渠道不同：小红书=1（待发），公众号=0（待发，由 wechat_publish 控制）
- 验证时必须确认小红书的rewritten_title未被覆盖

## 正确方法二：sqlite3 UPDATE（6/24方式）

写小红书字段用 API PUT，公众号字段用 sqlite3 DIRECT UPDATE：

```bash
# 第2步：sqlite3 直接写公众号字段（用文件传SQL避免shell转义）
python3 -c "
title = open('/tmp/gzh_draft.md').readline().strip()
body = ''.join(open('/tmp/gzh_draft.md').read().split('\\\\n')[1:]).strip()
safe_t = title.replace(\"'\", \"''\")
safe_b = body.replace(\"'\", \"''\")
print(f\"UPDATE news SET wechat_title='{safe_t}', wechat_content='{safe_b}', channel='gzh', wechat_publish=0 WHERE key='<完整key>';\")
" > /tmp/update_gzh.sql

sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db < /tmp/update_gzh.sql
```

## 验证

```bash
# API方式
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news/<key>" | python3 -c "
import json,sys; d=json.load(sys.stdin)
print(f'xhs: {d.get(\"rewritten_title\",\"\")}')
print(f'gzh: {d.get(\"wechat_title\",\"\")} (len={len(d.get(\"wechat_content\",\"\"))})')
print(f'channel: {d.get(\"channel\",\"\")}')
"
```

## 三个关键原则

1. **小红书字段和公众号字段独立** — 写入时互不干扰，发布时各自读各自的字段
2. **preselected 在两个渠道不同** — 小红书=1（待发），公众号=0（待发，由 wechat_publish 控制）
3. **同一key上两个版本共存** — 不占用两条记录，不污染对方队列

## 多素材聚合 → 公众号（7/5新增）

当同人物/同事件有大量素材（如龟梨和也·田中皆实共11条素材），需要聚合成一篇公众号长文时：

1. **筛选核心素材：** 从 search= 结果中找出 content_ja 非空的条目，按事件时间线排序
2. **重点读取长发素材：** 优先读 content_ja ≥ 1500字的深访/分析类素材，它们有更多背景信息
3. **找共同线索：** 跨素材搜索共同关键词/共同引语（如「何屋さん」跨素材出现→可以作为主题线索）
4. **结构：** 时间线叙事+人物对照+行业现象，不必紧扣单条素材的原文结构
5. **公众号review：** 必须过 去魅测试 + 背景锚定 + renwei 三维度
