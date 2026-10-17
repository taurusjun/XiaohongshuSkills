#!/bin/bash
# 每小时清理宿主主 Chrome(9222) 累积的空转页面/Service Worker。
# 参考容器 ops/restart_main_chrome.sh，但宿主有两处必须不同：
#   1) 容器版 pkill 后靠 cdp_keeper(60s 巡检) 自动重拉；宿主 keeper 是 300s，
#      所以这里 pkill 后**立刻 kickstart keeper**，避免最多 5 分钟空窗。
#   2) 必须让 **GUI 域** 的 keeper 去拉 Chrome —— 从本脚本自己的会话直接起 Chrome
#      拿不到 macOS Keychain 的 Chrome Safe Storage 密钥 → cookies 只能放内存
#      （cookies 表 0 行）→ 重启就丢登录。（2026-10-17 实测确认）
# 只匹配 9222，不影响代理 Chrome(9223)。
# 护栏：webapp 有任务在跑（抓取/发布/其他）时跳过本次，避免打断任务
#      （容器 fetch 用独立的 9333 Chrome，宿主 fetch 用的就是 9222，所以宿主更需要这道护栏）。
set -u
LOG="$HOME/.hermes/logs/restart-main-chrome.log"
mkdir -p "$(dirname "$LOG")"
log() { echo "$(date '+%F %T') $*" >>"$LOG"; }

# ── 护栏：有任务在跑就跳过 ──
busy=$(curl -s --max-time 8 http://127.0.0.1:5000/api/active-tasks 2>/dev/null)
if [ -n "$busy" ] && printf '%s' "$busy" | /usr/bin/python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)
sys.exit(0 if (d.get("active") or d.get("fetch_running") or d.get("publish_running")) else 1)
' 2>/dev/null; then
  log "跳过：有任务在跑 -> $busy"
  exit 0
fi

if pkill -f "[r]emote-debugging-port=9222"; then
  log "killed main chrome(9222)"
else
  log "no main chrome(9222)"
fi
sleep 2
launchctl kickstart -k gui/501/com.xhs.cdp-keeper 2>>"$LOG"

for i in $(seq 1 20); do
  sleep 2
  if nc -z -w1 127.0.0.1 9222 2>/dev/null; then
    log "✅ 9222 已重拉（约 $((i*2))s）"
    exit 0
  fi
done
log "⚠️ 9222 在 40s 内未回来，交给 keeper 下一轮兜底"
exit 1
