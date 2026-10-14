#!/usr/bin/env python3
"""rollingstonejapan.com 图集抓取器。

图集页 /articles/gallery/{article_id}[/{photo_id}] 的照片是 CSS background-image（不是 <img>），
URL 形如 images.rollingstonejapan.com/articles_photos/{article_id}/{W}x{H}/{hash}.jpg。
把尺寸段换成 ORG 即原图（实测 1024x683；缩略图 205x309 实际只有 307x205）。
"""

import re
from pathlib import Path

import requests

from config.yahoo_conf import get_proxies as _get_proxies
from . import download_images

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://rollingstonejapan.com/",
}
IMG_HOST = "https://images.rollingstonejapan.com"


def _log_error(msg: str) -> None:
    try:
        from sqlite_db import _log_db_error
        _log_db_error(msg)
    except Exception:
        pass


def scrape(gallery_url: str) -> list[str]:
    """返回图集全部照片的原图 URL（按页面出现顺序去重）。"""
    m = re.search(r"/articles/(?:gallery|detail)/(\d+)", gallery_url)
    if not m:
        print(f"  ⚠️ rollingstonejapan 无法识别的 URL 结构: {gallery_url}")
        _log_error(f"rollingstonejapan 无法识别的 URL 结构: {gallery_url}")
        return []
    article_id = m.group(1)
    page_url = f"https://rollingstonejapan.com/articles/gallery/{article_id}"
    try:
        resp = requests.get(page_url, headers=HEADERS, proxies=_get_proxies(), timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"  ⚠️ rollingstonejapan 图集抓取失败: {e}")
        _log_error(f"rollingstonejapan 图集抓取失败: {page_url}: {e}")
        return []

    pattern = rf"/articles_photos/{article_id}/\d+x\d+/([0-9a-f]+)\.(jpe?g|png|webp)"
    images: list[str] = []
    for photo_hash, ext in re.findall(pattern, resp.text):
        url = f"{IMG_HOST}/articles_photos/{article_id}/ORG/{photo_hash}.{ext}"
        if url not in images:
            images.append(url)
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    return download_images(urls, out_dir, referer_url=gallery_url)
