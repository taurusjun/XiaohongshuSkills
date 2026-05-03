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


def get_page_media_blocks(page_id: str) -> tuple[list[str], list[str]]:
    """获取页面中图片和视频 URL。
    识别两种写法：
    1. 新格式（to_do + image/video 成对）：只取 checked=True 的 to_do 后面紧跟的 block
    2. 旧格式（裸 image block）：直接取所有 image URL（兼容旧数据）
    返回 (image_urls, video_urls)
    """
    resp = requests.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS
    )
    if resp.status_code != 200:
        return [], []

    blocks = resp.json().get("results", [])
    image_urls = []
    video_urls = []

    has_todo = any(b.get("type") == "to_do" for b in blocks)

    if has_todo:
        for i, block in enumerate(blocks):
            if block.get("type") != "to_do":
                continue
            if not block.get("to_do", {}).get("checked", False):
                continue
            if i + 1 >= len(blocks):
                continue
            next_block = blocks[i + 1]
            if next_block.get("type") == "image":
                img = next_block.get("image", {})
                url = img.get("external", {}).get("url") or img.get("file", {}).get("url", "")
                if url:
                    image_urls.append(url)
            elif next_block.get("type") == "video":
                vid = next_block.get("video", {})
                url = vid.get("external", {}).get("url") or vid.get("file", {}).get("url", "")
                if url:
                    video_urls.append(url)
            elif next_block.get("type") == "code":
                # YouTube 本地视频路径（绝对路径存在 code block 中）
                path = "".join(
                    r.get("plain_text", "")
                    for r in next_block.get("code", {}).get("rich_text", [])
                ).strip()
                if path and path.startswith("/"):
                    video_urls.append(path)
    else:
        for block in blocks:
            if block.get("type") == "image":
                img = block.get("image", {})
                url = img.get("external", {}).get("url") or img.get("file", {}).get("url", "")
                if url:
                    image_urls.append(url)

    return image_urls, video_urls


def get_page_content(page_id: str) -> tuple:
    """获取页面正文内容。
    返回 (正文, 词汇部分, 日文原标题, 日文摘要, 引流摘要, 视频配文)
    """
    resp = requests.get(
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        headers=NOTION_HEADERS
    )
    if resp.status_code != 200:
        return "", "", "", "", "", ""

    blocks = resp.json().get("results", [])
    lines = []
    vocab_lines = []
    original_title = ""
    ja_summary = ""
    in_vocab = False
    in_original = False
    in_video_caption = False
    title_captured = False

    summary_lines = []
    video_caption = ""

    for block in blocks:
        btype = block.get("type")
        rich = block.get(btype, {}).get("rich_text", [])
        text = "".join(r.get("plain_text", "") for r in rich)

        # heading_3 决定当前区域
        if btype == "heading_3":
            if "视频配文" in text:
                in_video_caption = True
                in_vocab = False
                in_original = False
            elif "词汇" in text:
                in_video_caption = False
                in_vocab = True
                in_original = False
                vocab_lines.append(f"\n{text}")
            elif "原文" in text:
                in_video_caption = False
                in_vocab = False
                in_original = True
            else:
                in_video_caption = False
                in_vocab = False
                in_original = False
                lines.append(f"\n{text}")
            continue

        # callout：视频配文区域内的是视频配文，否则是引流摘要
        if btype == "callout":
            if text:
                if in_video_caption:
                    video_caption = text
                else:
                    summary_lines.append(text)
            continue

        # 遇到原文链接部分，停止
        if "原文链接" in text:
            break

        if btype == "divider":
            in_video_caption = False
            lines.append("")
        elif btype == "bulleted_list_item":
            target = vocab_lines if in_vocab else lines
            target.append(f"• {text}")
        elif btype == "quote":
            target = vocab_lines if in_vocab else lines
            target.append(f"   💬 {text}")
        elif btype == "paragraph" and text:
            if in_original:
                if not title_captured:
                    original_title = text
                    title_captured = True
                else:
                    ja_summary = text
            elif not in_video_caption:
                target = vocab_lines if in_vocab else lines
                target.append(text)

    summary = summary_lines[0] if summary_lines else ""
    return (
        "\n".join(lines).strip(),
        "\n".join(vocab_lines).strip(),
        original_title,
        ja_summary,
        summary,
        video_caption,
    )


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
        "gallery_url": props.get("图集链接", {}).get("url", ""),
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

def _xhs_title_truncate(title: str, max_units: int = 20) -> str:
    """按小红书字数规则截断标题：英文字母/数字 2 个算 1 字，其余字符各算 1 字。"""
    units = 0.0
    for i, ch in enumerate(title):
        units += 0.5 if (ch.isascii() and (ch.isalpha() or ch.isdigit())) else 1.0
        if units > max_units:
            return title[:i]
    return title


def publish_to_xhs(title: str, content: str, image_urls: list[str] = None,
                   article_url: str = "", video_url: str = "") -> bool:
    """调用 publish_pipeline.py 发布到小红书。
    有 video_url 时走视频模式（--video-url），否则走图文模式（--image-urls）。
    """
    import subprocess
    if image_urls is None:
        image_urls = []

    title = _xhs_title_truncate(title)

    script_dir = os.path.dirname(os.path.abspath(__file__))
    pipeline = os.path.join(script_dir, "publish_pipeline.py")

    cmd = [
        sys.executable, pipeline,
        "--title", title,
        "--content", content,
        "--headless"
    ]

    if video_url:
        if video_url.startswith("/"):
            cmd += ["--video", video_url]
            print(f"  视频模式(本地): {video_url[:70]}")
        else:
            cmd += ["--video-url", video_url]
            print(f"  视频模式(URL): {video_url[:70]}")
    else:
        # XHS 最多 18 张
        effective_urls = image_urls[:18]
        if effective_urls:
            cmd += ["--image-urls"] + effective_urls
            print(f"  配图 {len(effective_urls)} 张: {effective_urls[0][:60]}...")
        else:
            print(f"  ⚠️ 未找到封面图，发布可能失败")

    print(f"  执行发布命令...")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)

    if result.returncode == 0:
        return True

    # 图片 URL 过期时降级重抓封面图（仅图文模式）
    if not video_url and "All image downloads failed" in result.stderr and article_url:
        print("  ⚠️ 图片URL已过期，重新抓取...")
        new_image_url = fetch_article_image(article_url)
        if new_image_url:
            print(f"  新配图: {new_image_url[:60]}...")
            cmd = [
                sys.executable, pipeline,
                "--title", title,
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
    parser.add_argument("--auto", action="store_true", help="一键发布，跳过逐条确认")
    parser.add_argument("--force", action="store_true", help="忽略图集检查，直接发布")
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
        content, vocab, original_title, ja_summary, summary, video_caption = get_page_content(page["id"])
        if not content:
            print("⚠️ 正文为空，跳过\n")
            continue

        # 拼装发布内容
        full_title = info['title']

        # 构建小红书正文
        parts = []

        # 引流摘要（如有）作为开头
        if summary:
            parts.append(summary)
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

        xhs_content = "\n".join(parts)

        # 添加标签（最后一行 #标签1 #标签2 格式）
        import random
        # 必选标签
        must_tag = "#看新闻学日语"
        # 分类标签池（按内容分类选择，避免不相关标签混入）
        BASE_TAGS = [
            "#日语学习", "#日语N1", "#日语N2", "#日语单词",
            "#中日双语", "#中日翻译",
            "#日语学习打卡", "#日本新闻", "#日本热点",
            "#日本文化", "#日本生活",
        ]
        FASHION_TAGS = [
            "#日系穿搭", "#日本穿搭", "#日系风格",
            "#穿搭分享", "#今日穿搭",
        ]
        BEAUTY_TAGS = [
            "#日本化妆", "#日系妆容", "#日本美妆",
            "#日本护肤", "#护肤分享", "#化妆教程",
        ]

        # 根据内容标签判断分类，选对应的标签池
        existing_tag_str = " ".join(info.get("tags", []))
        is_fashion = any(k in existing_tag_str for k in ["穿搭", "ファッション", "コーデ", "fashion"])
        is_beauty = any(k in existing_tag_str for k in ["メイク", "コスメ", "スキンケア", "美妆", "化妆", "护肤"])

        if is_fashion:
            hot_tags = BASE_TAGS[:6] + FASHION_TAGS
        elif is_beauty:
            hot_tags = BASE_TAGS[:6] + BEAUTY_TAGS
        else:
            hot_tags = BASE_TAGS
        # 标签规范化映射（日文/繁体 → 中文）
        TAG_NORMALIZE = {
            "コスプレ": "cosplay", "コスプ": "cosplay",
            # 不再转换 AKB48/乃木坂46/欅坂46，保留完整形式
            "鳴潮": "鸣潮", "原神": "原神", "崩壊": "崩坏", "スターレイル": "星穹铁道",
            "アニメ": "动漫", "マンガ": "漫画", "ゲーム": "游戏",
            "中東": "中东", "政治": "时政",
            # 时尚美妆
            "ファッション": "日系穿搭", "コーデ": "穿搭分享", "おしゃれ": "日系风格",
            "メイク": "日系妆容", "コスメ": "日本美妆", "スキンケア": "日本护肤",
            "ビューティー": "护肤分享", "トレンド": "日本潮流",
        }

# 关键词到发布标签的映射（与 yahoo_news_auto.py 保持一致）
        KEYWORD_TAG_MAP = {
            "AKB": ["AKB48", "akb48"],
            "乃木坂": ["乃木坂", "乃木坂46"],
            "欅坂": ["欅坂", "欅坂46", "樱坂", "樱坂46"],
            "伊織もえ": ["伊織もえ", "伊织萌", "きゅるん"],
            "えなこ": ["えなこ", "enako"],
            "アークナイツ": ["明日方舟"],
            "辻野かなみ": ["超心宣", "超ときめき宣伝部", "超とき宣", "辻野かなみ"],
            "≠ME": ["notequalme","指原系","符号系"],
        }

        def normalize_tag(t: str) -> str:
            return TAG_NORMALIZE.get(t, t)

        def add_tag(lst: list[str], seen_set: set[str], tag: str):
            tag = tag.lstrip("#")
            if tag not in seen_set:
                seen_set.add(tag)
                lst.append(tag)

        seen_set: set[str] = set()
        all_tags: list[str] = []

        # 1. KEYWORD_TAG_MAP 展开（最高优先级）
        raw_tags = info.get("tags", [])
        for t in raw_tags:
            if t in KEYWORD_TAG_MAP:
                for mapped in KEYWORD_TAG_MAP[t]:
                    add_tag(all_tags, seen_set, normalize_tag(mapped))
            else:
                add_tag(all_tags, seen_set, normalize_tag(t))

        # 2. 必选标签
        add_tag(all_tags, seen_set, must_tag)

        # 3. 随机热门标签（补足）
        random_hot = random.sample(hot_tags, min(4, len(hot_tags)))
        for t in random_hot:
            add_tag(all_tags, seen_set, t)

        tags_str = " ".join(f"#{t}" for t in all_tags[:10])
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

        # 封面图
        image_url = info.get("image_url", "")
        if not image_url and info["link"]:
            print("  封面图不在 Notion，重新抓取...")
            image_url = fetch_article_image(info["link"])

        # 图集图片 / 视频（gallery_upload 写入的 blocks）
        gallery_urls, gallery_videos = get_page_media_blocks(page["id"])
        if gallery_urls:
            print(f"  图集图片: {len(gallery_urls)} 张")
        if gallery_videos:
            print(f"  图集视频: {len(gallery_videos)} 个")

        # 有图集链接但图片/视频未下载 → 提醒
        if info.get("gallery_url") and not gallery_urls and not gallery_videos and not args.force:
            print(f"  ⚠️  此文章有图集链接但图片尚未下载：")
            print(f"      {info['gallery_url']}")
            print(f"  先运行 gallery_download.py 下载图集，或加 --force 强制发布")
            try:
                ans = input("  强制发布？(y/N): ").strip().lower()
            except EOFError:
                ans = "n"
            if ans != "y":
                print("  跳过\n")
                continue

        # 合并：封面图在前，图集在后，去重，最多 18 张
        all_images = []
        if image_url:
            all_images.append(image_url)
        for u in gallery_urls:
            if u not in all_images:
                all_images.append(u)
        all_images = all_images[:18]

        # 视频优先：有视频时忽略图集图片走视频模式
        video_url = gallery_videos[0] if gallery_videos else ""

        # 视频模式：用视频配文替换正文，保留同一批 tags
        if video_url and video_caption:
            xhs_content = f"{video_caption}\n{tags_str}"
            print(f"  🎬 视频配文: {video_caption[:60]}...")
        elif video_url and not video_caption:
            print(f"  ⚠️ 无视频配文，使用普通正文")

        # 发布
        print("📤 发布中...")
        if publish_to_xhs(full_title, xhs_content, all_images, info["link"], video_url=video_url):
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
