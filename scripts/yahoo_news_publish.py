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

def _get_pending_notion() -> list:
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


def _get_pending_sqlite() -> list:
    """SQLite: 返回伪 Notion page dict，兼容下游解析"""
    from sqlite_db import get_pending_publish as sqlite_pending
    rows = sqlite_pending(50)
    result = []
    for r in rows:
        tags = r.get('tags','').split(',') if isinstance(r.get('tags'), str) else r.get('tags',[])
        result.append({
            "_sqlite": True,
            "_key": r['key'],
            "id": r['key'],
            "properties": {
                "Name": {"title": [{"plain_text": r.get('title','')}]},
                "来源": {"rich_text": [{"plain_text": r.get('source','')}]},
                "发布时间": {"rich_text": [{"plain_text": r.get('pub_time','')}]},
                "原文链接": {"url": r.get('link','')},
                "封面图": {"url": r.get('image_url','')},
                "分类": {"select": {"name": r.get('category','')}},
                "标签": {"multi_select": [{"name": t} for t in tags]},
                "标题评分": {"number": r.get('title_score',0)},
                "内容评分": {"number": r.get('content_score',0)},
                "发布XHS": {"checkbox": bool(r.get('publish_xhs'))},
                "发布XHS时间": {"date": {"start": r.get('publish_time','')} if r.get('publish_time') else None},
            },
        })
    return result


def get_pending_pages() -> list:
    """获取 发布XHS=True 且 发布XHS时间 为空 的条目（支持 notion/sqlite/both）"""
    from config.yahoo_conf import STORAGE_BACKEND
    results = []
    if STORAGE_BACKEND == "notion":
        results = _get_pending_notion()
    if STORAGE_BACKEND == "sqlite":
        sqlite_rows = _get_pending_sqlite()
        # 去重（同一 key 只保留一份）
        seen_keys = set()
        merged = []
        for r in results:
            key = r.get('_key') or r.get('properties',{}).get('key',{}).get('rich_text',[{}])[0].get('plain_text','')
            if key not in seen_keys:
                seen_keys.add(key)
                merged.append(r)
        for r in sqlite_rows:
            if r['_key'] not in seen_keys:
                seen_keys.add(r['_key'])
                merged.append(r)
        results = merged
    return results


def get_page_media_blocks(page_id: str, is_sqlite: bool = False) -> tuple[list[str], list[str]]:
    """获取页面中图片和视频 URL。返回 (image_urls, video_urls)"""
    if is_sqlite:
        from sqlite_db import get_by_key
        import json
        row = get_by_key(page_id)
        if row:
            images = row.get('image_url','')
            gallery = row.get('gallery_images','') or '[]'
            try: gallery_imgs = json.loads(gallery) if isinstance(gallery, str) else gallery
            except: gallery_imgs = []
            img_urls = [images] if images else []
            img_urls.extend(gallery_imgs)
            video = row.get('video_path','') or row.get('gallery_video','')
            return img_urls, [video] if video else []
        return [], []

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


def get_page_content(page_id: str, is_sqlite: bool = False) -> tuple:
    """获取页面正文内容。
    返回 (正文, 词汇部分, 日文原标题, 日文摘要, 引流摘要, 短配文)
    """
    # SQLite: 内容已在行数据中
    if is_sqlite:
        from sqlite_db import get_by_key
        row = get_by_key(page_id)
        if row:
            return (row.get('content',''), '', row.get('title_ja',''), '',
                    row.get('summary',''), row.get('video_caption',''))
        return "", "", "", "", "", ""

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
            if "短配文" in text:
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

        # callout：短配文区域内的是短配文，否则是引流摘要
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


def mark_as_published(page_id: str, news_key: str = ""):
    """写入发布时间（Notion + SQLite 双写）"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    ok = False
    # Notion
    try:
        resp = requests.patch(
            f"https://api.notion.com/v1/pages/{page_id}",
            headers=NOTION_HEADERS,
            json={"properties": {"发布XHS时间": {"date": {"start": now}}}}
        )
        ok = resp.status_code == 200
    except Exception:
        pass
    # SQLite
    if news_key:
        from yahoo_common 
        if STORAGE_BACKEND == "sqlite":
            try:
                from sqlite_db import mark_published as sqlite_pub
                sqlite_pub(news_key, datetime.now().strftime("%Y-%m-%d %H:%M"))
            except ImportError:
                pass
    return ok


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
                   article_url: str = "", video_url: str = "",
                   preview: bool = False, headless: bool = True,
                   post_time: str = None, timing_jitter: float = 0.25,
                   reuse_existing_tab: bool = False) -> bool:
    """调用 publish_pipeline.py 发布到小红书。"""
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
    ]
    if headless:
        cmd.append("--headless")
    if preview:
        cmd.append("--preview")
    if post_time:
        cmd += ["--post-time", post_time]
    if timing_jitter != 0.25:
        cmd += ["--timing-jitter", str(timing_jitter)]
    if reuse_existing_tab:
        cmd.append("--reuse-existing-tab")

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
            cmd2 = [sys.executable, pipeline,
                    "--title", title, "--content", content,
                    "--image-urls", new_image_url]
            if headless: cmd2.append("--headless")
            if preview: cmd2.append("--preview")
            result = subprocess.run(cmd2, capture_output=True, text=True, timeout=120)
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
    parser.add_argument("--preview", action="store_true", help="仅填充内容不发布（预览模式）")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--post-time", type=str, default=None, help="定时发布时间")
    parser.add_argument("--timing-jitter", type=float, default=0.25, help="操作延迟系数（默认0.25）")
    parser.add_argument("--reuse-existing-tab", action="store_true", help="复用已有Chrome tab")
    args = parser.parse_args()

    print("=" * 60)
    print("📤 小红书发布器 - 从 Notion 读取待发布内容")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}")
    if args.auto:
        print("⚡ 自动模式：跳过确认")
    print()

    # 代理检测 + Chrome 启动
    from yahoo_common import check_chrome_cdp, check_proxy
    if not check_proxy():
        return
    if not check_chrome_cdp():
        return

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
        is_sqlite = page.get("_sqlite", False)
        print(f"━━━ [{i}/{len(pages)}] ━━━")
        print(f"标题: {info['title'][:40]}...")
        print(f"来源: {info['source']} | 分类: {info['category']}")

        # 获取正文和词汇
        content, vocab, original_title, ja_summary, summary, video_caption = get_page_content(page["id"], is_sqlite)
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

        xhs_content = "\n".join(parts)

        # 添加标签（最后一行 #标签1 #标签2 格式）
        import random
        # 必选标签
        must_tag = "#看新闻学日语"
        # 分类标签池（按内容分类选择，避免不相关标签混入）
        BASE_TAGS = [
            "#日语学习", "#日语N1", "#日语N2", "#日语单词",
            "#中日双语", "#中日翻译",
            "#日语学习打卡", "#日本新闻",
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
            "=LOVE": ["equallove","等爱","指原系","符号系"],
            "柏木由纪": ["柏木由纪"],
            "指原莉乃": ["指原莉乃"],
            "lesserafim": ["lesserafim", "炽", "韩国偶像", "Kpop", "韩国女团", "女团"],
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

        # 1. 先收集 KEYWORD_TAG_MAP 匹配的标签，优先展开（避免去重后优先级丢失）
        raw_tags = info.get("tags", [])
        mapped_tags: list[str] = []
        other_tags: list[str] = []
        for t in raw_tags:
            if t in KEYWORD_TAG_MAP:
                mapped_tags.append(t)
            else:
                other_tags.append(t)

        for t in mapped_tags:
            for mapped in KEYWORD_TAG_MAP[t]:
                add_tag(all_tags, seen_set, normalize_tag(mapped))

        for t in other_tags:
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
        gallery_urls, gallery_videos = get_page_media_blocks(page["id"], is_sqlite)
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

        # 视频模式：用短配文替换正文，保留同一批 tags
        if video_url and video_caption:
            xhs_content = f"{video_caption}\n{tags_str}"
            print(f"  🎬 短配文: {video_caption[:60]}...")
        elif video_url and not video_caption:
            print(f"  ⚠️ 无短配文，使用普通正文")

        # 发布
        print("📤 发布中...")
        if publish_to_xhs(full_title, xhs_content, all_images, info["link"], video_url=video_url,
                          preview=args.preview, headless=not args.no_headless,
                          post_time=args.post_time, timing_jitter=args.timing_jitter,
                          reuse_existing_tab=args.reuse_existing_tab):
            sqlite_key = page.get("_key", "") if is_sqlite else ""
            if mark_as_published(page["id"], sqlite_key):
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
