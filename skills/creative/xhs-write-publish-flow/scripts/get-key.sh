#!/bin/bash
# get-key.sh — 查完整40位key+当前改写状态+content_ja长度
# 用法: bash get-key.sh <关键词>
# 输出: 完整key | 标题前40字 | 改写状态 | content_ja长度 | preselected | publish_xhs | rewritten_content前50字
# content_ja长度=0说明没有抓到日文原文，不可用于写稿

KEYWORD="$1"
if [ -z "$KEYWORD" ]; then
  echo "用法: bash get-key.sh <关键词>"
  echo "示例: bash get-key.sh ラウール"
  echo "      bash get-key.sh 花田"
  echo "      bash get-key.sh 全部  # 显示最近50条未发布的新闻素材"
  exit 1
fi

DB=~/PG/XiaohongshuSkills/data/news_dev.db
LIMIT=20

if [ "$KEYWORD" = "全部" ]; then
  sqlite3 "$DB" "
    SELECT key, 
           substr(title,1,40),
           CASE WHEN rewritten_title != '' THEN '✅已写' ELSE '❌未写' END,
           length(content_ja),
           COALESCE(preselected,0),
           COALESCE(publish_xhs,0),
           substr(rewritten_content,1,50)
    FROM news 
    WHERE title != '' 
      AND (publish_xhs IS NULL OR publish_xhs = 0)
      AND status != 'archived'
    ORDER BY id DESC 
    LIMIT $LIMIT;
  " | column -t -s '|'
else
  sqlite3 "$DB" "
    SELECT key, 
           substr(title,1,40),
           CASE WHEN rewritten_title != '' THEN '✅已写' ELSE '❌未写' END,
           length(content_ja),
           COALESCE(preselected,0),
           COALESCE(publish_xhs,0),
           substr(rewritten_content,1,50)
    FROM news 
    WHERE title LIKE '%$KEYWORD%'
       OR key LIKE '%$KEYWORD%'
    ORDER BY id DESC 
    LIMIT $LIMIT;
  " | column -t -s '|'
fi
