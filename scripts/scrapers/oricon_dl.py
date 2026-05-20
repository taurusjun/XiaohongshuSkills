#!/usr/bin/env python3
"""oricon.co.jp 图集下载 — requests + 完整浏览器头 + Referer 链 + 随机延迟"""
import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from . import BROWSER_HEADERS, download_images

BASE_URL = "https://www.oricon.co.jp"


def scrape(gallery_url: str) -> list[str]:
    """从 oricon 图集页抓取所有大图 URL。支持 embed 和 photo 两种 URL 格式。"""
    # Normalize /embed/photo/ → /photo/1/
    if "/embed/photo/" in gallery_url:
        clean = gallery_url.split("?")[0].rstrip("/")
        m = re.search(r"/news/(\d+)/", clean)
        if m:
            gallery_url = f"{BASE_URL}/news/{m.group(1)}/photo/1/"

    base_photo = re.sub(r"/photo/\d+.*", "", gallery_url.split("?")[0])
    base_photo = base_photo.rstrip("/")
    start_num_m = re.search(r"/photo/(\d+)", gallery_url)
    start_num = int(start_num_m.group(1)) if start_num_m else 1

    session = requests.Session()
    images: list[str] = []
    seen: set[str] = set()

    entry_url = f"{base_photo}/photo/{start_num}/"
    try:
        resp = session.get(entry_url, headers=BROWSER_HEADERS, timeout=20)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        cached_soup: dict[int, BeautifulSoup] = {start_num: soup}
    except Exception as e:
        print(f"  ⚠️ oricon 入口页失败: {e}")
        return []

    total = len(soup.find_all("img", src=re.compile(r"_p_s_")))
    if total <= 0:
        links = soup.find_all("a", href=re.compile(r"/photo/\d+/"))
        nums = [int(re.search(r"/photo/(\d+)/", a["href"]).group(1))
                for a in links if re.search(r"/photo/(\d+)/", a["href"])]
        total = max(nums) if nums else 1
    if total <= 0:
        return []
    print(f"  📖 oricon 共 {total} 张图片")

    for i in range(1, total + 1):
        page_url = f"{base_photo}/photo/{i}/"

        if i in cached_soup:
            page_soup = cached_soup[i]
        else:
            time.sleep(random.uniform(1.5, 3.0))
            referer = f"{base_photo}/photo/{max(1, i - 1)}/"
            hdrs = dict(BROWSER_HEADERS)
            hdrs["Referer"] = referer
            hdrs["Sec-Fetch-Site"] = "same-origin"

            for retry in range(3):
                try:
                    resp = session.get(page_url, headers=hdrs, timeout=20)
                    resp.raise_for_status()
                    page_soup = BeautifulSoup(resp.text, "html.parser")
                    break
                except Exception as e:
                    if retry < 2:
                        time.sleep(3 * (retry + 1))
                        continue
                    print(f"  ⚠️ oricon page {i} 失败: {e}")
                    page_soup = None
            if page_soup is None:
                continue

        img = page_soup.find("img", class_=re.compile(r"main_photo_image"))
        if not img:
            img = page_soup.find("img", src=re.compile(r"_p_[ol]_"))
        src = img.get("src", "") if img else ""
        if src and not src.startswith("http"):
            src = urljoin(BASE_URL, src)
        if src and src not in seen:
            seen.add(src)
            images.append(src)

    return images


def download(gallery_url: str, out_dir: Path) -> int:
    """抓取并下载 oricon 图集所有图片到 out_dir。返回成功下载数量。"""
    urls = scrape(gallery_url)
    if not urls:
        return 0
    referer = gallery_url.split("?")[0]
    return download_images(urls, out_dir, referer_url=referer)
