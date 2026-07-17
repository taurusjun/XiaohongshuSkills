#!/usr/bin/env python3
"""bunshun.jp 图集下载 — Swiper 轮播图，图片散落在 HTML 中，提取后去重升级大图"""

import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "../.."))
from config.yahoo_conf import get_proxies as _get_proxies

from . import download_images

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}

SKIP_PATH = {"common", "resources", "mwimgs_fixed"}
SKIP_SIZE = {"280", "364", "160", "240x150", "120x", "160x", "200", "60", "-"}
BAD_KW = ["logo", "icon", "banner", "sprite", "favicon"]


def scrape(gallery_url: str) -> list[str]:
    """从 bunshun 文章页抓取所有大图 URL。"""
    try:
        resp = None
        for kwargs in [{}, {"proxies": _get_proxies()}]:
            try:
                resp = requests.get(gallery_url, headers=HEADERS, timeout=20, **kwargs)
                resp.raise_for_status()
                break
            except Exception:
                resp = None
                if kwargs: raise
    except Exception as e:
        print(f"  ⚠️ bunshun 获取页面失败: {e}")
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    seen_hashes: set[str] = set()
    images: list[str] = []

    for img in soup.find_all("img"):
        src = img.get("data-src") or img.get("src") or ""
        if not src or "bunshun.ismcdn.jp/mwimgs/" not in src:
            continue

        # Resolve relative
        if src.startswith("/"):
            src = urljoin(gallery_url, src)

        # Exclude non-gallery images: common/resources paths, author icons, thumbnails
        path_match = re.search(r'/mwimgs/[^/]+/[^/]+/([^/]+)/', src)
        if not path_match:
            continue
        size_dir = path_match.group(1)

        if any(kw in src.lower() for kw in BAD_KW):
            continue
        if size_dir in SKIP_SIZE:
            continue

        # Extract image hash for dedup
        hash_match = re.search(r'img_([a-f0-9]+)', src)
        img_hash = hash_match.group(1) if hash_match else src
        if img_hash in seen_hashes:
            continue
        seen_hashes.add(img_hash)

        # Upgrade to full-res: replace thumbnail size with 1500wm
        full_src = re.sub(r'/mwimgs/(\w)/(\w)/([^/]+)/', r'/mwimgs/\1/\2/1500wm/', src)
        images.append(full_src)

    if not images:
        print(f"  ⚠️ bunshun 未提取到图集图片")

    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    time.sleep(random.uniform(0.5, 1.5))
    return download_images(urls, out_dir, referer_url=gallery_url)
