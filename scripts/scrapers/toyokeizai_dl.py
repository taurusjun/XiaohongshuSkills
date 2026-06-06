#!/usr/bin/env python3
"""toyokeizai.net photo gallery scraper — .article-photo images, ismcdn.jp CDN."""
import re
from pathlib import Path
import requests
from bs4 import BeautifulSoup

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
# SOCKS5 proxy for toyokeizai
PROXIES = {"http": "socks5h://127.0.0.1:10090", "https": "socks5h://127.0.0.1:10090"}


def scrape(gallery_url: str) -> list[str]:
    url = gallery_url.split("?")[0]  # strip UTM params
    session = requests.Session()
    images: list[str] = []
    seen: set[str] = set()

    try:
        resp = session.get(url, headers=HEADERS, proxies=PROXIES, timeout=20)
        if resp.status_code != 200:
            return []
    except Exception:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    for block in soup.select(".article-photo"):
        # Prefer full-size from <a class="figure-expand" href>
        for a in block.select("a.figure-expand"):
            href = a.get("href", "")
            if href and "ismcdn" in href and "/-/" in href:
                full = href.split("?")[0]
                if full not in seen:
                    seen.add(full)
                    images.append(full)
        # Fallback: large preview images (870m)
        for img in block.find_all("img"):
            src = img.get("src", "")
            if not src or "870m/" not in src:
                continue
            full = re.sub(r'/\d+m/', '/-/', src)
            full = full.split("?")[0]
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
            r = requests.get(url, headers=HEADERS, proxies=PROXIES, timeout=30)
            ext = url.rsplit(".", 1)[-1].split("?")[0] or "jpg"
            if ext not in ("jpg", "jpeg", "png", "webp"):
                ext = "jpg"
            (out_dir / f"{i:03d}.{ext}").write_bytes(r.content)
            count += 1
        except Exception as e:
            print(f"    ✗ {url[:80]}: {e}")
    return count
