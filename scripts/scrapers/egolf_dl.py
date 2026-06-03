#!/usr/bin/env python3
"""egolf.jp gallery scraper — WordPress site, /wp-content/uploads/ images in article body."""
import re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
}

def download_images(img_urls: list[str], out_dir: Path, referer_url: str = "") -> int:
    """Download images to out_dir, return count."""
    out_dir.mkdir(parents=True, exist_ok=True)
    headers = dict(HEADERS)
    if referer_url:
        headers["Referer"] = referer_url
    count = 0
    for i, url in enumerate(img_urls, 1):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            ext = url.rsplit(".", 1)[-1].split("?")[0] or "jpg"
            if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
                ext = "jpg"
            (out_dir / f"{i:03d}.{ext}").write_bytes(r.content)
            count += 1
        except Exception as e:
            print(f"    ✗ {url[:80]}: {e}")
    return count


def scrape(gallery_url: str) -> list[str]:
    url = gallery_url.split("?")[0]  # strip UTM params
    session = requests.Session()
    images: list[str] = []
    seen: set[str] = set()

    for page in range(1, 20):
        page_url = f"{url.rstrip('/')}/{page}/" if page > 1 else url
        try:
            resp = session.get(page_url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                break
        except Exception:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        article = soup.find("article") or soup.find(class_="entry-content") or soup

        found = 0
        for img in article.find_all("img"):
            for attr in ("src", "data-src", "data-lazy-src"):
                src = img.get(attr, "")
                if not src or "wp-content/uploads" not in src:
                    continue
                # Skip thumbnails and site assets
                if "150x150" in src or "logo" in src.lower() or "banner" in src.lower() or "icon" in src.lower():
                    continue
                full = "https://egolf.jp" + src if src.startswith("/") else src
                full = full.split("?")[0]
                if full not in seen:
                    seen.add(full)
                    images.append(full)
                    found += 1

        if found == 0 and page > 1:
            break  # no more pages with images

    if images:
        print(f"  📷 egolf 共 {len(images)} 图片")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    return download_images(urls, out_dir, referer_url=gallery_url)
