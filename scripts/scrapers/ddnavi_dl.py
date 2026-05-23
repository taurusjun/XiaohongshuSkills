#!/usr/bin/env python3
"""ddnavi.com 图集抓取器 — 图片通过 JS 加载，从 HTML 源码正则提取"""

import re
import requests
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Referer": "https://ddnavi.com/",
}


def scrape(gallery_url: str) -> list[str]:
    """从 ddnavi 图集页面提取所有原图 URL"""
    try:
        resp = requests.get(gallery_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        text = resp.text

        article_id = ""
        m = re.search(r'/article/(\d+)/', gallery_url)
        if m:
            article_id = m.group(1)

        pattern = rf'/i/nw/{article_id}/\d+\.jpg' if article_id else r'/i/nw/\d+/\d+\.jpg'
        matches = re.findall(pattern, text) if article_id else []
        if not matches:
            # 备选：直接用更宽的模式
            matches = re.findall(r'/i/nw/\d+/\d+\.jpg', text)

        images = []
        for img_path in dict.fromkeys(matches):
            full = urljoin("https://ddnavi.com", img_path)
            images.append(full)
        return images
    except Exception as e:
        print(f"  ⚠️ ddnavi 图集抓取失败: {e}")
        return []
