# segment-send.py Timeout Fallback (7/14经验)

## 问题现象

```bash
python3 ~/.hermes/skills/creative/xhs-daily-material-review/scripts/segment-send.py <存档>
```

输出：
```
⚠️ 发送失败: <urlopen error [Errno 61] Connection refused>
⚠️ 段 1 发送失败
[Command timed out after 60s]
```

## 根因

segment-send.py 的 `send_msg()` 使用两阶段策略：
1. **直连**：unset 所有 HTTP_PROXY 环境变量后尝试（timeout=30s）
2. **代理 fallback**：使用 `socks5://127.0.0.1:<port>` 重试（timeout=20s）

如果直连不通（本环境无直连Telegram路线）且代理端口错误或代理不可用，
两阶段合计最多占用 50s，加上 fix_tables 的开销，总运行时间可能超过 60s 超时阈值。

进一步：当 `os.environ.pop()` 清除代理环境变量后，urllib 可能对 `socks5://` 协议
处理异常，导致 opener 请求在等待阶段自行超时——而不是报错后立即 fallback。

## 应对方案：手动内联发送

当 segment-send.py 超时/失败时，用内联 Python 直接发送：

```python
python3 -c "
import urllib.request, json, re, os, time
from pathlib import Path

# 清除代理
for k in ['HTTP_PROXY','http_proxy','HTTPS_PROXY','https_proxy','ALL_PROXY','all_proxy']:
    os.environ.pop(k, None)

# ⚠️ 代理端口从环境变量获取（7/20修正：不要硬编码，检查 $http_proxy）
import subprocess
result = subprocess.run(['echo', '$http_proxy'], capture_output=True, text=True, shell=True)
proxy_url = result.stdout.strip()
if not proxy_url:
    # fallback 到默认端口
    proxy_url = 'socks5://127.0.0.1:20808'
print(f'Using proxy: {proxy_url}')

proxy_handler = urllib.request.ProxyHandler({
    'https': proxy_url,
    'http': proxy_url
})
opener = urllib.request.build_opener(proxy_handler)

# 读取配置
cfg = Path('/Users/user/.hermes/config.yaml').read_text()
TOKEN = re.search(r'bot_token:\s*(\S+)', cfg).group(1)

# 从 cron/jobs.json 获取 chat_id
raw = json.loads(Path('/Users/user/.hermes/cron/jobs.json').read_text())
jobs = raw if isinstance(raw, list) else raw.get('jobs', [])
chat_id = ''
for j in jobs:
    if isinstance(j, dict):
        o = j.get('origin')
        if isinstance(o, dict) and o.get('chat_id'):
            chat_id = o['chat_id']; break

def send_msg(text):
    text = text.replace('\\x00', '')[:4000]
    payload = json.dumps({
        'chat_id': chat_id,
        'rich_message': {'markdown': text}
    }).encode()
    req = urllib.request.Request(
        f'https://api.telegram.org/bot{TOKEN}/sendRichMessage',
        data=payload,
        headers={'Content-Type': 'application/json'}
    )
    opener.open(req, timeout=20)

# 分段发送（按行追加，超3500字切段）
today = __import__('datetime').datetime.now().strftime('%Y-%m-%d')
content = Path(f'/Users/user/.hermes/daily-reviews/{today}.md').read_text()
lines = content.split('\n')
current = ''
seg = 0
for line in lines:
    test = current + '\n' + line if current else line
    if len(test) <= 3500:
        current = test
    else:
        seg += 1; send_msg(current); print(f'📤 段 {seg} OK')
        current = line; time.sleep(1)
if current:
    seg += 1; send_msg(current); print(f'📤 段 {seg} OK')
print(f'✅ 共 {seg} 段发送完成')
"
```

---

## 应对方案：手动内联发送（curl 版）⭐ 推荐（7/28验证通过）

> **8/5 更新：** 更好的做法是直接运行 `scripts/segment-send-curl-fallback.py <存档路径>` —— 它用 curl + **sendRichMessage**（rich_message.markdown），**保留 Markdown 表格渲染**；本节的 sendMessage + parse_mode=Markdown 版会把表格退化为纯文本，仅当 sendRichMessage 不可用时才用。两者都用 socks5h 代理，端口从 `echo $http_proxy` 动态读取。

**推荐原因：** Python 版依赖 `socks` 包（本环境未安装，`import socks` → ModuleNotFoundError），而 curl 原生支持 SOCKS5。7/28 使用 curl 成功发送 2 段（message_id: 16318-16319）。

### 步骤

#### 1. 确认代理端口

```bash
echo $http_proxy
# → socks5h://127.0.0.1:20808（本例）
```

#### 2. 确认连通性

```bash
curl -s -x socks5h://127.0.0.1:$PORT --connect-timeout 5 --max-time 10 \
  "https://api.telegram.org/bot$(grep bot_token ~/.hermes/config.yaml | awk '{print $2}')/getMe"
# 应返回 {"ok":true,...}
```

#### 3. 发送（Python 生成 → curl 投递）

```python
python3 << 'PYEOF'
import os, re, subprocess, json

# 读取存档
archive_path = os.path.expanduser('~/.hermes/daily-reviews/YYYY-MM-DD.md')
with open(archive_path, 'r') as f:
    content = f.read()

# 获取 token 和 chat_id
TOKEN = re.search(r'bot_token:\s*(\S+)',
    open(os.path.expanduser('~/.hermes/config.yaml')).read()).group(1)
chat_id = ''
for m in re.finditer(r'"chat_id"\s*:\s*"([^"]+)"',
    open(os.path.expanduser('~/.hermes/cron/jobs.json')).read()):
    chat_id = m.group(1)
    break

MAX_CHARS = 3800

# ⚠️ 转义 bare _ 为 \_（Telegram Markdown 解析报错）
def escape_md(text):
    result = []
    i = 0
    while i < len(text):
        if text[i:i+2] == '**':
            result.append('**')
            i += 2
        elif text[i] == '_':
            result.append('\\_')
            i += 1
        else:
            result.append(text[i])
            i += 1
    return ''.join(result)

# 分段
segments = []
current = ''
for line in content.split('\n'):
    if len(current) + len(line) + 1 > MAX_CHARS and current:
        segments.append(current.strip())
        current = line + '\n'
    else:
        current += line + '\n'
if current.strip():
    segments.append(current.strip())

for i, seg in enumerate(segments):
    header = f"Title ({i+1}/{len(segments)})\n\n"
    payload = escape_md(header + seg)

    payload_json = json.dumps({
        "chat_id": chat_id,
        "text": payload,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    })

    # 写临时文件 → curl --data-binary @file
    tmpfile = f"/tmp/telegram_seg_{i+1}.json"
    with open(tmpfile, 'w') as f:
        f.write(payload_json)

    cmd = [
        "curl", "-s", "-x", "socks5h://127.0.0.1:PUT_PORT_HERE",
        "--connect-timeout", "10", "--max-time", "30",
        "-X", "POST",
        f"https://api.telegram.org/bot{TOKEN}/sendMessage",
        "-H", "Content-Type: application/json",
        "--data-binary", f"@{tmpfile}"
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
    os.unlink(tmpfile)

    if '"ok":true' in result.stdout:
        print(f"  Segment {i+1} sent!")
    else:
        print(f"  Segment {i+1}: {result.stdout[:200]}")
        # Fallback: send without parse_mode
        payload_json2 = json.dumps({
            "chat_id": chat_id, "text": payload,
            "disable_web_page_preview": True
        })
        with open(tmpfile, 'w') as f:
            f.write(payload_json2)
        result2 = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        os.unlink(tmpfile)
        if '"ok":true' in result2.stdout:
            print(f"  sent (no markdown)!")
        else:
            print(f"  failed: {result2.stdout[:200]}")
print("Done!")
PYEOF
```

### curl 版 vs Python urllib 版对比

| 方面 | Python urllib 版 | curl 版 |
|------|-----------------|---------|
| 代理支持 | 需 `import socks`（未安装→ConnectionRefused） | 原生 SOCKS5 支持 |
| Markdown 转义 | 需 `escape_md()` | 需 `escape_md()` |
| payload 传递 | 字符串拼接 | `--data-binary @file` 可靠 |
| API 方法 | `sendRichMessage` | `sendMessage` |
| 7/28验证 | 未测试 | ✅ 成功（message_id 16318-16319） |

## 关键参数

| 参数 | 值 | 说明 |
|------|-----|------|
| 代理端口 | 20808 | **当前环境实际 SOCKS5 端口（7/28确认：`$http_proxy=socks5h://127.0.0.1:20808`）。端口不定期漂移，每次 fallback 前先 `echo $http_proxy` 确认。** |
| chat_id | 从 cron/jobs.json 自动获取 | 或硬编码 8066707199 |
| max_chars | 3500 | Telegram 上限 4000，留余量 |
| 段间 sleep | 1s | 避免 rate limit |
| API 方法 | sendRichMessage | 唯一支持 Markdown 表格的方法 |

## 验证

```bash
# 先确认代理能否连通 Telegram API
# ⚠️ 先 `echo $http_proxy` 确认当前端口（20808/20809/其他），不要硬编码
PORT=$(echo $http_proxy | sed 's/.*://; s/[^0-9]//g')
curl -s --max-time 15 -x socks5h://127.0.0.1:$PORT \
  "https://api.telegram.org/bot$(grep bot_token ~/.hermes/config.yaml | awk '{print $2}')/getMe"
# 返回 {"ok":true,...} 即正常
```

## ⚠️ 第三层 Fallback：代理 SSL 不可达（7/22新增）

### 现象

```bash
# 端口正确，SOCKS5 连接成功
PORT=$(echo $http_proxy | sed 's/.*://; s/[^0-9]//g')
curl -v --max-time 20 -x socks5h://127.0.0.1:$PORT "https://api.telegram.org/bot.../getMe"
# → SOCKS5 connect to api.telegram.org:443 (remotely resolved)
# → SOCKS5 request granted.
# → TLS handshake... SSL_ERROR_SYSCALL / EOF in violation of protocol
```

### 诊断流程

1. `echo $HTTP_PROXY` — 确认当前代理端口
2. `nc -z -w2 127.0.0.1 <port>` — 确认代理端口存活
3. `curl -v --max-time 20 -x socks5h://127.0.0.1:<port> "https://api.telegram.org/bot$(grep bot_token ~/.hermes/config.yaml | awk '{print $2}')/getMe" 2>&1 | grep -E "SSL|error|granted"` — 确认失败位置

如果 `SOCKS5 request granted` 之后出现 SSL 错误，说明是代理出口 TLS 阻断（而非端口问题）。

### 最终 Fallback：存档内容直接作为 cron 输出

当以下条件**全部**成立时，所有 Telegram 发送路径均已耗尽：
1. segment-send.py 失败（`socks` 包缺失或代理端口不匹配）
2. 手动内联 Python 失败（SSL握手错误或包缺失）
3. curl 直连超时（无直接 Telegram 路由）
4. curl 通过代理也 SSL 失败（代理出口无法路由 Telegram）

**此时存档内容以 cron 最终响应直接输出**——Hermes 的定时任务系统会自动将 final response 投递到用户的 Telegram（通过系统底层的 deliver 机制，不依赖 segment-send.py）。存档仍然写入 `~/.hermes/daily-reviews/YYYY-MM-DD.md`，审计仍需通过（但是否发送成功不影响审计PASS/FAIL判定）。

### 三阶段 fallback 总览

| 阶段 | 方法 | 典型失败原因 | 下一步 |
|------|------|------------|--------|
| 1️⃣ | `segment-send.py` 直接调用 | `socks` 包缺失 + 代理端口不匹配(20808 vs 20809) | curl 手动发送 |
| 2️⃣ | 手动内联 Python（正确端口+socks5://） | SSL 握手失败(代理出口限制) | curl 验证 |
| 3️⃣ | curl 通过代理 + 直连均失败 | 无可用 Telegram 路由 | 存档作为 cron 最终输出 |

## ⚙️ 2026-07-31 成功例：curl `--data-binary @-` + stdin pipe + sendRichMessage

### 状況
- segment-send.py 失败（Connection Refused — `socks` 包未安装）
- curl 手动发送 `sendRichMessage` 成功，3 段全部 HTTP 200

### 実行コマンド（Python 生成 → curl 投递）

```python
import json, subprocess, time
from pathlib import Path

TOKEN = '...'        # grep bot_token ~/.hermes/config.yaml | awk '{print $2}'
CHAT_ID = '8066707199'
PROXY = 'socks5h://127.0.0.1:20808'

content = Path('~/.hermes/daily-reviews/YYYY-MM-DD.md').expanduser().read_text()
lines = content.split('\n')

# 分段（3000 chars/段）
segments = []
current = ''
for line in lines:
    test = current + '\n' + line if current else line
    if len(test) <= 3000:
        current = test
    else:
        segments.append(current)
        current = line
if current:
    segments.append(current)

for i, seg in enumerate(segments):
    payload = json.dumps({
        'chat_id': CHAT_ID,
        'rich_message': {'markdown': seg.replace('\x00', '')[:4000]}
    }, ensure_ascii=False)

    cmd = [
        'curl', '-x', PROXY,
        '-s', '-o', '/dev/null', '-w', '%{http_code}',
        '-X', 'POST',
        f'https://api.telegram.org/bot{TOKEN}/sendRichMessage',
        '-H', 'Content-Type: application/json',
        '--data-binary', '@-'
    ]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                         stderr=subprocess.PIPE, text=True)
    stdout, stderr = p.communicate(input=payload, timeout=30)
    status = stdout.strip()
    print(f'📤 Segment {i+1}/{len(segments)}: HTTP {status}')
    time.sleep(1)
```

**关键区别 vs. 参考文档中的 curl 版（sendMessage）：**
- 使用 `sendRichMessage`（支持表格）而非 `sendMessage`
- 使用 `--data-binary @-` 从 stdin 读取 payload（无需写临时文件）
- payload 用 `ensure_ascii=False` 保留中文/日文
- **不需要** escape_md() 转义下划线—sendRichMessage 的 markdown 字段在 Telegram 客户端内部渲染，不经过 parse_mode 解析，不触发 italic 标记错误
- 使用 `-s -o /dev/null -w %{http_code}` 获取 HTTP 状态码而非解析 JSON response

### 效果确认
- 2026-07-31: 3/3 段全部 HTTP 200 成功投递
- 2026-08-04: 4/4 段全部 HTTP 200 成功投递（proxy 20808、chat 8066707199、存档 12822 chars→4段）——curl+sendRichMessage+stdin 方案连续两轮验证通过，为首选 fallback

---

## 前置条件

- 脚本已硬编码正确代理端口（当前：`$http_proxy=socks5h://127.0.0.1:20808`，已从 7/21 的 20809 漂移回来）
- **⚠️ 代理端口漂移风险（7/20验证，7/21再验，7/28再验）：** 端口不定期漂移。
  - 7/20: 20809
  - 7/21: 20809
  - **7/28: 20808**（已漂移回来）
  - **8/4: 20808**（连续沿用，两轮确认）
  - 应对：每次 fallback 时先 `echo $http_proxy` 确认当前实际端口
- **⚠️ `socks` Python 包未安装（7/28发现）：** segment-send.py 使用 `urllib.request.build_opener(ProxyHandler(...))` + `socks5://` 协议，但这需要 `import socks`（PySocks）。本环境无此包，因此脚本的代理 fallback 总是报 Connection Refused——无论端口是否正确。这是比端口漂移更根本的失败根因。
  - 修复方向：安装 `socks` 包或改用 `curl` 替代 urllib 发送
  - 当前变通：使用 curl 手动发送（见下方「手动内联发送（curl 版）」）
- **⚠️ Telegram Markdown 实体解析失败（7/28发现）：** 手动发送时如果内容包含 bare `_`（如下划线在 `Fetch_by分布`、`rewritten_title` 等字段名中），Telegram 会将其解析为 italic 标记，报错 `Can't find end of the entity`。必须在发送前将 bare `_` 转义为 `\_`，同时保护 `**` 标记不被破坏。
- **⚠️ segment-send.py 计数 bug（7/28发现）：** 脚本在每段发送失败后仍输出 `✅ 共 N 段发送完成`——它统计的是「尝试了的段数」而不是「成功发送的段数」。如果看到此输出，仍需检查实际是否投递成功。
- bot_token 从 config.yaml 读取（已有）
- **⚠️ bot_token 正则提取陷阱（7/31发现）：** `re.search(r'bot_token:\s*(\S+)', cfg_text).group(1)` 可能捕获错误 token！`\S+` 匹配到非空白字符序列，但 YAML 值的边界在某些 shell/encoding 环境下可能被误识别——7/31 实际捕获到 `...AAF9Ut_PBYRTQPEsfGxgAGkmAVxEj1w4jIw`（错误）而非正确的 `...AAHDRYvCHKJEPwV7feMzuTdadTVbI3_4jIw`。**推荐使用 `grep bot_token ~/.hermes/config.yaml | awk '{print $2}'` 替代 Python 正则提取**，已在 curl 验证步骤中验证可用。
- chat_id 优先用 cron/jobs.json 的 origin.chat_id
- **7/22新增验证：** 即使代理端口正确（20809），也可能出现 `SSL: UNEXPECTED_EOF_WHILE_READING` 或 `SSL_ERROR_SYSCALL` 错误（curl verbose 显示「SOCKS5 request granted → TLS handshake failed」）。这意味着代理本身无法路由 HTTPS 到 Telegram API。这是完全不同的故障模式——不是端口错误，而是**代理出口限制**。
