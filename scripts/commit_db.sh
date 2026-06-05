#!/bin/bash
# 安全提交 news_dev.db：dump/restore 后提交推送
# 用法: bash scripts/commit_db.sh "备注信息"

set -e

MSG="${1:-data: 更新 news_dev.db}"
DB="data/news_dev.db"
TMP="/tmp/news_dev_clean_$(date +%s).db"

echo "📦 dump/restore 中..."
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

cp "$TMP" "$DB"
rm -f "$TMP"

echo "📤 提交推送..."
git add -f "$DB"

if git diff --cached --quiet; then
    echo "⏭️  DB 无变化，跳过提交"
else
    git commit -m "$MSG"
    git push origin HEAD
    echo "✅ 完成"
fi
