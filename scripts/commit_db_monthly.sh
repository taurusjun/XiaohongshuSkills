#!/bin/bash
# 每月 1 日用 git 分片 push DB 到 GitHub（绕过 100MB 限制）
# 策略：把 DB 切成 < 100MB 的分片，commit 到 db-backup 分支
#        每次用 git commit-tree 重建单个 commit，历史永远只 1 条
# 用法: bash scripts/commit_db_monthly.sh

set -e

DB="data/news_dev.db"
PART_PREFIX="data/news_dev.db.part."
CHUNK_SIZE="90m"
BRANCH="db-backup"
REMOTE="origin"
TMP_DIR="$HOME/db-backup/.tmp/monthly-$(date +%s)"

if [ ! -f "$DB" ]; then
    echo "❌ DB 不存在: $DB"
    exit 1
fi

mkdir -p "$TMP_DIR"
trap "rm -rf $TMP_DIR" EXIT

cd "$(git rev-parse --show-toplevel)"

# 1. 切分 DB 到临时目录
echo "📦 切分 DB 为 ${CHUNK_SIZE} 分片..."
split -b "$CHUNK_SIZE" "$DB" "$TMP_DIR/part."
PARTS=( "$TMP_DIR"/part.* )
if [ ${#PARTS[@]} -eq 0 ]; then
    echo "❌ 切分失败"
    exit 1
fi
echo "  共 ${#PARTS[@]} 个分片: $(du -h "$TMP_DIR"/part.* | awk '{print $1}' | tr '\n' ' ')"

# 2. 构造 tree：只含分片文件，无父 commit（孤立 commit）
# 用 plumbing 命令构造孤立 commit，避免影响工作树
echo "🏗️  构造孤立 commit..."
# 清空 index，只 add 分片文件
git rm -r --cached --quiet "$PART_PREFIX"* 2>/dev/null || true

# 移除 index 中所有现有条目（不影响工作树），只构造一个含分片的 tree
INDEX_FILE="$TMP_DIR/index"
export GIT_INDEX_FILE="$INDEX_FILE"
git read-tree --empty
for part in "${PARTS[@]}"; do
    base=$(basename "$part")
    hash=$(git hash-object -w "$part")
    git update-index --add --cacheinfo 100644 "$hash" "${PART_PREFIX}${base#part.}"
done

# 用 commit-tree 创建孤立 commit（无父 commit）
TREE=$(git write-tree)
TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
COMMIT_MSG="data: 每月 DB 备份 ($TIMESTAMP) - ${#PARTS[@]} 分片"
COMMIT_HASH=$(git commit-tree "$TREE" -m "$COMMIT_MSG")
unset GIT_INDEX_FILE

echo "  孤立 commit: $COMMIT_HASH"

# 3. 更新 db-backup 分支指向新 commit（force 覆盖旧分支）
git branch -f "$BRANCH" "$COMMIT_HASH"
echo "  分支 $BRANCH 已更新"

# 4. force push 到远程
echo "📤 推送到远程..."
if git push --force "$REMOTE" "$BRANCH" 2>&1 | tee /tmp/db-push.log; then
    echo "✅ 完成"
else
    echo "❌ push 失败，查看 /tmp/db-push.log"
    exit 1
fi
