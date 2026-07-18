#!/usr/bin/env python3
"""itmedia.co.jp/nlab 图集下载 — article-thumb 升级大图 + 多页聚合"""
import re
import time
import random
from pathlib import Path
from urllib.parse import urljoin

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
}

BASE = "https://nlab.itmedia.co.jp"


def _upgrade(src: str) -> str:
    """article-thumb 缩略图 → 全尺寸"""
    full = re.sub(r'-\d+x\d+(\.[a-z]+)?$', r'\1', src)
    full = re.sub(r'-300(\.[a-z]+)?$', r'\1', full)
    return full


def scrape(gallery_url: str) -> list[str]:
    """从 nlab.itmedia.co.jp 文章页抓取所有大图 URL（多页聚合）"""
    base_url = re.sub(r'/\d+/?$', '/', gallery_url.split("?")[0].rstrip("/"))
    if not base_url.endswith("/cont/articles/"):
        # 把 URL 归一化到 /cont/articles/<id>/
        m = re.search(r'(/cont/articles/\d+/)', gallery_url)
        if not m:
            print(f"  ⚠️ itmedia/nlab URL 不识别: {gallery_url}")
            return []
        base_url = f"{BASE}{m.group(1)}"

    # 第一页发现分页
    try:
        for kwargs in [{}, {"proxies": _get_proxies()}]:
            try:
                r = requests.get(base_url, headers=HEADERS, timeout=20, **kwargs)
                r.raise_for_status()
                break
            except Exception:
                r = None
                if kwargs: raise
        if r is None:
            return []
    except Exception as e:
        print(f"  ⚠️ itmedia/nlab 获取页面失败: {e}")
        return []

    s = BeautifulSoup(r.text, "html.parser")
    # 收集所有分页
    max_page = 1
    for a in s.find_all("a", href=True):
        m = re.search(r'/cont/articles/\d+/(\d+)/?$', a["href"])
        if m:
            max_page = max(max_page, int(m.group(1)))
    print(f"  📖 itmedia/nlab 共 {max_page} 页")

    seen: set[str] = set()
    images: list[str] = []

    def _collect(soup):
        for im in soup.select(".article-thumb img"):
            src = im.get("src") or ""
            if not src.startswith("http"):
                src = urljoin(BASE, src)
            if "/assets/" in src or "icon" in src.lower():
                continue
            full = _upgrade(src)
            if full not in seen:
                seen.add(full)
                images.append(full)

    _collect(s)
    print(f"  page 1: {len(seen)} 张")

    # 后续分页
    for p in range(2, max_page + 1):
        page_url = f"{base_url}{p}/"
        try:
            time.sleep(random.uniform(0.5, 1.5))
            for kwargs in [{}, {"proxies": _get_proxies()}]:
                try:
                    r2 = requests.get(page_url, headers=HEADERS, timeout=20, **kwargs)
                    r2.raise_for_status()
                    break
                except Exception:
                    r2 = None
                    if kwargs: raise
            if r2 is None:
                continue
            before = len(seen)
            _collect(BeautifulSoup(r2.text, "html.parser"))
            print(f"  page {p}: +{len(seen)-before} 张")
        except Exception as e:
            print(f"  ⚠️ itmedia/nlab page {p} 失败: {e}")

    if not images:
        print("  ⚠️ itmedia/nlab 未提取到图片")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    time.sleep(random.uniform(0.5, 1.5))
    return download_images(urls, out_dir, referer_url=gallery_url.split("?")[0])
