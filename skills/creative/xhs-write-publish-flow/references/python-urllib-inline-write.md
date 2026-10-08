# Python urllib 内联写稿模式（6/27验证）

## 为什么用这个模式

SOCKS5代理环境（`http_proxy=socks5h://127.0.0.1:10090`）下，curl 到 127.0.0.1:5000 会被代理劫持（Empty reply / HTTP 000）。用 `ALL_PROXY="" python3 -c` 加 urllib 可以完全绕过代理，且比 curl + JSON payload 文件方案更简洁。

## 写稿入库单条

```python
ALL_PROXY="" python3 -c "
import json, urllib.request

# 读取draft（第一行=标题，之后=正文）
draft = open('/tmp/xhs_draft_xxx.md').read().strip()
lines = draft.split(chr(10))
title = lines[0].replace('# ', '').strip()      # 去除 '# ' 前缀（write-api.py会保留 ## 前缀，需要手动修复）
body = chr(10).join(lines[1:]).strip()

data = {
    'rewritten_title': title,
    'rewritten_content': body,
    'related_keys': '关联key1,关联key2',  # 可选
    'publish_mode': 'rewritten',
    'preselected': 1
}

payload = json.dumps(data).encode('utf-8')
req = urllib.request.Request(
    'http://127.0.0.1:5000/api/news/<完整40位key>',
    data=payload,
    headers={'Content-Type': 'application/json'},
    method='PUT'
)
resp = urllib.request.urlopen(req, timeout=10)
print(resp.read().decode())
"
```

## 入库后立即验证（三步合一步）

```python
ALL_PROXY="" python3 -c "
import urllib.request, json, math

key = '<完整40位key>'
resp = urllib.request.urlopen(f'http://127.0.0.1:5000/api/news/{key}', timeout=10)
d = json.loads(resp.read())
rc = d.get('rewritten_content','') or ''
rt = d.get('rewritten_title','') or ''
ja = d.get('content_ja','') or ''
pre = d.get('preselected',0)
rel = d.get('related_keys','')

# 计算xhs字数
cjk = sum(1 for c in rc if '\u4e00'<=c<='\u9fff' or '\u3000'<=c<='\u303f' or '\uff00'<=c<='\uffef')
body_xhs = math.ceil(cjk + (len(rc)-cjk)*0.5)
density = body_xhs / max(len(ja), 1) * 100

print(f'标题: {rt}')
print(f'正文: {len(rc)} chars / {body_xhs} xhs字')
print(f'密度: {density:.1f}%  {\"✅\" if density >= 30 else \"❌\"}')
print(f'preselected: {pre} | related_keys: {rel}')
print(f'正文不以标题开头: {\"✅\" if not rc.startswith(rt[:20]) else \"❌\"}')
"
```

## 短news review（不强制8分）

短news（正文<700字）不跑5维度评分，只检查：密度≥30% + 正文不以标题开头 + preselected正确。通过即可入库。

## 长文story review（强制8分）

长文（正文≥700字 + ##分段）跑完整5维度评分：爆发点/情绪价值/信息增量/内容深度/标题吸引力 各2分。≥8分通过。

## 批量操作技巧

- 不要复用 payload 文件（6/24教训：共用文件导致覆盖前一条内容）
- 每个 key 在循环内重建 data dict，不走文件
- review 可以一条语句完成（入库+验证+评分合并），不需要分3步
- 用 `import math` 计算 xhs_len，用 `chr(10)` 替代 `\n` 在 shell 字符串中避免转义问题
