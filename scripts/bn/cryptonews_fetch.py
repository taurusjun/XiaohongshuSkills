#!/usr/bin/env python3
"""
从 crypto.news 抓取最新新闻，存入 Notion cryptonews 数据库

用法:
    python scripts/bn/cryptonews_fetch.py
    python scripts/bn/cryptonews_fetch.py --count 10
    python scripts/bn/cryptonews_fetch.py --dry-run
"""

import argparse
import hashlib
import os
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
except ImportError:
    pass

from bs4 import BeautifulSoup

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = "34aaaa31a0aa806aa20bdd5f9a6d53e8"

LITELLM_URL = os.environ.get("LITELLM_URL", "https://litellm-prod.toolsfdg.net")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "bedrock-claude-4-6-sonnet")

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

# Token 识别（从标题提取相关 token）
TOKEN_KEYWORDS = {
    "BTC": ["bitcoin", "btc", "blackrock", "microstrategy", "satoshi"],
    "ETH": ["ethereum", "eth", "vitalik", "ether"],
    "BNB": ["bnb", "binance coin"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["doge", "dogecoin"],
    "USDT": ["usdt", "tether"],
    "USDC": ["usdc", "circle"],
    "ADA": ["cardano", "ada"],
    "AVAX": ["avalanche", "avax"],
    "MATIC": ["polygon", "matic"],
    "LINK": ["chainlink", "link"],
    "DOT": ["polkadot", "dot"],
    "UNI": ["uniswap", "uni"],
    "PEPE": ["pepe"],
    "SHIB": ["shiba", "shib"],
}

def extract_tokens(title: str) -> list[str]:
    """从标题提取相关 token"""
    title_lower = title.lower()
    found = []
    for token, keywords in TOKEN_KEYWORDS.items():
        if any(k in title_lower for k in keywords):
            found.append(token)
    return found[:5]


def url_to_key(url: str) -> str:
    """取 URL 最后一段路径（slug）的 MD5"""
    slug = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return hashlib.md5(slug.encode()).hexdigest()


def fetch_article_text(url: str) -> str:
    """抓取文章正文（前 2000 字符，供摘要用）"""
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=12)
        soup = BeautifulSoup(resp.text, "html.parser")
        # crypto.news 正文在 .article-content 或 .post-content
        body = (soup.find(class_=re.compile(r"article.content|post.content|entry.content"))
                or soup.find("article"))
        if body:
            # 去掉 script/style
            for tag in body(["script", "style", "aside", "nav"]):
                tag.decompose()
            return body.get_text(" ", strip=True)[:2000]
    except Exception:
        pass
    return ""


def summarize_zh(title: str, body: str) -> tuple[str, str]:
    """用 LiteLLM 生成中文标题 + 摘要，返回 (zh_title, summary)"""
    if not LITELLM_API_KEY:
        return "", ""
    try:
        prompt = (
            f"请对以下加密货币新闻完成两项任务，直接输出结果，不要有多余解释：\n\n"
            f"1. 用中文翻译标题（一句话，不超过30字）\n"
            f"2. 用中文写3-5句摘要，简洁客观\n\n"
            f"按以下格式输出：\n"
            f"【标题】翻译后的中文标题\n"
            f"【摘要】3-5句中文摘要\n\n"
            f"原文标题：{title}\n\n正文节选：{body[:1500]}"
        )
        resp = requests.post(
            f"{LITELLM_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={
                "model": LITELLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 4000,
            },
            timeout=20,
        )
        if resp.status_code == 200:
            msg = resp.json()["choices"][0]["message"]
            raw = msg.get("content") or ""
            if not raw.strip():
                rc = msg.get("reasoning_content") or ""
                # GLM-5: 最终答案在 reasoning_content 末尾，找【标题】【摘要】标记
                if "【标题】" in rc:
                    raw = rc[rc.rfind("【标题】"):]
                else:
                    paras = [p.strip() for p in rc.split("\n\n") if p.strip()]
                    candidates = [p for p in paras[-6:] if any('\u4e00' <= c <= '\u9fff' for c in p)]
                    raw = "\n\n".join(candidates)

            if raw.strip():
                # 解析 【标题】 和 【摘要】
                zh_title, summary = "", ""
                for line in raw.splitlines():
                    line = line.strip()
                    if line.startswith("【标题】"):
                        zh_title = line.replace("【标题】", "").strip()
                    elif line.startswith("【摘要】"):
                        summary = line.replace("【摘要】", "").strip()
                # 摘要可能跨多行
                if "【摘要】" in raw and not summary:
                    summary = raw.split("【摘要】", 1)[1].strip()
                return zh_title, summary
    except Exception as e:
        print(f"  ⚠️ 摘要生成失败: {e}")
    return "", ""


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
    key = url_to_key(news["link"])
    summary = news.get("summary", "")
    zh_title = news.get("zh_title", "") or news["title"]

    properties = {
        "Name":       {"title": [{"text": {"content": zh_title}}]},
        "key":        {"rich_text": [{"text": {"content": key}}]},
        "来源":       {"rich_text": [{"text": {"content": "crypto.news"}}]},
        "发布时间":   {"rich_text": [{"text": {"content": news.get("pub_time", "")}}]},
        "原文链接":   {"url": news["link"]},
        "分类":       {"select": {"name": category}},
        "发布BNSquare": {"checkbox": False},
    }

    if summary:
        properties["摘要"] = {"rich_text": [{"text": {"content": summary[:2000]}}]}
    if news.get("image_url"):
        properties["封面图"] = {"url": news["image_url"]}
    if tags:
        properties["标签"] = {"multi_select": [{"name": t} for t in tags]}
    tokens = extract_tokens(news["title"])
    if tokens:
        properties["tokens"] = {"multi_select": [{"name": t} for t in tokens]}

    body: dict = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties}

    # 封面图同时设为页面 cover
    if news.get("image_url"):
        body["cover"] = {"type": "external", "external": {"url": news["image_url"]}}

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=body,
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
        print(f"  分类: {map_category(news['category_raw'])}")
        print(f"  标签: {auto_tags(news['title'])}")
        print(f"  tokens: {extract_tokens(news['title'])}")
        print(f"  时间: {news['pub_time']}")

        # 抓文章正文（同时顺手取封面图）
        print("  正在抓取正文...")
        body_text = fetch_article_text(news["link"])
        if not news.get("image_url"):
            news["image_url"] = fetch_og_image(news["link"])
        print(f"  封面图: {news['image_url'][:60] if news['image_url'] else '无'}")

        # 生成中文标题 + 摘要
        print("  生成中文标题和摘要...")
        zh_title, summary = summarize_zh(news["title"], body_text)
        news["zh_title"] = zh_title
        news["summary"] = summary
        print(f"  中文标题: {zh_title or '(无)'}")
        print(f"  摘要: {summary[:80]}..." if summary else "  摘要: 无")
        print(f"  key: {url_to_key(news['link'])}")

        if args.dry_run:
            print("  [dry-run] 跳过写入\n")
            continue

        if page_exists(zh_title or news["title"]):
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
