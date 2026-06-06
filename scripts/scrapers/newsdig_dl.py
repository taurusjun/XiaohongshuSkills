#!/usr/bin/env python3
"""newsdig.tbs.co.jp 图集下载 — 分页获取，只取图集大图（figure 内 img）"""

import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from . import download_images

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}


def scrape(gallery_url: str) -> list[str]:
    """从 newsdig 图集页分页抓取所有大图 URL。"""
    # Base URL without display param
    base = re.sub(r'\?.*', '', gallery_url).replace('/gallery/', '/-/')
    if '/articles/-/' not in base:
        # Extract article ID
        m = re.search(r'/articles/gallery/(\d+)', gallery_url)
        if m:
            base = f"https://newsdig.tbs.co.jp/articles/-/{m.group(1)}"

    seen_hashes: set[str] = set()
    images: list[str] = []

    for page in range(1, 50):
        url = f"{base}?display={page}"
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.raise_for_status()
        except Exception:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        found = 0

        # Only images inside <figure> — gallery photos, not sidebar thumbs
        for fig in soup.find_all("figure"):
            for img in fig.find_all("img"):
                src = img.get("data-src") or img.get("src") or ""
                if "newsdig.ismcdn.jp/mwimgs/" not in src:
                    continue
                if src.startswith("/"):
                    src = urljoin(url, src)

                hash_match = re.search(r'img_([a-f0-9]+)', src)
                img_hash = hash_match.group(1) if hash_match else src
                if img_hash in seen_hashes:
                    continue
                seen_hashes.add(img_hash)
                found += 1

                # Upgrade to 1920w full-res
                full_src = re.sub(r'/mwimgs/(\w)/(\w)/(\w+)w/', r'/mwimgs/\1/\2/1920w/', src)
                images.append(full_src)

        if found == 0:
            break
        time.sleep(random.uniform(0.3, 0.6))

    if not images:
        print(f"  ⚠️ newsdig 未提取到图集图片")

    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    return download_images(urls, out_dir, referer_url=gallery_url)
