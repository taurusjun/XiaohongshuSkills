#!/bin/bash
# -*- coding: utf-8 -*-
# 逐人物发布状态聚合 —— 第2~3层「搁置信号」判定的取证脚本
#
# 用法:
#   bash person_pub_audit.sh 西畑大吾 土屋太凤 前田敦子 相川暖花
#   bash person_pub_audit.sh 石田千穗 石田千穂          # 简繁各传一个，合并看
#   NEWS_DB=/path/to/news_dev.db bash person_pub_audit.sh 前田敦子
#
# 输出: k|n|pub|lastpub  （入库数 / 已发布 / 最近发布时间）
#       + 全库按日发布量 + 定时队列（未来发布）
#
# 安全: 只复制 DB 副本到 /tmp 再查询，绝不写原库。末尾清理副本。
set -u

DB_SRC="${NEWS_DB:-/Users/user/PG/XiaohongshuSkills/data/news_dev.db}"

if [ ! -f "$DB_SRC" ]; then
  echo "DB not found: $DB_SRC" >&2
  echo "定位方法: lsof -p \$(pgrep -f 'web/app.py' | head -1) | grep cwd  →  <cwd>/data/news_dev.db" >&2
  exit 1
fi

TMP="/tmp/nd_audit_$$.db"
cp "$DB_SRC" "$TMP" || { echo "copy failed: $DB_SRC" >&2; exit 1; }
# WAL 库必须连 -wal/-shm 一起带，否则读到旧快照、看不到今日入库
[ -f "$DB_SRC-wal" ] && cp "$DB_SRC-wal" "$TMP-wal"
[ -f "$DB_SRC-shm" ] && cp "$DB_SRC-shm" "$TMP-shm"

echo "=== 全库按日发布量 (最近10天) ==="
sqlite3 "$TMP" "SELECT date(xhs_pub_time)||' : '||count(*) FROM news
  WHERE COALESCE(xhs_pub_time,'')!='' GROUP BY date(xhs_pub_time)
  ORDER BY date(xhs_pub_time) DESC LIMIT 10;"

echo "=== 定时发布队列 (xhs_pub_time > now，发布前0数据属预期，勿写「已发布」) ==="
sqlite3 "$TMP" "SELECT substr(key,1,12)||' | '||xhs_pub_time FROM news
  WHERE xhs_pub_time > datetime('now') ORDER BY xhs_pub_time LIMIT 10;"

echo "=== 逐人物 (k=关键词 n=入库数 pub=已发布 lastpub=最近发布) ==="
if [ "$#" -gt 0 ]; then
  Q=""
  for name in "$@"; do
    [ -z "$name" ] && continue
    [ -n "$Q" ] && Q="$Q UNION ALL "
    # 三字段 OR —— 只用 title LIKE 会系统性低估发布数（9/23 实测西畑大吾 61/0 vs 144/4）
    Q="$Q SELECT '$name' k, count(*) n, sum(CASE WHEN COALESCE(xhs_pub_time,'')!='' THEN 1 ELSE 0 END) pub, COALESCE(max(xhs_pub_time),'-') lastpub FROM news WHERE title LIKE '%$name%' OR content_ja LIKE '%$name%' OR rewritten_title LIKE '%$name%'"
  done
  if [ -n "$Q" ]; then
    sqlite3 -header -separator '|' "$TMP" "$Q"
    echo
    echo "判读: pub=0 或 lastpub 远早于今天 ⇒ 强搁置(降级/合并/跳过); pub 占比高 ⇒ 本批最高优先."
  fi
else
  echo "(未传人名，跳过逐人物查询)"
fi

rm -f "$TMP" "$TMP-wal" "$TMP-shm"
