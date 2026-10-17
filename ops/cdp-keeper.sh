#!/bin/bash
# CDP 保活（launchd：开机拉起 + 每 5 分钟兜底）—— **宿主版模板**
#
# ⚠️ 两个关键点（2026-10-17 排查确认，缺一不可）：
#   1) plist 必须带 AbandonProcessGroup=true —— 否则 launchd 会在本任务退出时
#      **杀掉它拉起的 Chrome**（症状：keeper 日志写 alive=1，但端口随即又关闭）。
#   2) 必须带 CHROME_PROFILE_DIR（宿主登录态在 "Profile 1"，而 Local State.last_used
#      可能是 Default）—— 否则 Chrome 开到没有登录态的空 profile。
#   3) 还必须由 **GUI 会话**（本 LaunchAgent）拉起：从 SSH 会话直接起 Chrome 拿不到
#      macOS Keychain 的「Chrome Safe Storage」密钥 → cookies 只能放内存（cookies 表 0 行）
#      → 一重启就丢登录。（容器是 Linux，不用 Keychain，所以没这个问题。）
##   9222 = 无代理 Chrome，小红书发布用（有登录态，绝不主动重启）
#   9223 = 带代理 Chrome，IG/Twitter/图集抓取用
# ensure_chrome / ensure_proxy_chrome 都是幂等的：端口已开就直接返回，
# 不会踢掉正在跑的 Chrome，所以定时执行是安全的。

PROJ="$HOME/PG/XiaohongshuSkills"
export CHROME_PROFILE_DIR="Profile 1"   # 宿主登录态在 Profile 1（多 profile，不指定会开到 last_used）
LOG="$HOME/.hermes/logs/cdp-keeper.log"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

port_open() { nc -z -w1 127.0.0.1 "$1" 2>/dev/null; }

before_9222=0; before_9223=0
port_open 9222 && before_9222=1
port_open 9223 && before_9223=1

# ── 标签清理（参考容器 ops/cdp_keeper.py 的 _de_explore）——放在早退之前，保证每次都跑 ──
#   ① /explore（视频流，持续烧 CPU）→ 导航到创作者首页
#   ② 关掉多余的 chrome://newtab/ · about:blank（至少保留 1 个 page，否则会关掉 Chrome）；
#      只剩空白页时 → 把它导航到创作者首页（而不是留一个 newtab）
if [ "$before_9222" = 1 ]; then
"$PROJ/.venv/bin/python3" - <<'PYCLEAN' 2>/dev/null
import json, urllib.request
BASE = "http://127.0.0.1:9222"
try:
    tabs = json.load(urllib.request.urlopen(f"{BASE}/json", timeout=5))
except Exception:
    raise SystemExit
pages = [t for t in tabs if t.get("type") == "page"]
DEST = "https://creator.xiaohongshu.com/new/home"
for t in pages:
    if "www.xiaohongshu.com/explore" in (t.get("url") or ""):
        try:
            import websocket
            ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=5)
            ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": DEST}}))
            ws.recv(); ws.close()
        except Exception:
            pass
blanks = [t for t in pages if (t.get("url") or "") in ("chrome://newtab/", "about:blank")]
keep = 1 if len(pages) - len(blanks) == 0 else 0
for t in blanks[keep:]:
    try:
        urllib.request.urlopen(f"{BASE}/json/close/{t['id']}", timeout=5)
    except Exception:
        pass
if keep and blanks:  # 只剩空白页时，把它导航到创作者首页（而不是留 newtab）
    try:
        import websocket
        ws = websocket.create_connection(blanks[0]["webSocketDebuggerUrl"], timeout=5)
        ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": DEST}}))
        ws.recv(); ws.close()
    except Exception:
        pass
PYCLEAN
fi

# 两个都在跑就直接退出，不产生日志噪音
if [ "$before_9222" = 1 ] && [ "$before_9223" = 1 ]; then
  exit 0
fi

cd "$PROJ" || { log "项目目录不存在: ${PROJ}"; exit 1; }

out=$("$PROJ/.venv/bin/python3" -c "
import sys
sys.path.insert(0, 'scripts')
from chrome_launcher import ensure_chrome, ensure_proxy_chrome
print('9222', ensure_chrome())
print('9223', ensure_proxy_chrome())
" 2>&1)

after_9222=0; after_9223=0
port_open 9222 && after_9222=1
port_open 9223 && after_9223=1

[ "$before_9222" = 0 ] && log "9222 缺失 -> 拉起结果 alive=${after_9222}"
[ "$before_9223" = 0 ] && log "9223 缺失 -> 拉起结果 alive=${after_9223}"

if [ "$after_9222" = 0 ] || [ "$after_9223" = 0 ]; then
  log "拉起失败，launcher 输出: $(echo "$out" | tr '\n' ' ' | tail -c 300)"
fi
