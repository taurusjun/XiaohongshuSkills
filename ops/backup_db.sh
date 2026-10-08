#!/bin/bash
# 分层备份：VACUUM INTO 安全快照（不碰线上文件，对并发写安全）+ 硬链接去重
set -euo pipefail
DB="${SQLITE_PATH:-/home/user/PG/XiaohongshuSkills/data/news_dev.db}"
DEST="${DB_BACKUP_DIR:-/backup}"
mkdir -p "$DEST/hourly" "$DEST/daily" "$DEST/weekly"
STAMP=$(date +%Y%m%d-%H%M)
TMP="$DEST/hourly/news_dev-$STAMP.db"

sqlite3 "$DB" "VACUUM INTO '$TMP'"
[ "$(sqlite3 "$TMP" 'PRAGMA integrity_check;')" = "ok" ] || { echo "integrity FAIL"; rm -f "$TMP"; exit 1; }
[ "$(sqlite3 "$TMP" 'SELECT COUNT(*) FROM news;')" -gt 0 ] || { echo "empty FAIL"; rm -f "$TMP"; exit 1; }

[ -e "$DEST/daily/news_dev-$(date +%Y%m%d).db" ] || cp -l "$TMP" "$DEST/daily/news_dev-$(date +%Y%m%d).db"
if [ "$(date +%u)" = "1" ]; then
  [ -e "$DEST/weekly/news_dev-$(date +%G-W%V).db" ] || cp -l "$TMP" "$DEST/weekly/news_dev-$(date +%G-W%V).db"
fi

ls -1t "$DEST/hourly"/news_dev-*.db 2>/dev/null | tail -n +25 | xargs -I{} rm -f {} 2>/dev/null || true
ls -1t "$DEST/daily"/news_dev-*.db  2>/dev/null | tail -n +8  | xargs -I{} rm -f {} 2>/dev/null || true
ls -1t "$DEST/weekly"/news_dev-*.db 2>/dev/null | tail -n +5  | xargs -I{} rm -f {} 2>/dev/null || true
echo "backup ok: $TMP"
