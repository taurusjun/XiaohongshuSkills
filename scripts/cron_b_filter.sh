#!/bin/bash
set -euo pipefail

DB="/Users/user/PG/XiaohongshuSkills/data/news_dev.db"

KEYS=$(sqlite3 "$DB" "SELECT key FROM news WHERE en_content IS NOT NULL AND en_content != '' AND (en_tweet IS NULL OR en_tweet = '');" 2>/dev/null)

if [ -z "$KEYS" ]; then
  echo "__NONE__"
  exit 0
fi

echo "$KEYS"
