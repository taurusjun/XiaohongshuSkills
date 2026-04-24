#!/usr/bin/env python3
"""
Yahoo 新闻 → 小红书完整发布流程：
  1. gallery_fetch      — 从 Notion 检测 Yahoo 文章图集，下载到本地
  2. gallery_preview    — CDP 浏览器预览，人工勾选图片后继续
  3. gallery_upload     — 上传到 Cloudinary，写入 Notion blocks
  4. yahoo_news_publish — CDP 自动发布到小红书（封面图 + 图集）

用法:
    python scripts/xhs_news_pipeline.py
    python scripts/xhs_news_pipeline.py --fetch-limit 10
    python scripts/xhs_news_pipeline.py --max-images 6      # 每篇文章最多下载 N 张图
    python scripts/xhs_news_pipeline.py --skip-fetch        # 跳过抓取，直接预览已缓存图集
    python scripts/xhs_news_pipeline.py --skip-gallery      # 跳过图集全流程（fetch+preview），直接发布
    python scripts/xhs_news_pipeline.py --skip-publish      # 只处理图集，不发布
    python scripts/xhs_news_pipeline.py --auto              # 发布时跳过逐条确认
    python scripts/xhs_news_pipeline.py --dry-run           # 全程不实际上传/发布
"""

import argparse
import subprocess
import sys
import os
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent


def run(script: str, extra_args: list[str] = None, check=True) -> int:
    cmd = [sys.executable, str(SCRIPT_DIR / script)] + (extra_args or [])
    print(f"\n{'='*60}")
    print(f"▶ {' '.join(cmd)}")
    print("=" * 60)
    result = subprocess.run(cmd)
    if check and result.returncode != 0:
        print(f"\n❌ {script} 退出码 {result.returncode}，流程中止")
        sys.exit(result.returncode)
    return result.returncode


def has_pending_gallery() -> bool:
    """是否有待预览的图集（有 meta.json 且无 uploaded.flag）"""
    cache_dir = Path.home() / ".cache" / "xhs_images"
    if not cache_dir.exists():
        return False
    for d in cache_dir.iterdir():
        if (d / "meta.json").exists() and not (d / "uploaded.flag").exists():
            return True
    return False


def has_pending_upload() -> bool:
    """是否有已勾选待上传（有 selected.json 且无 uploaded.flag）"""
    cache_dir = Path.home() / ".cache" / "xhs_images"
    if not cache_dir.exists():
        return False
    for d in cache_dir.iterdir():
        if (d / "selected.json").exists() and not (d / "uploaded.flag").exists():
            return True
    return False


def main():
    parser = argparse.ArgumentParser(description="图集发布完整流程")
    parser.add_argument("--fetch-limit", type=int, default=20, help="gallery_fetch 扫描条数")
    parser.add_argument("--max-images", type=int, default=None, help="每篇文章最多下载图片数（默认 9）")
    parser.add_argument("--skip-fetch", action="store_true", help="跳过抓取，直接预览已缓存图集")
    parser.add_argument("--skip-gallery", action="store_true", help="跳过图集全流程（fetch+preview），直接发布")
    parser.add_argument("--skip-publish", action="store_true", help="跳过 XHS 发布")
    parser.add_argument("--dry-run", action="store_true", help="不实际上传/发布")
    parser.add_argument("--auto", action="store_true", help="XHS 发布跳过确认")
    args = parser.parse_args()

    print("🚀 图集发布流程启动")

    # Step 1: 抓取图集
    if args.skip_gallery:
        print("\n⏭ 跳过图集流程（fetch + preview）")
    else:
        if not args.skip_fetch:
            fetch_args = ["--limit", str(args.fetch_limit)]
            if args.max_images:
                fetch_args += ["--max-images", str(args.max_images)]
            run("gallery_fetch.py", fetch_args)
        else:
            print("\n⏭ 跳过 gallery_fetch")

        # Step 2: 预览选图 + 上传（gallery_preview 确认后自动调用 gallery_upload）
        if has_pending_gallery():
            print("\n📷 打开预览，请勾选要上传的图片后点「确认上传」...")
            run("gallery_preview.py")
        else:
            print("\n✅ 没有待预览的图集，跳过")

    # Step 4: 发布到小红书
    if not args.skip_publish:
        publish_args = []
        if args.auto:
            publish_args.append("--auto")
        run("yahoo_news_publish.py", publish_args, check=False)
    else:
        print("\n⏭ 跳过 XHS 发布")

    print("\n" + "=" * 60)
    print("✅ 流程完成")


if __name__ == "__main__":
    main()
