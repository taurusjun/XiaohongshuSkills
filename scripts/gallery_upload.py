#!/usr/bin/env python3
"""
上传已选图片到 Cloudinary，并将图片 URL 写入对应 Notion 页面 blocks。

用法:
    python scripts/gallery_upload.py
    python scripts/gallery_upload.py --dry-run
"""

import argparse
import hashlib
import io
import json
import os
import sys
import time
from pathlib import Path

import requests

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

CACHE_DIR = Path.home() / ".cache" / "xhs_images"
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


# ============ Cloudinary ============

def upload_local_jpeg(file_path: Path) -> str:
    """将本地图片转成 JPEG 上传到 Cloudinary，返回 CDN URL"""
    from PIL import Image

    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
    api_key = os.environ.get("CLOUDINARY_API_KEY")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET")
    if not all([cloud_name, api_key, api_secret]):
        print("  ❌ 缺少 Cloudinary 配置")
        return ""

    # 转 JPEG
    buf = io.BytesIO()
    try:
        img = Image.open(file_path).convert("RGB")
        img.save(buf, "JPEG", quality=92)
        buf.seek(0)
    except Exception as e:
        print(f"  ⚠️ 图片转换失败: {e}")
        return ""

    timestamp = int(time.time())
    params_str = f"timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(params_str.encode()).hexdigest()

    try:
        resp = requests.post(
            f"https://api.cloudinary.com/v1_1/{cloud_name}/image/upload",
            files={"file": (file_path.stem + ".jpg", buf, "image/jpeg")},
            data={"api_key": api_key, "timestamp": timestamp, "signature": signature},
            timeout=30,
        )
        if resp.status_code == 200:
            url = resp.json().get("secure_url", "")
            if url:
                return url
        print(f"  ❌ Cloudinary 上传失败: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"  ❌ 上传异常: {e}")
    return ""


# ============ Notion ============

def append_image_blocks(page_id: str, cloudinary_urls: list[str]):
    """在 Notion 页面末尾追加图片 blocks"""
    children = [
        {
            "type": "image",
            "image": {"type": "external", "external": {"url": url}},
        }
        for url in cloudinary_urls
    ]
    resp = requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        json={"children": children},
        timeout=10,
    )
    return resp.status_code == 200


def update_notion_image_urls(page_id: str, urls: list[str]):
    """将第一张图设为封面图属性（可选），其余写入 blocks"""
    # 如果需要更新封面图属性，取消注释：
    # if urls:
    #     requests.patch(
    #         f"https://api.notion.com/v1/pages/{page_id}",
    #         headers=NOTION_HEADERS,
    #         json={"properties": {"封面图": {"url": urls[0]}}},
    #         timeout=10,
    #     )
    return append_image_blocks(page_id, urls)


# ============ Main ============

def process_article(article_dir: Path, dry_run: bool) -> bool:
    meta_path = article_dir / "meta.json"
    selected_path = article_dir / "selected.json"
    uploaded_flag = article_dir / "uploaded.flag"

    if not meta_path.exists() or not selected_path.exists():
        return False
    if uploaded_flag.exists():
        print("  ⏭ 已上传，跳过")
        return False

    with open(meta_path) as f:
        meta = json.load(f)
    with open(selected_path) as f:
        selected = json.load(f).get("selected", [])

    if not selected:
        print("  — 无勾选图片，跳过")
        return False

    print(f"  notion_page_id: {meta.get('notion_page_id')}")
    print(f"  已选 {len(selected)} 张: {selected}")

    if dry_run:
        print("  [dry-run] 跳过上传")
        return True

    cloudinary_urls = []
    for fname in selected:
        fpath = article_dir / fname
        if not fpath.exists():
            print(f"  ⚠️ 文件不存在: {fname}")
            continue
        print(f"  上传 {fname}...")
        url = upload_local_jpeg(fpath)
        if url:
            cloudinary_urls.append(url)
            print(f"    → {url[:70]}")
        time.sleep(0.5)

    if not cloudinary_urls:
        print("  ❌ 全部上传失败")
        return False

    notion_id = meta.get("notion_page_id")
    if notion_id:
        ok = update_notion_image_urls(notion_id, cloudinary_urls)
        if ok:
            print(f"  ✅ 已写入 Notion blocks ({len(cloudinary_urls)} 张)")
        else:
            print(f"  ⚠️ Notion 写入失败")

    # 保存上传结果
    with open(article_dir / "uploaded.json", "w") as f:
        json.dump({"cloudinary_urls": cloudinary_urls}, f, ensure_ascii=False, indent=2)
    uploaded_flag.touch()
    return True


def main():
    parser = argparse.ArgumentParser(description="上传已选图片到 Cloudinary 并写入 Notion")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not NOTION_API_KEY and not args.dry_run:
        print("❌ NOTION_API_KEY 未设置")
        sys.exit(1)

    if not CACHE_DIR.exists():
        print(f"❌ 缓存目录不存在: {CACHE_DIR}")
        sys.exit(1)

    print("=" * 60)
    print("📤 gallery_upload — 上传已选图片到 Cloudinary + Notion")
    print("=" * 60)

    pending = [
        d for d in sorted(CACHE_DIR.iterdir())
        if d.is_dir()
        and (d / "meta.json").exists()
        and (d / "selected.json").exists()
        and not (d / "uploaded.flag").exists()
    ]

    if not pending:
        print("✅ 没有待上传的图集（先运行 gallery_preview.py 勾选）")
        return

    print(f"找到 {len(pending)} 个待上传图集\n")
    success = 0
    for article_dir in pending:
        meta_path = article_dir / "meta.json"
        with open(meta_path) as f:
            meta = json.load(f)
        print(f"━━━ {meta.get('title', '')[:40]}")
        if process_article(article_dir, args.dry_run):
            success += 1
        print()

    print("=" * 60)
    print(f"完成！成功 {success}/{len(pending)}")


if __name__ == "__main__":
    main()
