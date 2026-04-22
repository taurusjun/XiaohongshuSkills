#!/usr/bin/env python3
"""
从 crypto.news 抓取最新新闻，存入 Notion cryptonews 数据库

用法:
    python scripts/bn/cryptonews_fetch.py
    python scripts/bn/cryptonews_fetch.py --count 10
    python scripts/bn/cryptonews_fetch.py --dry-run
"""

import argparse
import os
import re
import sys
import time
from datetime import datetime, timezone

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
except ImportError:
    pass

from bs4 import BeautifulSoup

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = "34aaaa31a0aa806aa20bdd5f9a6d53e8"

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

BASE_URL = "https://crypto.news"

# crypto.news 分类 → Notion select 选项映射
CATEGORY_MAP = {
    "btc": "Bitcoin", "bitcoin": "Bitcoin",
    "eth": "Ethereum", "ethereum": "Ethereum",
    "defi": "DeFi",
    "nft": "NFT",
    "market": "Market", "markets": "Market",
    "regulation": "Regulation",
    "web3": "Web3",
    "technology": "Technology", "tech": "Technology",
    "altcoin": "Altcoin", "altcoins": "Altcoin",
    "doge": "Altcoin", "bnb": "Altcoin", "sol": "Altcoin",
    "xrp": "Altcoin",
}

# 自动打标签关键词
TAG_KEYWORDS = {
    "Bitcoin": ["bitcoin", "btc"],
    "Ethereum": ["ethereum", "eth"],
    "BNB": ["bnb", "binance"],
    "DOGE": ["doge", "dogecoin"],
    "Solana": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "DeFi": ["defi", "liquidity", "yield", "amm"],
    "NFT": ["nft", "nonfungible"],
    "Regulation": ["sec", "regulation", "legal", "law", "ban", "comply"],
    "ETF": ["etf", "fund", "blackrock", "fidelity"],
    "AI": ["ai", "artificial intelligence", "llm", "openai"],
    "Mining": ["mining", "miner", "hashrate"],
    "Stablecoin": ["usdt", "usdc", "stablecoin"],
}


# ============ 抓取 ============

def fetch_news(count: int = 5) -> list[dict]:
    resp = requests.get(BASE_URL, headers={
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
    }, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")

    articles = []
    # home-latest-news__item 是首页最新新闻列表
    items = soup.select(".home-latest-news-item")
    for item in items[:count]:
        try:
            href = item.get("href", "")
            if href.startswith("/"):
                href = BASE_URL + href

            title_el = item.find(class_=re.compile("title"))
            title = title_el.get_text(strip=True) if title_el else item.get_text(strip=True)

            time_el = item.find("time")
            pub_time = ""
            if time_el:
                pub_time = time_el.get("datetime") or time_el.get_text(strip=True)

            tag_el = item.find(class_=re.compile("tag|category"))
            # 从 URL 路径推断分类（比 DOM 更可靠）
            category_raw = tag_el.get_text(strip=True).lower() if tag_el else ""
            if not category_raw:
                # 从标题关键词推断
                category_raw = title.lower()

            img_el = item.find("img")
            image_url = ""
            if img_el:
                image_url = img_el.get("src") or img_el.get("data-src") or ""

            if title and href:
                articles.append({
                    "title": title,
                    "link": href,
                    "category_raw": category_raw,
                    "image_url": image_url,
                    "pub_time": pub_time,
                })
        except Exception:
            continue

    return articles


def fetch_og_image(url: str) -> str:
    """从文章页抓 og:image"""
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]
    except Exception:
        pass
    return ""


def map_category(raw: str) -> str:
    """将原始分类字符串映射到 Notion select 选项"""
    raw_lower = raw.lower()
    for key, val in CATEGORY_MAP.items():
        if key in raw_lower:
            return val
    return "Market"


def auto_tags(title: str) -> list[str]:
    """根据标题关键词自动打标签"""
    title_lower = title.lower()
    tags = []
    for tag, keywords in TAG_KEYWORDS.items():
        if any(k in title_lower for k in keywords):
            tags.append(tag)
    return tags[:5]


# ============ Notion 操作 ============

def page_exists(title: str) -> bool:
    """检查标题是否已存在（避免重复）"""
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {"property": "Name", "title": {"equals": title}},
            "page_size": 1,
        },
        timeout=10,
    )
    if resp.status_code == 200:
        return len(resp.json().get("results", [])) > 0
    return False


def create_page(news: dict) -> bool:
    tags = auto_tags(news["title"])
    category = map_category(news.get("category_raw", ""))

    properties = {
        "Name": {"title": [{"text": {"content": news["title"]}}]},
        "来源": {"rich_text": [{"text": {"content": "crypto.news"}}]},
        "发布时间": {"rich_text": [{"text": {"content": news.get("pub_time", "")}}]},
        "原文链接": {"url": news["link"]},
        "分类": {"select": {"name": category}},
        "发布BNSquare": {"checkbox": False},
    }

    if news.get("image_url"):
        properties["封面图"] = {"url": news["image_url"]}

    if tags:
        properties["标签"] = {"multi_select": [{"name": t} for t in tags]}

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties},
        timeout=10,
    )
    return resp.status_code == 200


# ============ 主程序 ============

def main():
    parser = argparse.ArgumentParser(description="crypto.news → Notion")
    parser.add_argument("--count", type=int, default=5, help="抓取条数")
    parser.add_argument("--dry-run", action="store_true", help="只抓取不写入")
    args = parser.parse_args()

    if not NOTION_API_KEY:
        print("❌ NOTION_API_KEY 未设置")
        sys.exit(1)

    print("=" * 60)
    print("📰 crypto.news → Notion")
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}  抓取: {args.count} 条")
    print("=" * 60)

    print("🔍 抓取 crypto.news...")
    articles = fetch_news(args.count)
    print(f"找到 {len(articles)} 条\n")

    saved = skipped = failed = 0
    for i, news in enumerate(articles, 1):
        print(f"[{i}/{len(articles)}] {news['title'][:60]}")
        print(f"  分类: {news['category_raw']} → {map_category(news['category_raw'])}")
        print(f"  标签: {auto_tags(news['title'])}")
        print(f"  时间: {news['pub_time']}")

        if not news.get("image_url"):
            print("  封面图: 抓取 og:image...")
            news["image_url"] = fetch_og_image(news["link"])
        print(f"  封面图: {news['image_url'][:60] if news['image_url'] else '无'}")

        if args.dry_run:
            print("  [dry-run] 跳过写入\n")
            continue

        if page_exists(news["title"]):
            print("  ⏭  已存在，跳过\n")
            skipped += 1
            continue

        if create_page(news):
            print("  ✅ 已写入 Notion\n")
            saved += 1
        else:
            print("  ❌ 写入失败\n")
            failed += 1

        time.sleep(0.5)

    print("=" * 60)
    print(f"完成 | 新增: {saved}  跳过: {skipped}  失败: {failed}")


if __name__ == "__main__":
    main()
