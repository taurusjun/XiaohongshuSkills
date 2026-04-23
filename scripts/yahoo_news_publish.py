#!/usr/bin/env python3
"""
从 Notion 读取已勾选「发布XHS」的新闻，自动发布到小红书
- 只发布 发布XHS=True 且 发布XHS时间 为空 的条目
- 发布成功后写入 发布XHS时间
"""

import requests
import json
import sys
import os
import time
from datetime import datetime, timezone

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

# ============ 配置 ============

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}


# ============ Notion 查询 ============

def get_pending_pages() -> list:
    """获取 发布XHS=True 且 发布XHS时间 为空 的条目"""
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {
                "and": [
                    {"property": "发布XHS", "checkbox": {"equals": True}},
                    {"property": "发布XHS时间", "date": {"is_empty": True}}
                ]
            },
            "page_size": 50
        }
    )
    if resp.status_code == 200:
        return resp.json().get("results", [])
    print(f"❌ 查询失败: {resp.status_code} {resp.text}")
    return []


def get_page_content(page_id: str) -> tuple:
    """获取页面正文内容，返回 (正文, 词汇部分, 日文原标题, 日文摘要)"""
    resp = requests.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS
    )
    if resp.status_code != 200:
        return "", "", "", ""

    blocks = resp.json().get("results", [])
    lines = []
    vocab_lines = []
    original_title = ""
    ja_summary = ""
    in_vocab = False
    in_original = False
    title_captured = False

    for block in blocks:
        btype = block.get("type")
        rich = block.get(btype, {}).get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rich)

        # 遇到原文链接部分，停止
        if "原文链接" in text:
            break

        if btype == "heading_3":
            if "词汇" in text:
                in_vocab = True
                in_original = False
                vocab_lines.append(f"\n{text}")
            elif "原文" in text:
                in_vocab = False
                in_original = True
            else:
                in_vocab = False
                in_original = False
                lines.append(f"\n{text}")
        elif btype == "bulleted_list_item":
            target = vocab_lines if in_vocab else lines
            target.append(f"• {text}")
        elif btype == "quote":
            # 例句是 quote 类型
            target = vocab_lines if in_vocab else lines
            target.append(f"   💬 {text}")
        elif btype == "paragraph" and text:
            if in_original:
                if not title_captured:
                    original_title = text
                    title_captured = True
                else:
                    ja_summary = text
            else:
                target = vocab_lines if in_vocab else lines
                target.append(text)
        elif btype == "divider":
            lines.append("")

    return "\n".join(lines).strip(), "\n".join(vocab_lines).strip(), original_title, ja_summary


def mark_as_published(page_id: str):
    """写入发布时间"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": {"发布XHS时间": {"date": {"start": now}}}}
    )
    return resp.status_code == 200


def parse_page(page: dict) -> dict:
    """解析 Notion 页面为发布数据"""
    props = page.get("properties", {})

    def get_text(prop_name):
        prop = props.get(prop_name, {})
        rich = prop.get("rich_text", [])
        return "".join(r.get("plain_text", "") for r in rich)

    def get_title():
        title = props.get("Name", {}).get("title", [])
        return "".join(r.get("plain_text", "") for r in title)

    return {
        "id": page["id"],
        "title": get_title(),
        "source": get_text("来源"),
        "pub_time": get_text("发布时间"),
        "link": props.get("原文链接", {}).get("url", ""),
        "image_url": props.get("封面图", {}).get("url", ""),
        "category": props.get("分类", {}).get("select", {}).get("name", ""),
        "tags": [t.get("name", "") for t in props.get("标签", {}).get("multi_select", [])],
    }


# ============ 图片抓取 ============

def fetch_article_image(url: str) -> str:
    """从 Yahoo 新闻文章页抓取封面图 URL"""
    try:
        from bs4 import BeautifulSoup
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        # 优先取 og:image
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            return og["content"]

        # 备选：文章内第一张图
        img = soup.select_one("article img, .article img, figure img")
        if img and img.get("src"):
            return img["src"]

    except Exception as e:
        print(f"  ⚠️ 抓取封面图失败: {e}")
    return ""


# ============ XHS 发布 ============

def publish_to_xhs(title: str, content: str, image_url: str = "", article_url: str = "") -> bool:
    """调用 publish_pipeline.py 发布到小红书"""
    import subprocess
    script_dir = os.path.dirname(os.path.abspath(__file__))
    pipeline = os.path.join(script_dir, "publish_pipeline.py")

    cmd = [
        sys.executable, pipeline,
        "--title", title[:20],
        "--content", content,
        "--headless"
    ]

    if image_url:
        cmd += ["--image-urls", image_url]
        print(f"  配图: {image_url[:60]}...")
    else:
        print(f"  ⚠️ 未找到封面图，发布可能失败")

    print(f"  执行发布命令...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

    if result.returncode == 0:
        return True

    # 如果图片下载失败，尝试重新抓取
    if "All image downloads failed" in result.stderr and article_url:
        print("  ⚠️ 图片URL已过期，重新抓取...")
        new_image_url = fetch_article_image(article_url)
        if new_image_url:
            print(f"  新配图: {new_image_url[:60]}...")
            cmd = [
                sys.executable, pipeline,
                "--title", title[:20],
                "--content", content,
                "--headless",
                "--image-urls", new_image_url
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if result.returncode == 0:
                return True

    print(f"  发布失败:\n{result.stderr[-500:]}")
    return False


# ============ 主程序 ============

def main():
    import argparse
    parser = argparse.ArgumentParser(description="小红书发布器")
    parser.add_argument("--auto", action="store_true", help="一键发布，跳过确认")
    args = parser.parse_args()

    print("=" * 60)
    print("📤 小红书发布器 - 从 Notion 读取待发布内容")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}")
    if args.auto:
        print("⚡ 自动模式：跳过确认")
    print()

    # 查询待发布条目
    print("📡 查询 Notion 待发布内容...")
    pages = get_pending_pages()

    if not pages:
        print("✅ 没有待发布内容（发布XHS=True 且未发布）")
        return

    print(f"找到 {len(pages)} 条待发布\n")

    # 逐条处理
    for i, page in enumerate(pages, 1):
        info = parse_page(page)
        print(f"━━━ [{i}/{len(pages)}] ━━━")
        print(f"标题: {info['title'][:40]}...")
        print(f"来源: {info['source']} | 分类: {info['category']}")

        # 获取正文和词汇
        content, vocab, original_title, ja_summary = get_page_content(page["id"])
        if not content:
            print("⚠️ 正文为空，跳过\n")
            continue

        # 拼装发布内容
        full_title = info['title']
        short_title = full_title[:20]

        # 构建小红书正文
        parts = []

        # 标题
        parts.append(f"📰 {full_title}")
        parts.append("")

        # 新闻要点
        parts.append(content)
        parts.append("")

        # 词汇部分
        if vocab:
            parts.append("─" * 15)
            parts.append("📝 今日词汇 (N1/N2)")
            parts.append("")
            # 重新格式化词汇
            vocab_lines = []
            for line in vocab.split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("📝") or "词汇" in line:
                    continue  # 跳过标题
                if line.startswith("例句"):
                    vocab_lines.append(f"   💬 {line}")
                else:
                    vocab_lines.append(line)
            parts.append("\n".join(vocab_lines))
            parts.append("")

        # 原文信息
        if original_title or ja_summary:
            parts.append("─" * 15)
            parts.append("📰 原文")
            parts.append("")
            if original_title:
                parts.append(original_title)
                parts.append("")  # 添加空行
            if ja_summary:
                parts.append(ja_summary)
                parts.append("")  # 添加空行

        parts.append(f"原文链接：{info['link']}")

        xhs_content = "\n".join(parts)

        # 添加标签（最后一行 #标签1 #标签2 格式）
        import random
        # 必选标签
        must_tag = "#看新闻学日语"
        # 其他热门标签库
        hot_tags = [
            "#日语学习", "#日语N1", "#日语N2", "#日语单词",
            "#中日双语", "#中日对照", "#中日翻译",
            "#日本新闻", "#日本热点", "#日本资讯",
            "#日语学习打卡", "#日本文化", "#日本生活"
        ]
        # 标签规范化映射（日文/繁体 → 中文）
        TAG_NORMALIZE = {
            "コスプレ": "cosplay", "コスプ": "cosplay",
            "AKB48": "AKB", "乃木坂46": "乃木坂", "欅坂46": "欅坂",
            "鳴潮": "鸣潮", "原神": "原神", "崩壊": "崩坏", "スターレイル": "星穹铁道",
            "アニメ": "动漫", "マンガ": "漫画", "ゲーム": "游戏",
            "中東": "中东", "政治": "时政",
        }

        def normalize_tag(t: str) -> str:
            return TAG_NORMALIZE.get(t, t)

        # 从 Notion 标签 + 必选标签 + 随机1-2个热门标签
        all_tags = [normalize_tag(t) for t in info.get("tags", [])]
        if must_tag not in all_tags:
            all_tags.append(must_tag)
        random_hot = random.sample(hot_tags, min(2, len(hot_tags)))
        for t in random_hot:
            if t not in all_tags:
                all_tags.append(t)
        tags_str = " ".join(f"#{tag}" if not tag.startswith("#") else tag for tag in all_tags[:8])
        xhs_content = f"{xhs_content}\n{tags_str}"

        print(f"正文预览: {content[:80]}...")
        print()

        # 确认发布
        if args.auto:
            choice = "y"
        else:
            print(f"是否发布此条？(y/n/q退出): ", end="")
            try:
                choice = input().strip().lower()
            except EOFError:
                choice = "y"

        if choice == "q":
            print("已退出")
            break
        elif choice != "y":
            print("跳过\n")
            continue

        # 从 Notion 读取封面图，没有再抓取
        image_url = info.get("image_url", "")
        if not image_url and info["link"]:
            print("  封面图不在 Notion，重新抓取...")
            image_url = fetch_article_image(info["link"])
        if image_url:
            print(f"  封面图: {image_url[:60]}...")

        # 发布
        print("📤 发布中...")
        if publish_to_xhs(short_title, xhs_content, image_url, info["link"]):
            if mark_as_published(page["id"]):
                print(f"✅ 发布成功，已记录时间\n")
            else:
                print(f"✅ 发布成功，但更新时间失败\n")
        else:
            print(f"❌ 发布失败\n")

        time.sleep(3)

    print("=" * 60)
    print("完成！")


if __name__ == "__main__":
    main()
