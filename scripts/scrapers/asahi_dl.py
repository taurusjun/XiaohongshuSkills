#!/usr/bin/env python3
"""asahi.com 图集下载 — main[role=main] 内 img 提取，去重升级大图"""

import re
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import download_images

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}

BASE = "https://www.asahi.com"


def scrape(gallery_url: str) -> list[str]:
    """从 asahi 图集页抓取所有大图 URL。"""
    try:
        resp = requests.get(gallery_url, headers=HEADERS, timeout=20)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠️ asahi 获取页面失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    body = soup.select_one("main[role=main]") or soup
    seen = set()
    images = []

    for img in body.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        if not src or "asahicom.jp" not in src:
            continue
        if any(k in src.lower() for k in ["logo", "icon", "banner", "sprite"]):
            continue
        if src.startswith("/"):
            src = urljoin(BASE, src)
        if src in seen:
            continue
        seen.add(src)
        images.append(src)

    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    return download_images(urls, out_dir, referer_url=gallery_url)
