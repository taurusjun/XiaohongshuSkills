#!/bin/bash
# 发布互斥：若发布进行中则等待空闲（最多 ~10 分钟）
set -e
BASE="${XHS_WEBAPI_BASE:-http://127.0.0.1:5000}"
for _ in $(seq 1 60); do
  out=$(curl -s --noproxy '*' "$BASE/api/active-tasks" 2>/dev/null || true)
  if ! echo "$out" | grep -q '"publish_running"[[:space:]]*:[[:space:]]*true'; then
    exit 0
  fi
  sleep 10
done
exit 0
