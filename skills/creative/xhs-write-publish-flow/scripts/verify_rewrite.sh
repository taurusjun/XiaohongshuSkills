#!/bin/bash
# verify_rewrite.sh <key>
# 验证稿件入库是否合规：标题是否混入正文、preselected/publish_xhs/publish_mode是否设好、related_keys是否写入
# 用法: bash scripts/verify_rewrite.sh <完整40位key>

API="http://127.0.0.1:5000"

if [ -z "$1" ]; then
  echo "用法: $0 <完整40位key>"
  exit 1
fi

KEY="$1"

echo "=== 入库验证 ==="
echo "Key: $KEY"
echo ""

RESP=$(curl -s "$API/api/news/$KEY")
echo "$RESP" | python3 -c "
import sys, json

d = json.load(sys.stdin)
rt = d.get('rewritten_title', '') or ''
rc = d.get('rewritten_content', '') or ''
ps = d.get('preselected')
px = d.get('publish_xhs')
pm = d.get('publish_mode', '') or ''
rk = d.get('related_keys', '') or ''
ch = d.get('channel', '') or ''

print(f'标题: {rt}')
print(f'正文字数: {len(rc)}')
print(f'preselected: {ps}')
print(f'publish_xhs: {px}')
print(f'publish_mode: {pm}')
print(f'channel: {ch}')
print(f'related_keys: {rk[:60]}')
print()

# 检查1: 正文是否以标题行开头
starts_with_heading = rc.startswith('#')
print(f'检查1 {\"❌ 正文以标题行开头\" if starts_with_heading else \"✅ 正文不以标题行开头\"}')

# 检查2: 标题是否混入了正文
prefix = rc[:len(rt)] if len(rc) >= len(rt) else rc
title_in_body = (prefix == rt)
print(f'检查2 {\"❌ 标题混入正文\" if title_in_body else \"✅ 正文开头不是标题\"}')

# 检查3: preselected
print(f'检查3 {\"✅ preselected=1\" if ps == 1 else \"⚠️ preselected=\"+str(ps)}')

# 检查4: publish_xhs
print(f'检查4 {\"✅ publish_xhs=1\" if px == 1 else \"⚠️ publish_xhs=\"+str(px)}')

# 检查5: publish_mode
print(f'检查5 {\"✅ publish_mode=\"+pm if pm else \"ℹ️ publish_mode=normal（默认）\"}')

# 检查6: 公众号稿channel
if ch == 'gzh':
    print(f'检查6 ✅ channel=gzh（公众号稿）')
elif ch == '':
    print(f'检查6 ℹ️ channel为空（默认小红书）')
else:
    print(f'检查6 ℹ️ channel={ch}')
"
