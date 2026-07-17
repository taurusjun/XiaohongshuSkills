#!/usr/bin/env python3
"""billboard-japan.com scraper — 缩略图 170x170 → 650x 全尺寸。
SSL: verify=False，不发 Accept-Encoding（避免 Brotli EOF 问题）。
"""
import re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

BASE = "https://www.billboard-japan.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Referer": BASE + "/",
}


def scrape(gallery_url: str) -> list[str]:
    m = re.search(r"/d_news/image/(\d+)", gallery_url)
    if not m:
        return []
    news_id = m.group(1)
    try:
        r = requests.get(f"{BASE}/d_news/image/{news_id}/1",
                         headers=HEADERS, timeout=20, verify=False)
        if r.status_code != 200:
            return []
    except Exception as e:
        print(f"  ⚠️ billboard-japan 请求失败: {e}")
        return []

    soup = BeautifulSoup(r.text, "html.parser")
    images, seen = [], set()
    for img in soup.find_all("img"):
        src = img.get("src", "")
        if "170x170_sub_image" not in src:
            continue
        full = (BASE + src if src.startswith("/") else src)
        full = full.replace("170x170_", "650x_")
        if full not in seen:
            seen.add(full)
            images.append(full)

    if images:
        print(f"  📷 billboard-japan 共 {len(images)} 图片")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    count = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(urls, 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30, verify=False)
            r.raise_for_status()
            ext = url.rsplit(".", 1)[-1].split("?")[0] or "jpg"
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            (out_dir / f"{i:03d}.{ext}").write_bytes(r.content)
            count += 1
        except Exception as e:
            print(f"    ✗ {url[:80]}: {e}")
    return count
