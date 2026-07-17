#!/usr/bin/env python3
"""图集下载共享工具 — 各站点 <site>_dl.py 的公共依赖"""

import requests
from pathlib import Path

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
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
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
}


def download_images(urls: list[str], out_dir: Path, referer_url: str = "") -> int:
    """Download image URLs to out_dir. Returns count of successfully downloaded files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for idx, url in enumerate(urls):
        ext = url.rsplit(".", 1)[-1].split("?")[0]
        if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
            ext = "jpg"
        dest = out_dir / f"{idx + 1:02d}.{ext}"
        hdrs = dict(IMG_HEADERS)
        if referer_url:
            hdrs["Referer"] = referer_url
        try:
            r = requests.get(url, headers=hdrs, timeout=30)
            r.raise_for_status()
            dest.write_bytes(r.content)
            count += 1
        except Exception:
            pass
    return count
