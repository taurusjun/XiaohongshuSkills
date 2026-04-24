#!/usr/bin/env python3
"""
从 Notion 下载已勾选「图集下载」的图集，打开预览选图后上传到 Cloudinary。

用法:
    python scripts/gallery_download.py
    python scripts/gallery_download.py --max-images 6
"""

import argparse
import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def has_pending_gallery() -> bool:
    cache_dir = Path.home() / ".cache" / "xhs_images"
    if not cache_dir.exists():
        return False
    return any(
        (d / "meta.json").exists() and not (d / "uploaded.flag").exists()
        for d in cache_dir.iterdir() if d.is_dir()
    )


def main():
    parser = argparse.ArgumentParser(description="下载图集并预览上传")
    parser.add_argument("--max-images", type=int, default=None, help="每篇最多下载图片数（默认 9）")
    args = parser.parse_args()

    # Step 1: 从 Notion 下载勾选了「图集下载」的图集
    fetch_args = [sys.executable, str(SCRIPT_DIR / "gallery_fetch.py")]
    if args.max_images:
        fetch_args += ["--max-images", str(args.max_images)]
    subprocess.run(fetch_args, check=True)

    # Step 2: 预览选图（确认后自动上传）
    if has_pending_gallery():
        subprocess.run([sys.executable, str(SCRIPT_DIR / "gallery_preview.py")])
    else:
        print("\n✅ 没有待预览的图集")


if __name__ == "__main__":
    main()
