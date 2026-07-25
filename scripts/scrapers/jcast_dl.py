#!/usr/bin/env python3
"""j-cast.com 图集下载 — 单页提取所有 /images/ 大图，去重"""
import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

import sys as _sys, os as _os
_sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "../.."))
from config.yahoo_conf import get_proxies as _get_proxies

from . import download_images

BASE = "https://www.j-cast.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
}

SKIP_KW = ["logo", "icon", "banner", "sprite", "favicon", "footer", "header",
           "btn", "button", "sns", "ad_", "/ad/", "adv", "common", "default"]


def scrape(gallery_url: str) -> list[str]:
    """从 j-cast.com 图集页抓取所有大图 URL（单页，去重）"""
    # 去掉 ?num= 等 referrer 参数（不是分页）
    p = urlparse(gallery_url)
    clean_url = f"{p.scheme}://{p.netloc}{p.path}"

    try:
        for kwargs in [{}, {"proxies": _get_proxies()}]:
            try:
                resp = requests.get(clean_url, headers=HEADERS, timeout=20, **kwargs)
                resp.raise_for_status()
                break
            except Exception:
                resp = None
                if kwargs: raise
        if resp is None:
            return []
    except Exception as e:
        print(f"  ⚠️ j-cast 获取页面失败: {e}")
        return []

    s = BeautifulSoup(resp.text, "html.parser")
    seen: set[str] = set()
    images: list[str] = []

    for im in s.find_all("img"):
        src = im.get("src") or im.get("data-src") or ""
        if not src or "j-cast.com/images/" not in src:
            continue
        # Resolve relative
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = urljoin(BASE, src)
        # 过滤 logo/icon 等
        src_lower = src.lower()
        if any(kw in src_lower for kw in SKIP_KW):
            continue
        # 过滤 article list 缩略图（路径含 /list/ 或尺寸很小）
        if "/list/" in src or "_list_" in src_lower:
            continue
        if src not in seen:
            seen.add(src)
            images.append(src)

    if not images:
        print("  ⚠️ j-cast 未提取到图片")
    else:
        print(f"  📷 j-cast 提取到 {len(images)} 张图片")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    time.sleep(random.uniform(0.3, 1.0))
    return download_images(urls, out_dir, referer_url=gallery_url.split("?")[0])
