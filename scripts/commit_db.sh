#!/bin/bash
# 每小时本地滚动备份：保留当前 + 上一个版本
# 用法: bash scripts/commit_db.sh "备注信息"

set -e

MSG="${1:-data: 滚动备份}"
DB="data/news_dev.db"
BACKUP_DIR="${DB_BACKUP_DIR:-$HOME/db-backup}"
BACKUP_FILE="$BACKUP_DIR/news_dev.db"
PREV_FILE="$BACKUP_DIR/news_dev.db.prev"
TMP="$BACKUP_DIR/.tmp/news_dev_clean_$(date +%s).db"

mkdir -p "$BACKUP_DIR/.tmp"

echo "📦 dump/restore 优化中..."
sqlite3 "$DB" ".dump" | sqlite3 "$TMP"

echo "🔍 验证..."
ROWS=$(sqlite3 "$TMP" "SELECT COUNT(*) FROM news;")
OK=$(sqlite3 "$TMP" "PRAGMA integrity_check;")
echo "  news=$ROWS 完整性=$OK"
if [ "$OK" != "ok" ] || [ "$ROWS" -eq 0 ]; then
    echo "❌ 验证失败，取消备份"
    rm -f "$TMP"
    exit 1
fi

# 比对内容 hash，没变化也滚动（保证 prev 不丢）
OLD_HASH=$(sqlite3 "$DB" "SELECT * FROM news;" 2>/dev/null | shasum | cut -d' ' -f1)
NEW_HASH=$(sqlite3 "$TMP" "SELECT * FROM news;" | shasum | cut -d' ' -f1)

if [ "$OLD_HASH" = "$NEW_HASH" ]; then
    echo "⏭️  DB 内容无变化，但仍然滚动备份保证 prev"
else
    # 替换 DB 文件（优化后的版本）
    cp "$TMP" "$DB"
fi
rm -f "$TMP"

# 滚动备份：当前 → prev，新版本 → 当前
if [ -f "$BACKUP_FILE" ]; then
    # 比较备份和当前是否相同，相同则不滚动（避免 prev == current）
    if ! cmp -s "$BACKUP_FILE" "$DB"; then
        mv "$BACKUP_FILE" "$PREV_FILE"
    fi
fi
cp "$DB" "$BACKUP_FILE"

echo "✅ 滚动备份完成"
echo "  当前版本: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
[ -f "$PREV_FILE" ] && echo "  上版本:   $PREV_FILE ($(du -h "$PREV_FILE" | cut -f1))"
