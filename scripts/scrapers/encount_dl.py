#!/usr/bin/env python3
"""encount.press 图集下载 — .photo-wrap 内 Instagram embed + body 内 wp-content/uploads 图片"""
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import download_images

BASE_URL = "https://encount.press"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://encount.press/",
}

BANNER_KW = ["banner", "recruit", "_SP_", "600x200"]


def _extract_instagram_urls(soup: BeautifulSoup, limit: int = 20) -> list[str]:
    """Extract Instagram post URLs from .photo-wrap blockquotes."""
    urls: list[str] = []
    seen: set[str] = set()
    for pw in soup.find_all(class_=re.compile(r"photo-wrap")):
        for bq in pw.find_all("blockquote", class_="instagram-media"):
            permalink = bq.get("data-instgrm-permalink", "")
            if permalink and permalink not in seen:
                seen.add(permalink)
                urls.append(permalink)
                if len(urls) >= limit:
                    return urls
        for a in pw.find_all("a", href=re.compile(r"instagram\.com/p/")):
            href = a.get("href", "")
            if href and href not in seen:
                seen.add(href)
                urls.append(href)
                if len(urls) >= limit:
                    return urls
    return urls


def _extract_upload_images(soup: BeautifulSoup, limit: int = 20) -> list[str]:
    """Extract wp-content/uploads images from article body, excluding banners."""
    body = soup.find(class_="single__content__txt") or soup
    images: list[str] = []
    seen: set[str] = set()
    for img in body.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if "wp-content/uploads/" not in src:
            continue
        if not src.endswith(('.jpg', '.jpeg', '.png', '.webp')):
            continue
        if "hatena_white.png" in src or "logo.svg" in src or "icon_" in src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(BASE_URL, src)
        src = src.split("?")[0]
        if any(kw in src for kw in BANNER_KW):
            continue
        if src not in seen:
            seen.add(src)
            images.append(src)
            if len(images) >= limit:
                break
    return images


def scrape(gallery_url: str) -> list[str]:
    """从 encount.press 文章页抓取所有图集 URL。支持 Instagram embed 和直接图片。"""
    session = requests.Session()
    try:
        resp = session.get(gallery_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠️ encount 入口页失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    results: list[str] = []

    # 1. Instagram embeds in .photo-wrap
    ig_urls = _extract_instagram_urls(soup)
    results.extend(ig_urls)

    # 2. Direct upload images in article body
    upload_imgs = _extract_upload_images(soup)
    results.extend(upload_imgs)

    if results:
        print(f"  📷 encount 共 {len(results)} 图片/embed")
    return results


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    # Instagram URLs are returned as-is for the downstream downloader to handle
    img_urls = [u for u in urls if not u.startswith("https://www.instagram.com")]
    ig_urls = [u for u in urls if u.startswith("https://www.instagram.com")]
    if ig_urls:
        print(f"  📸 {len(ig_urls)} Instagram embeds (delegated to Instagram downloader)")
    if img_urls:
        return download_images(img_urls, out_dir, referer_url=gallery_url)
    return 0
