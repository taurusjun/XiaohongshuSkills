#!/bin/bash
# 每周「保真度审计」（宿主 LaunchAgent 触发）：在容器里跑 ops/fidelity_audit.py → 落报告 → 有违规则飞书告警。
# 目的：让「规则实现了但实现错了/断了」这类漂移由**数据**发现，而不是靠人想。
set -u
DOCKER=/usr/local/bin/docker
NAME=xhs
REPO=/Users/user/PG/xhs-docker-src
LOG="$REPO/logs/fidelity_audit.log"
OUTDIR=/Users/user/PG/xhs-storage/data/audits
TODAY=$(date '+%F')
OUT="$OUTDIR/$TODAY-fidelity.md"
mkdir -p "$REPO/logs" "$OUTDIR"
log() { echo "$(date '+%F %T') $*" >>"$LOG"; }

res=$("$DOCKER" exec "$NAME" bash -lc 'cd /home/user/PG/XiaohongshuSkills && .venv/bin/python ops/fidelity_audit.py --days 3' 2>&1)
rc=$?
log "==== 审计 rc=$rc ===="
echo "$res" >>"$LOG"
{ printf '# 保真度审计 %s\n\n```\n' "$TODAY"; printf '%s\n' "$res"; printf '```\n'; } > "$OUT"
log "报告: $OUT"

if [ "$rc" != "0" ]; then
  msg="⚠️ xhs 保真度审计发现违规（$TODAY）@ $(hostname)
$(printf '%s' "$res" | grep -E '⚠️' | head -6)
报告: $OUT"
  python3 "$REPO/ops/notify_feishu.py" "$msg" >>"$LOG" 2>&1 || log "飞书告警失败"
  exit 1
fi
exit 0
