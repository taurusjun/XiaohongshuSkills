#!/usr/bin/env python3
"""
segment-send-curl-fallback.py — segment-send.py 失败时的首选 fallback（8/5验证通过，3段全部 ok:true）

为什么存在：segment-send.py 的 Python urllib socks5 fallback 因 socks 包未安装必然失败
（无论代理端口是否正确），直连在 cron 环境也不通。curl 原生支持 SOCKS5，且 sendRichMessage
保留 Markdown 表格渲染（references/segment-send-timeout-fallback.md 里的 sendMessage 版
会把表格退化为纯文本——本脚本是更优选择）。

用法：
  python3 scripts/segment-send-curl-fallback.py <存档路径> [<max_chars>]

前置检查（代理端口会漂移：7/20:20809、7/28:20808、8/5:20808）：
  echo $http_proxy   # 确认当前端口
  curl -s -x socks5h://127.0.0.1:$PORT --connect-timeout 5 --max-time 12 \
    "https://api.telegram.org/bot$(grep bot_token ~/.hermes/config.yaml | awk '{print $2}')/getMe"
  # 返回 {"ok":true,...} 才继续；Connection refused 先查端口漂移

分段逻辑与 segment-send.py 相同：3500字符/段 + 表格边界保护（表格整体后移、不跨段切开）。
chat_id 从 ~/.hermes/cron/jobs.json 第一个 origin 提取；bot_token 从 ~/.hermes/config.yaml 读取。
"""
import json, os, re, subprocess, sys, time
from pathlib import Path

FILE = sys.argv[1]
MAX_CHARS = int(sys.argv[2]) if len(sys.argv) > 2 else 3500
PORT = os.environ.get('http_proxy', 'socks5h://127.0.0.1:20808').split(':')[-1]
PROXY = f"socks5h://127.0.0.1:{PORT}"

home = Path.home()
cfg_text = (home / '.hermes/config.yaml').read_text()
TOKEN = re.search(r'bot_token:\s*(\S+)', cfg_text).group(1)

raw = json.loads((home / '.hermes/cron/jobs.json').read_text())
jobs = raw if isinstance(raw, list) else raw.get('jobs', [])
chat_id = ''
for j in jobs:
    if isinstance(j, dict):
        o = j.get('origin')
        if isinstance(o, dict):
            cid = o.get('chat_id', '')
            if cid:
                chat_id = cid
                break
if not chat_id:
    print("❌ CHAT_ID 为空", file=sys.stderr)
    sys.exit(1)

print(f"chat_id={chat_id} proxy={PROXY}")


def curl_send(text: str) -> bool:
    text = text.replace('\x00', '')[:4000]
    payload = json.dumps({
        'chat_id': chat_id,
        'rich_message': {'markdown': text}
    }).encode('utf-8')
    tmpfile = f"/tmp/telegram_rich_seg_{int(time.time()*1000)}.json"
    with open(tmpfile, 'wb') as f:
        f.write(payload)
    cmd = [
        "curl", "-s", "-x", PROXY,
        "--connect-timeout", "10", "--max-time", "30",
        "-X", "POST",
        f"https://api.telegram.org/bot{TOKEN}/sendRichMessage",
        "-H", "Content-Type: application/json",
        "--data-binary", f"@{tmpfile}"
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=35)
        os.unlink(tmpfile)
        if '"ok":true' in r.stdout:
            return True
        print(f"⚠️ 响应异常: {r.stdout[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"⚠️ curl失败: {e}", file=sys.stderr)
        return False


content = Path(FILE).read_text()
lines = content.split('\n')
current = ''
segment_num = 0

for line in lines:
    test = current + '\n' + line if current else line
    if len(test) <= MAX_CHARS:
        current = test
    else:
        next_is_table = line.strip().startswith('|')
        current_lines_list = current.split('\n')
        current_last_table_line = -1
        for j in range(len(current_lines_list) - 1, -1, -1):
            if current_lines_list[j].strip().startswith('|'):
                current_last_table_line = j
                break
        current_has_table_end = current_last_table_line >= 0

        if current_has_table_end and next_is_table:
            table_first_row = -1
            for j in range(len(current_lines_list) - 1, -1, -1):
                cl = current_lines_list[j].strip()
                if not cl.startswith('|'):
                    table_first_row = j + 1
                    break
                if j == 0:
                    table_first_row = 0
            if table_first_row > 0:
                before_table = '\n'.join(current_lines_list[:table_first_row])
                table_part = '\n'.join(current_lines_list[table_first_row:])
                if before_table.strip():
                    if curl_send(before_table):
                        segment_num += 1
                        print(f"📤 段 {segment_num} OK（表格前内容）")
                    else:
                        print(f"⚠️ 段 {segment_num} 发送失败", file=sys.stderr)
                    time.sleep(1)
                current = table_part + '\n' + line
            else:
                if curl_send(current):
                    segment_num += 1
                    print(f"📤 段 {segment_num} OK（表格段）")
                else:
                    print(f"⚠️ 段 {segment_num} 发送失败", file=sys.stderr)
                time.sleep(1)
                current = line
        else:
            if curl_send(current):
                segment_num += 1
                print(f"📤 段 {segment_num} OK")
            else:
                print(f"⚠️ 段 {segment_num} 发送失败", file=sys.stderr)
            time.sleep(1)
            current = line

if current.strip():
    if curl_send(current):
        segment_num += 1
        print(f"📤 段 {segment_num} OK")
    else:
        print(f"⚠️ 段 {segment_num} 发送失败", file=sys.stderr)

print(f"✅ 共 {segment_num} 段发送完成")
