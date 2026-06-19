#!/usr/bin/env python3
"""nishispo.nishinippon.co.jp scraper — 只取正文图，排除关联文章缩略图。

文章图片来自两个容器：
  .details-headarea img（src 含 size1 或 /files/article/）→ 头图
  .details-cntarea  img（src 含 /files/article/）          → 正文插图
其余 size2/size3 均为关联文章缩略图，跳过。
"""
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

BASE = "https://nishispo.nishinippon.co.jp"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
           "Referer": BASE + "/"}


def _get(url: str) -> requests.Response | None:
    """Fetch with SSL quirk workaround (UNEXPECTED_EOF_WHILE_READING)."""
    for kwargs in [{"verify": False}, {"verify": False, "proxies": {"http": "http://127.0.0.1:10090", "https": "http://127.0.0.1:10090"}}]:
        try:
            r = requests.get(url, headers=HEADERS, timeout=20, **kwargs)
            if r.status_code == 200:
                return r
        except Exception:
            pass
    return None


def scrape(gallery_url: str) -> list[str]:
    url = gallery_url.split("#")[0].split("?")[0]
    resp = _get(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    images: list[str] = []
    seen: set[str] = set()

    def add(src: str):
        if not src:
            return
        full = src if src.startswith("http") else ("https:" + src if src.startswith("//") else urljoin(BASE, src))
        if full not in seen:
            seen.add(full)
            images.append(full)

    # 头图：.details-headarea 内含 size1 或 /files/article/ 的图
    for img in soup.select(".details-headarea img"):
        src = img.get("src", "") or img.get("data-src", "")
        if "size1" in src or "/files/article/" in src:
            add(src)

    # 正文插图：.details-cntarea 内含 /files/article/ 的图
    for img in soup.select(".details-cntarea img"):
        src = img.get("src", "") or img.get("data-src", "")
        if "/files/article/" in src or "size1" in src:
            add(src)

    if images:
        print(f"  📷 nishispo 共 {len(images)} 图片")
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
