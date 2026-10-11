#!/bin/bash
# 每晚 23:00 重启 xhs 容器：健康检查（容器在跑 + UI 200）；失败重试 3 次；仍失败 → 飞书告警。
# 由 LaunchAgent com.xhs.container-restart-daily 触发。
set -u
DOCKER=/usr/local/bin/docker
NAME=xhs
UI=http://127.0.0.1:15000/
REPO=/Users/user/PG/xhs-docker-src
LOG="$REPO/logs/container_restart.log"
mkdir -p "$REPO/logs"

log() { echo "$(date '+%F %T') $*" >>"$LOG"; }

health() {
  [ "$("$DOCKER" inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = "true" ] || return 1
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "$UI" 2>/dev/null)
  [ "$code" = "200" ]
}

log "==== 每日重启开始 ===="
for i in 1 2 3; do
  log "第 $i/3 次：docker restart $NAME"
  "$DOCKER" restart "$NAME" >>"$LOG" 2>&1
  ok=0
  for _ in $(seq 1 18); do          # 最多等 ~90s 就绪
    sleep 5
    if health; then ok=1; break; fi
  done
  if [ "$ok" = "1" ]; then log "✅ 重启成功（第 $i 次）"; exit 0; fi
  log "⚠️ 第 $i 次后健康检查未通过"
  sleep 10
done

msg="🚨 xhs 容器每日重启失败：3 次均未通过健康检查 $(date '+%F %T') @ $(hostname)。请登录 $UI 检查。"
log "❌ 全部失败 → 发送飞书告警"
python3 "$REPO/ops/notify_feishu.py" "$msg" >>"$LOG" 2>&1
exit 1
