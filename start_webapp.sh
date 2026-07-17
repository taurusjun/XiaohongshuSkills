#!/bin/bash
# 防止休眠
/usr/bin/caffeinate -i -s &
CAFF_PID=$!
echo "caffeinate started: $CAFF_PID"

# 启动 webapp
exec /Users/user/PG/XiaohongshuSkills/.venv/bin/python web/app.py
