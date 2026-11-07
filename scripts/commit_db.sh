#!/bin/bash
# 每小时本地滚动备份：保留当前 + 上一个版本
# 重要：绝不在库被其他进程打开时覆盖/替换 data/news_dev.db（会产生 stale WAL，
#       表现为 "database disk image is malformed"）。只生成一致性快照，不动原库。
# 用法: bash scripts/commit_db.sh "备注信息"

set -e

MSG="${1:-data: 滚动备份}"
DB="data/news_dev.db"
BACKUP_DIR="${DB_BACKUP_DIR:-$HOME/db-backup}"
BACKUP_FILE="$BACKUP_DIR/news_dev.db"
PREV_FILE="$BACKUP_DIR/news_dev.db.prev"
TMP="$BACKUP_DIR/.tmp/news_dev_clean_$(date +%s).db"

mkdir -p "$BACKUP_DIR/.tmp"

echo "📦 生成一致性快照（VACUUM INTO）..."
rm -f "$TMP"
if ! sqlite3 "$DB" "VACUUM INTO '$TMP'" 2>/dev/null; then
    # 老版本 SQLite 兜底：online backup，同样不动原库
    rm -f "$TMP"
    sqlite3 "$DB" ".backup '$TMP'"
fi

echo "🔍 验证..."
ROWS=$(sqlite3 "$TMP" "SELECT COUNT(*) FROM news;")
OK=$(sqlite3 "$TMP" "PRAGMA integrity_check;")
echo "  news=$ROWS 完整性=$OK"
if [ "$OK" != "ok" ] || [ "$ROWS" -eq 0 ]; then
    echo "❌ 验证失败，取消备份"
    rm -f "$TMP"
    exit 1
fi

# 滚动备份：内容没变就不滚动；变了则 当前 → prev，新快照 → 当前
if [ -f "$BACKUP_FILE" ]; then
    OLD_HASH=$(sqlite3 "$BACKUP_FILE" "SELECT * FROM news;" 2>/dev/null | shasum | cut -d' ' -f1)
    NEW_HASH=$(sqlite3 "$TMP" "SELECT * FROM news;" | shasum | cut -d' ' -f1)
    if [ "$OLD_HASH" = "$NEW_HASH" ]; then
        echo "⏭️  备份内容无变化，不滚动"
        rm -f "$TMP"
        echo "✅ 滚动备份完成"
        echo "  当前版本: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
        [ -f "$PREV_FILE" ] && echo "  上版本:   $PREV_FILE ($(du -h "$PREV_FILE" | cut -f1))"
        exit 0
    fi
    mv "$BACKUP_FILE" "$PREV_FILE"
fi
cp "$TMP" "$BACKUP_FILE"
rm -f "$TMP"

echo "✅ 滚动备份完成"
echo "  当前版本: $BACKUP_FILE ($(du -h "$BACKUP_FILE" | cut -f1))"
[ -f "$PREV_FILE" ] && echo "  上版本:   $PREV_FILE ($(du -h "$PREV_FILE" | cut -f1))"
