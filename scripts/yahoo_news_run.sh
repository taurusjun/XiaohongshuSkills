#!/bin/bash
# 日本 Yahoo 新闻自动抓取 - 一键运行

cd "$(dirname "$0")/.."

echo "=========================================="
echo "🇯🇵 日本 Yahoo 新闻自动抓取"
echo "$(date '+%Y.%m.%d %H:%M')"
echo "=========================================="

# 确保 Chrome 已启动
if ! lsof -i :9222 > /dev/null 2>&1; then
    echo "📡 启动 Chrome..."
    python3 scripts/chrome_launcher.py
    sleep 3
else
    echo "✅ Chrome 已在运行"
fi

# 运行抓取
python3 scripts/yahoo_news_auto.py --push

echo ""
echo "✅ 完成！查看 Notion: https://www.notion.so/344aaa31a0aa8068b72cede158e88eb9"
