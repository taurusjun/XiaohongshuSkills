#!/usr/bin/env python3
"""Download all photos from an Oricon photo gallery page."""

import re
import sys
import time
import random
import os
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://www.oricon.co.jp"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

IMG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
}


def get_news_id(url: str) -> str:
    m = re.search(r"/news/(\d+)/", url)
    return m.group(1) if m else "unknown"


def fetch_page(session: requests.Session, url: str, referer: str | None = None) -> BeautifulSoup | None:
    hdrs = dict(HEADERS)
    if referer:
        hdrs["Referer"] = referer
    try:
        resp = session.get(url, headers=hdrs, timeout=20)
        resp.raise_for_status()
        return BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  [!] Failed to fetch {url}: {e}")
        return None


def get_photo_count(soup: BeautifulSoup) -> int:
    """Infer total photo count from thumbnail list or pagination."""
    # Try thumbnail list
    thumbs = soup.select("ul.photo-list li, .thumb-list li, .photo_list li")
    if thumbs:
        return len(thumbs)
    # Try _s_ thumbnail images
    imgs = soup.find_all("img", src=re.compile(r"_p_s_"))
    if imgs:
        return len(imgs)
    # Try pagination links like /photo/6/
    links = soup.find_all("a", href=re.compile(r"/photo/\d+/"))
    nums = [int(re.search(r"/photo/(\d+)/", a["href"]).group(1)) for a in links if re.search(r"/photo/(\d+)/", a["href"])]
    return max(nums) if nums else 1


def get_large_img_url(soup: BeautifulSoup) -> str | None:
    """Extract large image URL from the page."""
    # Try class="main_photo_image"
    img = soup.find("img", class_=re.compile(r"main_photo_image"))
    if img and img.get("src"):
        return img["src"]
    # Try _p_o_ or _p_l_ pattern in any img
    for img in soup.find_all("img", src=re.compile(r"_p_[ol]_")):
        return img["src"]
    return None


def download_image(session: requests.Session, img_url: str, dest: Path, referer: str) -> bool:
    hdrs = dict(IMG_HEADERS)
    hdrs["Referer"] = referer
    try:
        resp = session.get(img_url, headers=hdrs, timeout=30, stream=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        size_kb = len(resp.content) // 1024
        print(f"  -> saved {dest.name} ({size_kb} KB)")
        return True
    except Exception as e:
        print(f"  [!] Download failed {img_url}: {e}")
        return False


def main():
    start_url = sys.argv[1] if len(sys.argv) > 1 else (
        "https://www.oricon.co.jp/news/2455479/photo/2/"
        "?anc=112&utm_source=headlines.yahoo.co.jp"
        "&utm_content=%2Fhl%3Fa%3D20260519-00000378-oric-ent&utm_medium=referral"
    )

    # Normalize /embed/photo/ → /photo/1/
    if "/embed/photo/" in start_url:
        clean = start_url.split("?")[0].rstrip("/")
        news_id_part = re.search(r"/news/(\d+)/", clean).group(1)
        start_url = f"https://www.oricon.co.jp/news/{news_id_part}/photo/1/"
        print(f"Normalized to: {start_url}")

    news_id = get_news_id(start_url)
    out_dir = Path.home() / "Downloads" / f"oricon_{news_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Output dir: {out_dir}")

    session = requests.Session()

    # First visit: discover total count
    print(f"\nFetching first page: {start_url}")
    soup = fetch_page(session, start_url)
    if not soup:
        print("Failed to load start page")
        sys.exit(1)

    total = get_photo_count(soup)
    print(f"Total photos detected: {total}")

    # Build base URL for photo pages
    base_photo = re.sub(r"/photo/\d+/.*", "", start_url.split("?")[0])
    base_photo = base_photo.rstrip("/")

    # Detect which photo number the start URL points to, cache its soup
    start_num_m = re.search(r"/photo/(\d+)/", start_url)
    start_num = int(start_num_m.group(1)) if start_num_m else None

    for i in range(1, total + 1):
        page_url = f"{base_photo}/photo/{i}/"
        print(f"\n[{i}/{total}] {page_url}")

        if i == start_num:
            page_soup = soup  # reuse already-fetched page
        else:
            time.sleep(random.uniform(1.5, 3.0))  # polite delay + anti-bot
            referer = f"{base_photo}/photo/{max(1, i-1)}/"
            page_soup = fetch_page(session, page_url, referer=referer)

        if not page_soup:
            continue

        img_url = get_large_img_url(page_soup)
        if not img_url:
            print("  [!] Could not find large image URL")
            continue

        print(f"  URL: {img_url}")
        ext = img_url.rsplit(".", 1)[-1].split("?")[0]
        dest = out_dir / f"{i:02d}.{ext}"
        download_image(session, img_url, dest, referer=page_url)

    print(f"\nDone. Files in: {out_dir}")


if __name__ == "__main__":
    main()
