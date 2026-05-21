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
python3 - <<EOF
import sqlite3, os
db = sqlite3.connect("$TMP")
news = db.execute("SELECT COUNT(*) FROM news").fetchone()[0]
dims = db.execute("SELECT COUNT(*) FROM score_dims").fetchone()[0]
ok = db.execute("PRAGMA integrity_check").fetchone()[0]
print(f"  news={news} score_dims={dims} 完整性={ok}")
assert ok == "ok", "完整性检查失败！"
assert news > 0, "news 表为空！"
db.close()
EOF

cp "$TMP" "$DB"
rm -f "$TMP"

echo "📤 提交推送..."
git add "$DB"
git commit -m "$MSG"
git push origin xhs-smart-agent

echo "✅ 完成"
