#!/usr/bin/env python3
"""
独立媒体下载组件：输入任意 URL，自动识别类型并下载到本地。

支持类型：
  - Instagram 帖子（图片 + 视频）：instagram.com/p/SHORTCODE/
  - YouTube 视频：youtube.com/watch?v=ID 或 youtu.be/ID
  - 文章页嵌入检测：自动从文章 HTML 中提取 Instagram / YouTube 嵌入

前置条件：Chrome 需已通过 CDP 端口 9222 启动并登录对应平台。

用法:
    python scripts/media_download.py <URL>
    python scripts/media_download.py <URL> --dir /tmp/output
    python scripts/media_download.py <URL> --no-cdp   # 跳过 CDP，仅尝试公开下载
"""

import argparse
import hashlib
import os
import re
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

# 复用 gallery_fetch 中已实现的下载逻辑
sys.path.insert(0, os.path.dirname(__file__))
from gallery_fetch import (
    _extract_instagram_shortcode,
    _extract_youtube_video_id,
    _scrape_instagram,
    _download_youtube_video,
)

import requests

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
DEFAULT_CACHE = Path.home() / ".cache" / "xhs_media_download"


def _url_key(url: str) -> str:
    """根据 URL 生成唯一目录名"""
    return hashlib.md5(url.encode()).hexdigest()[:12]


def _extract_youtube_from_url(url: str) -> str:
    """从 YouTube URL 直接解析 video ID"""
    m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else ""


def detect_and_download(url: str, out_dir: Path) -> list[str]:
    """
    自动识别 URL 类型并下载。
    返回下载的本地文件名列表（相对于 out_dir）。
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # ── 1. Instagram 直链 ──────────────────────────────────────────
    if "instagram.com/p/" in url:
        print(f"🔗 识别为 Instagram 帖子")
        return _scrape_instagram(url, out_dir)

    # ── 2. YouTube 直链 ────────────────────────────────────────────
    video_id = _extract_youtube_from_url(url)
    if video_id:
        print(f"🎬 识别为 YouTube 视频: {video_id}")
        fname = _download_youtube_video(video_id, out_dir)
        return [fname] if fname else []

    # ── 3. 文章页：检测嵌入内容 ────────────────────────────────────
    print(f"🔍 抓取文章页，检测嵌入内容: {url[:80]}")
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        html = resp.text
    except Exception as e:
        print(f"  ❌ 无法访问 URL: {e}")
        return []

    # Instagram embed
    shortcode = _extract_instagram_shortcode(html)
    if shortcode:
        ig_url = f"https://www.instagram.com/p/{shortcode}/"
        print(f"  📱 检测到 Instagram 嵌入: {ig_url}")
        return _scrape_instagram(ig_url, out_dir)

    # YouTube embed
    video_id = _extract_youtube_video_id(html)
    if video_id:
        print(f"  🎬 检测到 YouTube 嵌入: {video_id}")
        fname = _download_youtube_video(video_id, out_dir)
        return [fname] if fname else []

    print("  ⚠️ 未检测到支持的嵌入媒体（Instagram / YouTube）")
    return []


def main():
    parser = argparse.ArgumentParser(
        description="输入任意 URL，自动识别并下载 Instagram / YouTube 媒体"
    )
    parser.add_argument("url", help="目标 URL（Instagram / YouTube / 含嵌入的文章页）")
    parser.add_argument(
        "--dir", "-d",
        help="下载目标目录（默认 ~/.cache/xhs_media_download/<hash>/）",
    )
    args = parser.parse_args()

    if args.dir:
        out_dir = Path(args.dir)
    else:
        out_dir = DEFAULT_CACHE / _url_key(args.url)

    print("=" * 60)
    print(f"📥 media_download")
    print(f"   URL : {args.url}")
    print(f"   目录: {out_dir}")
    print("=" * 60)

    files = detect_and_download(args.url, out_dir)

    print()
    if files:
        print(f"✅ 下载完成，共 {len(files)} 个文件:")
        for f in files:
            fpath = out_dir / f
            size = fpath.stat().st_size // 1024 if fpath.exists() else 0
            print(f"   {f}  ({size} KB)  →  {fpath}")
    else:
        print("❌ 下载失败或无文件")
        sys.exit(1)


if __name__ == "__main__":
    main()
