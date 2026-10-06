#!/usr/bin/env python3
"""bezzy.jp 图集下载

URL 格式：
  https://bezzy.jp/2026/08/90811/?gallery=90811&aid=90805&gn=0

分页图集：每页一张主图（bezzy.jp/cms/wp-content/uploads/），
沿 gn=0..N 分页链接遍历收集。requests 被 TLS 指纹拦截时走 CDP 兜底。
"""

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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://bezzy.jp/",
}

MAX_PAGES = 40


def _fetch_html(url: str) -> str:
    """requests 优先，TLS 指纹被拦时走 CDP 9223（带代理）"""
    try:
        r = requests.get(url, headers=HEADERS, proxies=_get_proxies(), timeout=15)
        r.raise_for_status()
        return r.text
    except Exception as e:
        print(f"  ⚠️ bezzy requests 失败: {e}")
    from . import cdp_page_html
    return cdp_page_html(url, port=9223, wait=10.0)


def scrape(gallery_url: str) -> list[str]:
    """从 bezzy.jp 图集页抓取所有主图 URL（分页遍历，去重）"""
    images: list[str] = []
    seen: set[str] = set()
    visited: set[str] = set()
    current_url = gallery_url

    for _ in range(MAX_PAGES):
        if current_url in visited:
            break
        visited.add(current_url)

        html = _fetch_html(current_url)
        if not html:
            break
        s = BeautifulSoup(html, "html.parser")

        for img in s.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if "bezzy.jp/cms/wp-content/uploads/" not in src:
                continue
            if src.startswith("//"):
                src = "https:" + src
            src = src.split("?")[0]
            if src not in seen:
                seen.add(src)
                images.append(src)

        # 下一页：找 gn=N+1 的分页链接（每页 aid 不同，必须从链接取）
        qs = parse_qs(urlparse(current_url).query)
        try:
            gn = int(qs.get("gn", ["0"])[0])
        except ValueError:
            gn = 0
        next_url = ""
        for a in s.find_all("a", href=True):
            if f"gn={gn + 1}" in a["href"]:
                next_url = urljoin(current_url, a["href"])
                break
        if not next_url:
            break
        current_url = next_url
        time.sleep(random.uniform(0.3, 0.8))

    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    time.sleep(random.uniform(0.3, 1.0))
    return download_images(urls, out_dir, referer_url=gallery_url.split("?")[0])
