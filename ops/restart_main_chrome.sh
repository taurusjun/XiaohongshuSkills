#!/bin/bash
# 每小时清理容器主 Chrome(9222) 累积的空转页面/Service Worker：
# 整组杀掉 9222 的 Chrome，cdp_keeper(每 60s 巡检) 会在端口关闭后自动重拉。
# 只匹配 9222，不影响 fetch 专用 Chrome(9333)/代理 Chrome(9223)。
pkill -f "[r]emote-debugging-port=9222" \
  && echo "$(date '+%F %T') killed main chrome(9222)" \
  || echo "$(date '+%F %T') no main chrome(9222)"
