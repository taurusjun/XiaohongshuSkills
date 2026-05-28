#!/usr/bin/env python3
"""news.ntv.co.jp 图集下载

URL 格式：
  https://news.ntv.co.jp/category/{cat}/{article_id}/image?p=1

图片来自 Next.js 渲染的 JSON 数据，需用 CDP 渲染后提取。
图片 URL 模式：https://news.ntv.co.jp/gimage/n24/articles/{uuid}/{uuid}.jpg?w=1200
"""

import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import download_images

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "ja-JP,ja;q=0.9",
    "Referer": "https://news.ntv.co.jp/",
}

MAX_PAGES = 10


def _article_id(gallery_url: str) -> str:
    """从 gallery URL 提取 article_id"""
    m = re.search(r'/([a-f0-9]{32})/image', gallery_url)
    return m.group(1) if m else ""


def _fetch_page_images_cdp(gallery_url: str) -> list[str]:
    """用 CDP 渲染页面提取图片（JS 渲染必须）"""
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from cdp_publish import XiaohongshuPublisher
        pub = XiaohongshuPublisher()
        pub.connect()

        all_imgs: list[str] = []

        # 检测总页数
        pub._navigate(gallery_url)
        time.sleep(3)
        pub._evaluate("window.scrollTo(0, document.body.scrollHeight)")
        time.sleep(1.5)

        # 提取当前页图片
        def _extract():
            return pub._evaluate("""
                (() => {
                    const imgs = new Set();
                    document.querySelectorAll('img, [data-src]').forEach(el => {
                        const src = el.src || el.dataset.src || '';
                        if (src && src.includes('news.ntv.co.jp') && src.includes('gimage'))
                            imgs.add(src);
                    });
                    document.querySelectorAll('picture source').forEach(el => {
                        const url = (el.srcset || '').split(' ')[0];
                        if (url && url.includes('gimage')) imgs.add(url);
                    });
                    return Array.from(imgs);
                })()
            """) or []

        imgs = _extract()
        all_imgs.extend(imgs)

        # 检测总页数
        total_pages = pub._evaluate("""
            (() => {
                const txt = document.body.innerText;
                const m = txt.match(/(\\d+)\\/(\\d+)/);
                return m ? parseInt(m[2]) : 1;
            })()
        """) or 1

        # 翻页
        p = urlparse(gallery_url)
        base = f"{p.scheme}://{p.netloc}{p.path}"
        for page in range(2, min(total_pages + 1, MAX_PAGES + 1)):
            pub._navigate(f"{base}?p={page}")
            time.sleep(3)
            pub._evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(1.5)
            for img in _extract():
                if img not in all_imgs:
                    all_imgs.append(img)

        return all_imgs
    except Exception as e:
        print(f"  ⚠️ ntv CDP 抓取失败: {e}")
        return []


def _to_full_res(url: str) -> str:
    """把缩略图 URL 升级为 1200px 高清版"""
    url = re.sub(r'\?.*$', '', url)
    return f"{url}?w=1200"


def scrape(gallery_url: str) -> list[str]:
    """返回图片 URL 列表（高清版）"""
    raw = _fetch_page_images_cdp(gallery_url)
    return list(dict.fromkeys(_to_full_res(u) for u in raw))


def download(gallery_url: str, out_dir: Path) -> int:
    """下载图集到 out_dir，返回下载数量"""
    urls = scrape(gallery_url)
    if not urls:
        return 0
    return download_images(urls, out_dir, headers=HEADERS)
