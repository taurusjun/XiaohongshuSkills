#!/usr/bin/env python3
"""jprime.jp 图集下载 — .article-body 内 .image-area img，支持分页"""
import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import download_images

BASE_URL = "https://www.jprime.jp"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}


def scrape(gallery_url: str) -> list[str]:
    """从 jprime 文章页抓取所有大图 URL。支持多页（?page=N）。"""
    session = requests.Session()

    # Determine max pages from page 1
    base = re.sub(r'\?page=\d+', '', gallery_url.split('?')[0])
    try:
        r = session.get(gallery_url.split('?')[0], headers=HEADERS, timeout=20)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ jprime 入口页失败: {e}")
        return []

    max_page = 1
    for a in soup.find_all("a", href=re.compile(r"page=")):
        m = re.search(r"page=(\d+)", a.get("href", ""))
        if m:
            max_page = max(max_page, int(m.group(1)))

    images: list[str] = []
    seen: set[str] = set()

    for pg in range(1, max_page + 1):
        if pg > 1:
            time.sleep(random.uniform(1, 2))
            try:
                r = session.get(f"{base}?page={pg}", headers=HEADERS, timeout=20)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
            except Exception as e:
                print(f"  ⚠️ jprime page {pg} 失败: {e}")
                continue

        body = soup.select_one(".article-body")
        if not body:
            continue

        for img in body.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if not src:
                continue
            # Skip tiny icons/logos
            if any(kw in src.lower() for kw in ["logo", "icon", "banner"]):
                continue
            if not src.startswith("http"):
                src = urljoin(BASE_URL, src)
            # Convert thumbnail URLs to full-size: /350mw/ -> /-/
            src = re.sub(r'/350mw/', '/-/', src)
            if src not in seen:
                seen.add(src)
                images.append(src)

    if images:
        print(f"  📷 jprime 共 {len(images)} 张图片 ({max_page} 页)")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    return download_images(urls, out_dir, referer_url=gallery_url)
