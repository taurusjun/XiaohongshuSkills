#!/usr/bin/env python3
"""toyokeizai.net photo gallery scraper — TKOLIB.photos JS var, ismcdn.jp CDN."""
import json
import re
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
# SOCKS5 proxy for toyokeizai
PROXIES = {"http": "socks5h://127.0.0.1:10090", "https": "socks5h://127.0.0.1:10090"}


import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def _fetch(url: str) -> requests.Response | None:
    for _ in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=30, verify=False)
            if resp.status_code == 200:
                return resp
        except Exception:
            pass
    return None


def scrape(gallery_url: str) -> list[str]:
    url = gallery_url.split("?")[0]  # strip UTM params
    resp = _fetch(url)
    if resp is None:
        return []

    images: list[str] = []
    seen: set[str] = set()

    # Primary: parse TKOLIB.photos inline JS variable (all photos in one shot)
    m = re.search(r'TKOLIB\.photos\s*=\s*(\[[\s\S]*?\]);', resp.text)
    if m:
        try:
            photos = json.loads(m.group(1))
            for p in photos:
                img = p.get("image", "").split("?")[0]
                if img and "ismcdn" in img and img not in seen:
                    seen.add(img)
                    images.append(img)
        except (json.JSONDecodeError, AttributeError):
            pass

    # Fallback: CSS selector approach
    if not images:
        soup = BeautifulSoup(resp.text, "html.parser")
        for block in soup.select(".article-photo"):
            for a in block.select("a.figure-expand"):
                href = a.get("href", "").split("?")[0]
                if href and "ismcdn" in href and "/-/" in href and href not in seen:
                    seen.add(href)
                    images.append(href)
            for img in block.find_all("img"):
                src = img.get("src", "")
                if not src or "870m/" not in src:
                    continue
                full = re.sub(r'/\d+m/', '/-/', src).split("?")[0]
                if full not in seen:
                    seen.add(full)
                    images.append(full)

    if images:
        print(f"  📷 toyokeizai 共 {len(images)} 图片")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    count = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, url in enumerate(urls, 1):
        try:
            r = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=30, verify=False)
            ext = url.rsplit(".", 1)[-1].split("?")[0] or "jpg"
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            (out_dir / f"{i:03d}.{ext}").write_bytes(r.content)
            count += 1
        except Exception as e:
            print(f"    ✗ {url[:80]}: {e}")
    return count
