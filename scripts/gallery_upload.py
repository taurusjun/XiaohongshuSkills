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


def upload_local_video(file_path: Path) -> str:
    """将本地视频上传到 Cloudinary，返回 CDN URL"""
    cloud_name = os.environ.get("CLOUDINARY_CLOUD_NAME")
    api_key = os.environ.get("CLOUDINARY_API_KEY")
    api_secret = os.environ.get("CLOUDINARY_API_SECRET")
    if not all([cloud_name, api_key, api_secret]):
        print("  ❌ 缺少 Cloudinary 配置")
        return ""

    timestamp = int(time.time())
    params_str = f"timestamp={timestamp}{api_secret}"
    signature = hashlib.sha1(params_str.encode()).hexdigest()

    try:
        with open(file_path, "rb") as f:
            resp = requests.post(
                f"https://api.cloudinary.com/v1_1/{cloud_name}/video/upload",
                files={"file": (file_path.name, f, "video/mp4")},
                data={"api_key": api_key, "timestamp": timestamp, "signature": signature},
                timeout=120,
            )
        if resp.status_code == 200:
            url = resp.json().get("secure_url", "")
            if url:
                return url
        print(f"  ❌ Cloudinary 视频上传失败: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        print(f"  ❌ 视频上传异常: {e}")
    return ""


# ============ Notion ============

def append_image_blocks(page_id: str, cloudinary_urls: list[str]):
    """在 Notion 页面末尾追加图片 blocks。
    每张图写一对：to_do(checkbox，默认不选中) + image block。
    在 Notion 中勾选 checkbox 即可在发布时包含该图片。
    """
    children = []
    for url in cloudinary_urls:
        # checkbox：默认不选中，文字显示图片序号，方便识别
        idx = len(children) // 2 + 1
        children.append({
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "text": {"content": f"图片 {idx:02d}"}}],
                "checked": False,
            },
        })
        children.append({
            "type": "image",
            "image": {"type": "external", "external": {"url": url}},
        })
    resp = requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        json={"children": children},
        timeout=10,
    )
    return resp.status_code == 200


def append_video_blocks(page_id: str, video_urls: list[str]):
    """在 Notion 页面末尾追加视频 blocks（to_do + video 成对）"""
    children = []
    for i, url in enumerate(video_urls):
        children.append({
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "text": {"content": f"视频 {i + 1:02d}"}}],
                "checked": False,
            },
        })
        children.append({
            "type": "video",
            "video": {"type": "external", "external": {"url": url}},
        })
    resp = requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        json={"children": children},
        timeout=10,
    )
    return resp.status_code == 200


def append_local_video_blocks(page_id: str, local_paths: list[str]):
    """YouTube 本地视频：写 to_do + code block，code block 存绝对路径供发布脚本读取"""
    children = []
    for i, path in enumerate(local_paths):
        children.append({
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "text": {"content": f"本地视频 {i + 1:02d}"}}],
                "checked": False,
            },
        })
        children.append({
            "type": "code",
            "code": {
                "rich_text": [{"type": "text", "text": {"content": path}}],
                "language": "plain text",
            },
        })
    resp = requests.patch(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        json={"children": children},
        timeout=10,
    )
    return resp.status_code == 200


def update_notion_image_urls(page_id: str, urls: list[str]):
    """将第一张图设为封面图属性（可选），其余写入 blocks"""
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

    image_urls = []
    video_urls = []
    youtube_local_only = meta.get("youtube_local_only", False)

    for fname in selected:
        fpath = article_dir / fname
        if not fpath.exists():
            print(f"  ⚠️ 文件不存在: {fname}")
            continue
        if fpath.suffix.lower() == ".mp4":
            if youtube_local_only:
                # YouTube 视频仅保留本地，不上传 Cloudinary，写绝对路径到 Notion
                local_path = str(fpath.resolve())
                video_urls.append(local_path)
                print(f"  📁 YouTube 视频保留本地: {local_path}")
            else:
                print(f"  上传 {fname}...")
                url = upload_local_video(fpath)
                if url:
                    video_urls.append(url)
                    print(f"    → [video] {url[:70]}")
                time.sleep(0.5)
        else:
            print(f"  上传 {fname}...")
            url = upload_local_jpeg(fpath)
            if url:
                image_urls.append(url)
                print(f"    → {url[:70]}")
            time.sleep(0.5)

    if not image_urls and not video_urls:
        print("  ❌ 全部上传失败")
        return False

    notion_id = meta.get("notion_page_id")
    if notion_id:
        if image_urls:
            ok = update_notion_image_urls(notion_id, image_urls)
            if ok:
                print(f"  ✅ 已写入 Notion image blocks ({len(image_urls)} 张)")
            else:
                print(f"  ⚠️ Notion image 写入失败")
        if video_urls:
            if youtube_local_only:
                ok = append_local_video_blocks(notion_id, video_urls)
                if ok:
                    print(f"  ✅ 已写入 Notion 本地视频路径 ({len(video_urls)} 个)")
                else:
                    print(f"  ⚠️ Notion 本地视频路径写入失败")
            else:
                ok = append_video_blocks(notion_id, video_urls)
                if ok:
                    print(f"  ✅ 已写入 Notion video blocks ({len(video_urls)} 个)")
                else:
                    print(f"  ⚠️ Notion video 写入失败")

    # 保存上传结果
    with open(article_dir / "uploaded.json", "w") as f:
        json.dump({"image_urls": image_urls, "video_urls": video_urls},
                  f, ensure_ascii=False, indent=2)
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
