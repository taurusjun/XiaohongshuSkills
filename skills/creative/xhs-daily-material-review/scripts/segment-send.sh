#!/bin/bash
# 分段发送 review 内容到 Telegram（代理方式，调用 Python 实现）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec python3 "$SCRIPT_DIR/segment-send.py" "$@"
