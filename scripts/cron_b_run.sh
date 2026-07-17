#!/bin/bash
# 手动跑 Cron B — 英文评审+推文生成
# 用法: bash cron_b_run.sh
set -euo pipefail

DB="/Users/user/PG/XiaohongshuSkills/data/news_dev.db"
API="http://127.0.0.1:5000"

echo "=== Step 1: 过滤候选素材 ==="
KEYS=$(sqlite3 "$DB" "SELECT key FROM news WHERE en_content IS NOT NULL AND en_content != '' AND (en_tweet IS NULL OR en_tweet = '');" 2>/dev/null)

if [ -z "$KEYS" ]; then
  echo "没有候选素材"
  exit 0
fi

echo "找到以下候选素材:"
echo "$KEYS"
echo ""

echo "=== Step 2: 输出上下文给 LLM ==="
echo "以下内容复制出来，作为 Cron B 的输入上下文："
echo "===================================="
echo "$KEYS"
echo "===================================="
echo ""
echo "然后用 LLM 逐条处理（curl API 拿详情 -> 评审 -> 生成推文 -> PUT 写回）"
echo ""
echo "或者直接复制上面 key 列表到这条命令逐个处理："
echo ""
for KEY in $KEYS; do
  echo "curl -s \"$API/api/news/$KEY\" | python3 -c \"import sys,json; d=json.load(sys.stdin); print(d['title']); print('en_content len:', len(d.get('en_content',''))); print('en_tweet empty:', not d.get('en_tweet'))\""
done
