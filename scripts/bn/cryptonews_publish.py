#!/usr/bin/env python3
"""
从 Notion cryptonews 数据库读取勾选「发布BNSquare」的条目，发布到币安广场

用法:
    python scripts/bn/cryptonews_publish.py
    python scripts/bn/cryptonews_publish.py --dry-run
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
except ImportError:
    pass

from bn.square_publish import SquarePublisher

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = "34aaaa31a0aa806aa20bdd5f9a6d53e8"
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


# ============ Notion 读取 ============

def get_pending_pages() -> list:
    """获取 发布BNSquare=True 且 发布BNSquare时间 为空 的条目"""
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {
                "and": [
                    {"property": "发布BNSquare", "checkbox": {"equals": True}},
                    {"property": "发布BNSquare时间", "date": {"is_empty": True}},
                ]
            },
            "page_size": 20,
        },
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json().get("results", [])
    print(f"❌ 查询失败: {resp.status_code} {resp.text[:200]}")
    return []


def get_page_blocks(page_id: str) -> list:
    """获取页面正文 blocks"""
    resp = requests.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS,
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json().get("results", [])
    return []


def blocks_to_parts(blocks: list) -> tuple[str, str, list[str]]:
    """
    将 Notion blocks 解析为 (摘要, 要点列表)。
    - heading_3 → 跳过（章节标题）
    - paragraph → 摘要（第一段）
    - bulleted_list_item → 要点
    """
    summary_lines = []
    bullets = []
    in_summary = True  # 第一批段落作为摘要

    for block in blocks:
        btype = block.get("type", "")
        rich = block.get(btype, {}).get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rich).strip()

        if btype == "heading_3":
            continue
        elif btype == "paragraph" and text:
            if in_summary:
                summary_lines.append(text)
        elif btype == "bulleted_list_item" and text:
            in_summary = False
            bullets.append(text)

    summary = "\n".join(summary_lines)
    return summary, bullets


def build_post_content(title: str, summary: str, bullets: list[str], markdown: bool = False) -> str:
    """
    组装发布正文。
    markdown=True 时要点用 `- ` 前缀（文章模式 ProseMirror 自动转 bullet list）
    markdown=False 时用 `• ` 普通字符（普通帖子模式）
    """
    bullet_prefix = "- " if markdown else "• "
    parts = []

    if markdown:
        # 文章模式：正文不含标题，摘要直接接 bullet（ProseMirror 自带段落间距）
        if summary:
            parts.append(summary)
        for bullet in bullets:
            parts.append(f"{bullet_prefix}{bullet}")
    else:
        # 普通帖子模式：每段空行分隔
        parts.append(title)
        parts.append("")
        if summary:
            parts.append(summary)
            parts.append("")
        for bullet in bullets:
            parts.append(f"{bullet_prefix}{bullet}")
            parts.append("")
        while parts and parts[-1] == "":
            parts.pop()

    return "\n".join(parts)


def parse_page(page: dict) -> dict:
    props = page.get("properties", {})

    def get_text(name):
        return "".join(r.get("plain_text", "") for r in props.get(name, {}).get("rich_text", []))

    def get_title():
        return "".join(r.get("plain_text", "") for r in props.get("Name", {}).get("title", []))

    tokens = [t.get("name", "") for t in props.get("tokens", {}).get("multi_select", [])]
    if not tokens:
        tokens = ["BTC"]  # 默认 BTC

    return {
        "id": page["id"],
        "title": get_title(),
        "cdn_img": props.get("封面图", {}).get("url", ""),
        "tokens": tokens,
    }


def mark_published(page_id: str):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": {"发布BNSquare时间": {"date": {"start": now}}}},
        timeout=10,
    )


# ============ 下载封面图到本地 ============

def download_image(url: str) -> str:
    """下载图片并转成 JPEG，返回本地路径"""
    import tempfile
    import urllib.request
    from PIL import Image
    import io

    tmp_jpg = tempfile.mktemp(suffix=".jpg")
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        img.save(tmp_jpg, "JPEG", quality=92)
        return tmp_jpg
    except Exception as e:
        print(f"  ⚠️ 图片下载/转换失败: {e}")
        return ""


# ============ 主程序 ============

def main():
    parser = argparse.ArgumentParser(description="Notion cryptonews → 币安广场")
    parser.add_argument("--dry-run", action="store_true", help="不实际发布")
    parser.add_argument("--article", action="store_true", help="使用文章模式发布（有标题+封面+富文本）")
    args = parser.parse_args()

    if not NOTION_API_KEY:
        print("❌ NOTION_API_KEY 未设置")
        sys.exit(1)

    print("=" * 60)
    print("📤 cryptonews → 币安广场")
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}")
    print("=" * 60)

    pages = get_pending_pages()
    if not pages:
        print("✅ 没有待发布条目")
        return

    print(f"找到 {len(pages)} 条待发布\n")

    pub = SquarePublisher()
    pub.connect()

    for i, page in enumerate(pages, 1):
        info = parse_page(page)
        print(f"━━━ [{i}/{len(pages)}] {info['title'][:50]}")
        print(f"  tokens: {info['tokens']}")
        print(f"  封面图: {info['cdn_img'][:60] if info['cdn_img'] else '无'}")

        # 读取页面正文 blocks，组装发布格式
        blocks = get_page_blocks(page["id"])
        summary, bullets = blocks_to_parts(blocks)
        content_text = build_post_content(info["title"], summary, bullets, markdown=args.article)
        if not summary and not bullets:
            print("  ⚠️ 正文为空，跳过\n")
            continue

        print(f"  正文预览:\n{content_text[:200]}")

        # 下载封面图
        local_img = ""
        if info["cdn_img"]:
            print("  下载封面图...")
            local_img = download_image(info["cdn_img"])

        if args.dry_run:
            print("  [dry-run] 跳过发布\n")
            continue

        # 发布到币安广场
        print("  发布中...")
        if args.article:
            ok = pub.publish_article(
                title=info["title"],
                content=content_text,
                token_tags=info["tokens"],
                image_path=local_img,
            )
        else:
            ok = pub.publish(
                content=content_text,
                token_tags=info["tokens"],
                image_path=local_img,
            )

        if ok:
            mark_published(page["id"])
            print(f"  ✅ 发布成功，已记录时间\n")
        else:
            print(f"  ❌ 发布失败\n")

        # 临时图片清理
        if local_img and os.path.exists(local_img):
            os.remove(local_img)

        time.sleep(3)

    pub.close()
    print("=" * 60)
    print("完成！")


if __name__ == "__main__":
    main()
