#!/usr/bin/env python3
"""
独立媒体下载组件：输入任意 URL，自动识别类型并下载到本地。

支持类型：
  - YouTube 视频：youtube.com/watch?v=ID 或 youtu.be/ID
  - Instagram 帖子（图片 + 视频）：instagram.com/p/SHORTCODE/
  - Twitter / X 推文（图片 + 视频）：x.com/user/status/ID

前置条件：Chrome 需已通过 CDP 端口 9222 启动并登录对应平台（Instagram 必需）。

用法:
    python scripts/media_download.py <URL>
    python scripts/media_download.py <URL> --dir /tmp/output
    python scripts/media_download.py <URL> --no-cdp   # 跳过 CDP，仅尝试公开下载
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

from unified_media_downloader import (
    download_media,
    detect_url_type,
    extract_youtube_id,
    extract_instagram_shortcode,
    extract_tweet_id,
)

DEFAULT_CACHE = Path.home() / ".cache" / "xhs_media_download"


def _url_key(url: str) -> str:
    return hashlib.md5(url.encode()).hexdigest()[:12]


def main():
    parser = argparse.ArgumentParser(
        description="输入任意 URL，自动识别并下载 YouTube / Instagram / Twitter 媒体"
    )
    parser.add_argument("url", help="目标 URL")
    parser.add_argument("--dir", "-d", help="下载目标目录（默认 ~/.cache/xhs_media_download/<hash>/）")
    parser.add_argument("--no-subtitles", action="store_true", help="YouTube 跳过字幕烧录")
    parser.add_argument("--burn-subtitles", action="store_true", help="YouTube 烧录双语字幕")
    parser.add_argument("--trim-black-start", action="store_true", help="YouTube 黑屏裁剪")
    args = parser.parse_args()

    out_dir = Path(args.dir) if args.dir else DEFAULT_CACHE / _url_key(args.url)

    print("=" * 60)
    print(f"📥 media_download")
    print(f"   URL : {args.url}")
    print(f"   类型: {detect_url_type(args.url)}")
    print(f"   目录: {out_dir}")
    print("=" * 60)

    burn_subs = args.burn_subtitles and not args.no_subtitles
    files = download_media(
        args.url, output_dir=out_dir,
        burn_subtitles=burn_subs,
        trim_black_start=args.trim_black_start,
    )

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
