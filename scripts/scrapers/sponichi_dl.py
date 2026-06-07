#!/usr/bin/env python3
"""sponichi.co.jp photo gallery scraper.

Each /gazo/ page contains a photo-carousel listing ALL article photos as
_thum.webp thumbnails plus one photo-detail showing the current _view.webp.
We collect from the carousel and convert _thum → _view for full-size images.
"""
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://www.sponichi.co.jp"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}


def scrape(gallery_url: str) -> list[str]:
    url = gallery_url.split("?")[0]
    try:
        resp = requests.get(url, headers={**HEADERS, "Referer": BASE + "/"}, timeout=20)
        if resp.status_code != 200:
            return []
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    images: list[str] = []
    seen: set[str] = set()

    # photo-carousel contains ALL photos in this article's gallery as thumbnails
    carousel = soup.select_one("[data-component='photo-carousel']")
    if carousel:
        for img in carousel.find_all("img"):
            src = img.get("src", "")
            if not src:
                continue
            full_src = src if src.startswith("http") else urljoin(BASE, src)
            # Convert thumbnail to full-size
            view_src = full_src.replace("_thum.webp", "_view.webp")
            if view_src not in seen:
                seen.add(view_src)
                images.append(view_src)

    # Fallback: current photo-detail if carousel was empty
    if not images:
        detail = soup.select_one("figure[data-component='photo-detail'] img.gallery-img")
        if detail:
            src = detail.get("src", "")
            if src:
                full_src = src if src.startswith("http") else urljoin(BASE, src)
                images.append(full_src)

    if images:
        print(f"  📷 sponichi 共 {len(images)} 图片")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    count = 0
    out_dir.mkdir(parents=True, exist_ok=True)
    referer = gallery_url.split("?")[0]
    for i, url in enumerate(urls, 1):
        try:
            r = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=30)
            r.raise_for_status()
            ext = url.rsplit(".", 1)[-1].split("?")[0] or "jpg"
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            (out_dir / f"{i:03d}.{ext}").write_bytes(r.content)
            count += 1
        except Exception as e:
            print(f"    ✗ {url[:80]}: {e}")
    return count
