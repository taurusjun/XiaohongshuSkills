#!/bin/bash
# 看门狗（launchd 每 120s 跑一次，独立于 gateway 进程）
#  职责 1：VeloceMac 代理端口漂移时，让所有揣着旧端口的东西跟上
#          - 改写 hermes plist + xhs .env
#          - 重载 gateway
#          - 重启 webapp（它在 import 时读一次 .env，且图集下载是进程内线程）
#          - 杀掉 9223 代理 Chrome（--proxy-server 是启动参数，改不了），由 cdp-keeper 用新端口拉起
#  职责 2：gateway 进程死了就拉起
# 说明：VeloceMac 的 Mixed 端口是"自动检测"的，GUI 不可手动锁定，会在 20808/20809 之间漂移。
# 注意：日志里变量后面不要紧跟中文标点，bash 会把多字节首字节当成变量名的一部分。

LOG="$HOME/.hermes/logs/watchdog.log"
PLIST="$HOME/Library/LaunchAgents/ai.hermes.gateway.plist"
XHS_ENV="$HOME/PG/XiaohongshuSkills/scripts/.env"
NAYOUT_LOG="$HOME/Library/Group Containers/group.com.nayout.app/Library/Logs/nayout.log"
KEEPER="$HOME/cdp-keeper.sh"
PENDING="$HOME/.hermes/logs/.webapp-restart-pending"
SVC="gui/$(id -u)/ai.hermes.gateway"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >> "$LOG"; }

gateway_alive() { ps aux | grep "gateway run" | grep -v grep > /dev/null; }
job_loaded()    { launchctl list 2>/dev/null | grep -q 'ai\.hermes\.gateway'; }

probe_port() {
  nc -z -w1 127.0.0.1 "$1" 2>/dev/null || return 1
  curl -s --max-time 6 -x "socks5h://127.0.0.1:$1" -o /dev/null https://www.baidu.com 2>/dev/null
}

# webapp 是否空闲：无进程内任务、无抓取、无发布
webapp_idle() {
  local js
  js=$(curl -s --max-time 8 http://127.0.0.1:5000/api/active-tasks 2>/dev/null) || return 1
  [ -n "$js" ] || return 1
  echo "$js" | python3 -c "
import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    sys.exit(1)          # 解析不了 -> 当作忙，宁可不重启
busy = bool(d.get('active')) or d.get('fetch_running') or d.get('publish_running')
sys.exit(1 if busy else 0)
" 2>/dev/null
}

# 空闲才重启 webapp；忙就留个标记，后续轮次继续尝试
try_restart_webapp() {
  if webapp_idle; then
    launchctl kickstart -k "gui/$(id -u)/com.xhs.webapp" 2>/dev/null
    rm -f "$PENDING"
    log "webapp 已重启（读取新端口）"
  else
    if [ ! -f "$PENDING" ]; then
      log "webapp 有任务在跑，暂不重启，挂起待办"
      touch "$PENDING"
    fi
  fi
}

# 9223 是抓取用、无登录态，杀掉重拉安全；9222 有小红书登录态，绝不碰
recycle_proxy_chrome() {
  if nc -z -w1 127.0.0.1 9223 2>/dev/null; then
    pkill -f 'remote-debugging-port=9223' 2>/dev/null
    sleep 2
    log "已回收 9223 代理 Chrome"
  fi
  [ -x "$KEEPER" ] && bash "$KEEPER" >/dev/null 2>&1
}

reload_gateway() {
  launchctl bootout "$SVC" 2>/dev/null
  for _ in $(seq 1 20); do
    job_loaded || break
    sleep 1
  done
  for attempt in 1 2 3; do
    err=$(launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>&1)
    sleep 3
    if job_loaded; then
      log "gateway 重载成功 (attempt ${attempt})"
      return 0
    fi
    log "gateway bootstrap 第 ${attempt} 次失败: ${err:-未知错误}"
    sleep 5
  done
  log "gateway 重载彻底失败 — 需要人工介入"
  return 1
}

# ── 找当前真实代理端口 ──
live_port=""
cand=$(grep 'mixed-port' "$NAYOUT_LOG" 2>/dev/null | tail -1 | grep -oE '[0-9]{4,5}$' | tr -d '[:space:]')
if [ -n "$cand" ] && probe_port "$cand"; then
  live_port="$cand"
else
  for p in $(seq 20800 20820); do
    if probe_port "$p"; then live_port="$p"; break; fi
  done
fi

cur=$(grep -oE '127\.0\.0\.1:[0-9]+' "$PLIST" 2>/dev/null | head -1 | cut -d: -f2)

# ── 容器 .env 同步（无条件执行；端口不一致才改 + 重启容器内 webapp）──
#   容器内代理地址是 VM 网关形式 192.168.5.2:PORT（bind mount 的 scripts/.env）
XHS_ENV_C="$HOME/PG/xhs-docker-src/scripts/.env"
if [ -n "$live_port" ] && [ -f "$XHS_ENV_C" ]; then
  old_c=$(grep -oE '192\.168\.5\.2:[0-9]+' "$XHS_ENV_C" 2>/dev/null | head -1 | cut -d: -f2)
  if [ -n "$old_c" ] && [ "$old_c" != "$live_port" ]; then
    sed -i '' "s|192\.168\.5\.2:$old_c|192.168.5.2:$live_port|g" "$XHS_ENV_C"
    # cron 任务每次新起进程会自动读；webapp 是常驻，需重启
    /usr/local/bin/docker exec xhs bash -lc \
      'supervisorctl -c /opt/ops/supervisord.conf restart webapp' >/dev/null 2>&1
    log "容器 .env 端口同步: ${old_c} -> ${live_port}（已重启容器 webapp）"
  fi
fi

# ── 职责 1：端口漂移 ──
if [ -n "$live_port" ] && [ -n "$cur" ] && [ "$live_port" != "$cur" ]; then
  log "代理端口漂移: ${cur} -> ${live_port} ，开始改写配置"
  sed -i '' "s|127\.0\.0\.1:$cur|127.0.0.1:$live_port|g" "$PLIST"
  [ -f "$XHS_ENV" ] && sed -i '' "s|127\.0\.0\.1:$cur|127.0.0.1:$live_port|g" "$XHS_ENV"
  reload_gateway
  recycle_proxy_chrome
  try_restart_webapp
  exit 0
fi

# 上一轮因为忙而没重启的 webapp，这轮继续尝试
[ -f "$PENDING" ] && try_restart_webapp

# ── 职责 2：gateway 活着吗 ──
gateway_alive && exit 0

log "gateway 已死 — 尝试拉起"
if job_loaded; then
  launchctl kickstart -k "$SVC" 2>/dev/null
  sleep 5
else
  reload_gateway
fi

if gateway_alive; then
  log "gateway 已复活"
else
  log "拉起失败 — gateway 仍然死着"
fi
