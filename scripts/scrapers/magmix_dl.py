#!/usr/bin/env python3
"""magmix.jp 图集下载 — #gallery_main 内 .attachment-large 图片"""
import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import download_images

BASE_URL = "https://magmix.jp"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}


def scrape(gallery_url: str) -> list[str]:
    """从 magmix 图集页抓取所有大图 URL。#gallery_main 内 img.attachment-large"""
    session = requests.Session()
    try:
        resp = session.get(gallery_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠️ magmix 入口页失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    gallery = soup.select_one("#gallery_main")
    if not gallery:
        return []

    images: list[str] = []
    seen: set[str] = set()
    for img in gallery.find_all("img", class_="attachment-large"):
        src = img.get("src", "") or img.get("data-src", "")
        if not src:
            continue
        if not src.startswith("http"):
            src = urljoin(BASE_URL, src)
        if src not in seen:
            seen.add(src)
            images.append(src)

    if images:
        print(f"  📷 magmix 共 {len(images)} 张图片")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    """抓取并下载 magmix 图集所有图片到 out_dir。"""
    urls = scrape(gallery_url)
    if not urls:
        return 0
    return download_images(urls, out_dir, referer_url=gallery_url)
