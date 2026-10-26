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


# 正文 <div data-content> 里夹着的非正文图：Amazon 推广、相关文章卡片、Google 徽章、目录栏
_JUNK_CLASS_MARKERS = ("ogpcard", "affiliate", "google-preferred", "mokuji", "amazon")
_JUNK_HOSTS = ("media-amazon.com", "images-amazon.com", "amazon.co.jp")


def _log_error(msg: str) -> None:
    try:
        from sqlite_db import _log_db_error
        _log_db_error(msg)
    except Exception:
        pass


def _is_junk(im, body) -> bool:
    """img 是否属于正文里的推广 / 相关文章 / 装饰图（按祖先 class 和图片域名判断）。"""
    for p in im.parents:
        if p is body:
            break
        cls = " ".join(p.get("class") or [])
        if any(m in cls for m in _JUNK_CLASS_MARKERS):
            return True
    src = im.get("src") or im.get("data-src") or ""
    return any(h in src for h in _JUNK_HOSTS)


def _upgrade(src: str) -> str:
    """article-thumb 缩略图 → 全尺寸"""
    full = re.sub(r'-\d+x\d+(\.[a-z]+)?$', r'\1', src)
    full = re.sub(r'-300(\.[a-z]+)?$', r'\1', full)
    return full


def scrape(gallery_url: str) -> list[str]:
    """从 nlab.itmedia.co.jp 文章页抓取所有大图 URL（多页聚合）"""
    # 归一化到 /cont/articles/<id>/（不论带不带页码 / 查询串 / #utm 片段）。
    # 原先先用 re.sub(r'/\d+/?$', ...) 去页码，对不带页码的 /cont/articles/<id>/ 会把文章 id 当页码删掉，
    # 得到 /cont/articles/ 而 404。
    m = re.search(r'(/cont/articles/\d+/)', gallery_url)
    if not m:
        print(f"  ⚠️ itmedia/nlab URL 不识别: {gallery_url}")
        _log_error(f"itmedia/nlab URL 不识别: {gallery_url}")
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
        # 正文在 <div data-content> 里。页面其他位置的 .article-thumb 是排行榜 / 相关文章列表的
        # 缩略图（每篇文章都是同样 15 张），不是这篇的图，不能取。
        body = soup.select_one("div[data-content]")
        if body is None:
            print("  ⚠️ itmedia/nlab 未找到 div[data-content]（页面结构可能改版）")
            _log_error(f"itmedia/nlab 未找到 div[data-content]: {base_url}")
            return
        for im in body.select("img"):
            if _is_junk(im, body):
                continue
            src = im.get("src") or im.get("data-src") or ""
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
        _log_error(f"itmedia/nlab 未提取到图片: {gallery_url}")
    return images


def download(gallery_url: str, out_dir: Path) -> int:
    urls = scrape(gallery_url)
    if not urls:
        return 0
    time.sleep(random.uniform(0.5, 1.5))
    return download_images(urls, out_dir, referer_url=gallery_url.split("?")[0])
