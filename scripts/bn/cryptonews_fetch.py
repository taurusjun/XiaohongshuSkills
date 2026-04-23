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

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from image_uploader import upload_to_cloudinary as _upload_raw


def remove_watermark(img: "Image.Image") -> "Image.Image":
    """
    去掉 crypto.news 底部水印条带。
    crypto.news 的水印固定在底部约 12% 的区域，直接裁掉。
    """
    w, h = img.size
    crop_y = int(h * 0.88)
    return img.crop((0, 0, w, crop_y))


def upload_as_jpeg(image_url: str) -> str:
    """下载图片，去水印，转成 JPEG 后上传到 Cloudinary，返回 CDN URL"""
    import io
    import tempfile
    from PIL import Image

    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
    api_key = os.environ.get("CLOUDINARY_API_KEY")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET")
    if not all([cloud_name, api_key, api_secret]):
        print("[cloudinary] 缺少配置")
        return ""

    try:
        # 下载原图
        resp = requests.get(image_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        resp.raise_for_status()

        # 去水印 + 转 JPEG
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img = remove_watermark(img)
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=92)
        buf.seek(0)

        # 上传文件到 Cloudinary
        import hashlib, time as _time
        timestamp = int(_time.time())
        params = f"timestamp={timestamp}{api_secret}"
        signature = hashlib.sha1(params.encode()).hexdigest()

        upload_resp = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
            files={"file": ("cover.jpg", buf, "image/jpeg")},
            data={"api_key": api_key, "timestamp": timestamp, "signature": signature},
            timeout=30,
        )
        if upload_resp.status_code == 200:
            url = upload_resp.json().get("secure_url", "")
            if url:
                print(f"[cloudinary] Uploaded JPEG: {url}")
                return url
        print(f"[cloudinary] Failed: {upload_resp.status_code} {upload_resp.text[:200]}")
    except Exception as e:
        print(f"[cloudinary] Error: {e}")
    return ""

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
    """用 LiteLLM 生成中文标题 + 摘要+要点，返回 (zh_title, content)"""
    if not LITELLM_API_KEY:
        return "", ""
    try:
        prompt = (
            f"请对以下加密货币新闻完成任务，直接输出结果，不要有多余解释：\n\n"
            f"按以下格式输出：\n"
            f"【标题】中文标题（不超过30字）\n"
            f"【摘要】2-3句总结性描述\n"
            f"【要点】\n"
            f"• 要点1\n"
            f"• 要点2\n"
            f"• 要点3\n\n"
            f"原文标题：{title}\n\n正文节选：{body[:1500]}"
        )
        resp = requests.post(
            f"{LITELLM_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={
                "model": "bedrock-claude-4-6-sonnet",  # 比 GLM-5 快，适合摘要任务
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 1000,
            },
            timeout=60,
        )
        if resp.status_code == 200:
            msg = resp.json()["choices"][0]["message"]
            raw = msg.get("content") or ""
            if not raw.strip():
                rc = msg.get("reasoning_content") or ""
                if "【标题】" in rc:
                    raw = rc[rc.rfind("【标题】"):]
                else:
                    paras = [p.strip() for p in rc.split("\n\n") if p.strip()]
                    candidates = [p for p in paras[-6:] if any('\u4e00' <= c <= '\u9fff' for c in p)]
                    raw = "\n\n".join(candidates)

            if raw.strip():
                zh_title = ""
                for line in raw.splitlines():
                    line = line.strip()
                    if line.startswith("【标题】"):
                        zh_title = line.replace("【标题】", "").strip()
                        break
                # 摘要+要点 = 【标题】之后的全部内容
                body_content = raw
                if "【摘要】" in raw:
                    body_content = raw[raw.index("【摘要】"):]
                elif "【标题】" in raw:
                    body_content = raw[raw.index("【标题】") + len(zh_title) + 5:].strip()
                return zh_title, body_content
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

def key_exists(key: str) -> bool:
    """用 key（slug MD5）检查是否已存在，比标题去重更精准"""
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {"property": "key", "rich_text": {"equals": key}},
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
    zh_title = news.get("zh_title", "") or news["title"]
    summary = news.get("summary", "")

    properties = {
        "Name":         {"title": [{"text": {"content": zh_title}}]},
        "key":          {"rich_text": [{"text": {"content": key}}]},
        "来源":         {"rich_text": [{"text": {"content": "crypto.news"}}]},
        "发布时间":     {"rich_text": [{"text": {"content": news.get("pub_time", "")}}]},
        "原文链接":     {"url": news["link"]},
        "分类":         {"select": {"name": category}},
        "发布BNSquare": {"checkbox": False},
    }

    if news.get("original_img"):
        properties["原图链接"] = {"url": news["original_img"]}
    cdn = news.get("cdn_img") or news.get("original_img", "")
    if cdn:
        properties["封面图"] = {"url": cdn}
    if tags:
        properties["标签"] = {"multi_select": [{"name": t} for t in tags]}
    tokens = extract_tokens(news["title"])
    if tokens:
        properties["tokens"] = {"multi_select": [{"name": t} for t in tokens]}

    # 摘要和要点写入页面正文 blocks（格式化渲染）
    children = []
    if summary:
        for line in summary.split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("【") and "】" in line:
                # 章节标题 → heading_3
                heading = line[line.index("】") + 1:].strip()
                section = line[1:line.index("】")]
                children.append({
                    "object": "block", "type": "heading_3",
                    "heading_3": {"rich_text": [{"type": "text", "text": {"content": section}}]}
                })
                if heading:
                    children.append({
                        "object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": heading[:2000]}}]}
                    })
            elif line.startswith(("•", "-", "*", "·")):
                # 要点 → bulleted_list_item
                text = line.lstrip("•-*· ").strip()
                children.append({
                    "object": "block", "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": text[:2000]}}]}
                })
            else:
                children.append({
                    "object": "block", "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[:2000]}}]}
                })

    body: dict = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": properties,
    }
    if children:
        body["children"] = children

    cover_url = cdn
    if cover_url:
        body["cover"] = {"type": "external", "external": {"url": cover_url}}

    resp = requests.post(
        "https://api.notion.com/v1/pages",
        headers=NOTION_HEADERS,
        json=body,
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"  Notion error: {resp.text[:200]}")
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
        original_img = news.get("image_url", "")
        print(f"  原图链接: {original_img[:60] if original_img else '无'}")

        # 上传到 Cloudinary
        cdn_url = ""
        if original_img:
            print("  上传 Cloudinary (JPEG)...")
            cdn_url = upload_as_jpeg(original_img)
        news["original_img"] = original_img
        news["cdn_img"] = cdn_url
        print(f"  封面图(CDN): {cdn_url[:60] if cdn_url else '跳过'}")

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

        if key_exists(url_to_key(news["link"])):
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
