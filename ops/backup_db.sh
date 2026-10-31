#!/bin/bash
# 每日备份：VACUUM INTO 安全快照（不碰线上文件，对并发写安全）
# 2026-10-31 起：每天一次、保留最近 2 份（原为每小时 + 24/7/3 分层，占 12G）
set -euo pipefail
DB="${SQLITE_PATH:-/home/user/PG/XiaohongshuSkills/data/news_dev.db}"
DEST="${DB_BACKUP_DIR:-/backup}"
mkdir -p "$DEST/daily"
STAMP=$(date +%Y%m%d)
TMP="$DEST/daily/news_dev-$STAMP.db"

# 当天已备份过就跳过（cron 一天只跑一次，这里是重入保护）
[ -e "$TMP" ] && { echo "backup skip: $TMP 已存在"; exit 0; }

sqlite3 "$DB" "VACUUM INTO '$TMP'"
[ "$(sqlite3 "$TMP" 'PRAGMA integrity_check;')" = "ok" ] || { echo "integrity FAIL"; rm -f "$TMP"; exit 1; }
[ "$(sqlite3 "$TMP" 'SELECT COUNT(*) FROM news;')" -gt 0 ] || { echo "empty FAIL"; rm -f "$TMP"; exit 1; }

# 保留最近 2 份（约 0.8G；今天 + 昨天）
ls -1t "$DEST/daily"/news_dev-*.db 2>/dev/null | tail -n +3 | xargs -I{} rm -f {} 2>/dev/null || true

# 清掉旧的分层目录（hourly/weekly）
rm -rf "$DEST/hourly" "$DEST/weekly" 2>/dev/null || true

echo "backup ok: $TMP（$(du -h "$TMP" | cut -f1)）"
