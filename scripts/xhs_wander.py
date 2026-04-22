#!/usr/bin/env python3
"""
小红书自动闲逛脚本
- 按关键词搜索帖子
- 随机点赞 / 收藏 / 评论，模拟真人行为
- 支持多关键词、概率配置、评论模板

用法:
    python xhs_wander.py --keywords "日语学习" "AKB" --count 10
    python xhs_wander.py --keywords "日语学习" --like-prob 0.6 --bookmark-prob 0.3 --comment-prob 0.2
    python xhs_wander.py --keywords "日语学习" --dry-run
"""

import argparse
import json
import os
import random
import subprocess
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

# ============ 默认配置 ============

DEFAULT_LIKE_PROB = 0.5       # 点赞概率
DEFAULT_BOOKMARK_PROB = 0.25  # 收藏概率
DEFAULT_COMMENT_PROB = 0.15   # 评论概率（需要 LiteLLM）
DEFAULT_BROWSE_DELAY = (3, 8) # 浏览停留秒数区间（模拟阅读）
DEFAULT_ACTION_DELAY = (1, 3) # 操作间隔秒数区间

LITELLM_URL = os.environ.get("LITELLM_URL", "https://litellm-prod.toolsfdg.net")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "bedrock-claude-4-6-sonnet")

# 评论模板（无 LiteLLM 时随机选用）
COMMENT_TEMPLATES = [
    "学到了！",
    "太实用了，收藏慢慢看",
    "这个角度真的很棒！",
    "谢谢分享～",
    "正在学，感谢🙏",
    "好详细！",
    "跟着一起学习了",
    "涨知识了！",
]

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CDP_PUBLISH = os.path.join(SCRIPT_DIR, "cdp_publish.py")


# ============ 工具函数 ============

def run_cmd(args: list, timeout: int = 60) -> tuple[int, str, str]:
    result = subprocess.run(
        [sys.executable, CDP_PUBLISH] + args,
        capture_output=True, text=True, timeout=timeout
    )
    return result.returncode, result.stdout, result.stderr


def human_sleep(low: float, high: float, label: str = ""):
    secs = random.uniform(low, high)
    if label:
        print(f"  ⏱  {label} 等待 {secs:.1f}s...")
    time.sleep(secs)


def generate_comment(title: str, content_snippet: str = "") -> str:
    """用 LiteLLM 生成一条自然评论，失败则用模板"""
    if not LITELLM_API_KEY:
        return random.choice(COMMENT_TEMPLATES)
    try:
        import requests
        prompt = (
            f"请为以下小红书帖子写一条简短自然的中文评论（10-20字，口语化，不要emoji太多）：\n"
            f"标题：{title}\n"
            f"内容片段：{content_snippet[:100] if content_snippet else '（无）'}"
        )
        resp = requests.post(
            f"{LITELLM_URL}/chat/completions",
            headers={"Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={
                "model": LITELLM_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "max_tokens": 60,
            },
            timeout=15,
        )
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"  ⚠️ LiteLLM 评论生成失败: {e}")
    return random.choice(COMMENT_TEMPLATES)


# ============ 核心操作 ============

def search_feeds(keyword: str, sort_by: str = "最新") -> list:
    code, out, err = run_cmd(["search-feeds", "--keyword", keyword, "--sort-by", sort_by])
    if code != 0:
        print(f"  ❌ 搜索失败: {err[-200:]}")
        return []
    for line in out.splitlines():
        if line.startswith("SEARCH_FEEDS_RESULT:"):
            idx = out.index("SEARCH_FEEDS_RESULT:") + len("SEARCH_FEEDS_RESULT:")
            try:
                data = json.loads(out[idx:].strip())
                return data.get("feeds", [])
            except json.JSONDecodeError:
                pass
    return []


def do_like(feed_id: str, xsec_token: str, dry_run: bool) -> bool:
    if dry_run:
        print("  [dry] 点赞")
        return True
    code, out, err = run_cmd(["--reuse-existing-tab", "note-upvote", "--feed-id", feed_id, "--xsec-token", xsec_token])
    return code == 0


def do_bookmark(feed_id: str, xsec_token: str, dry_run: bool) -> bool:
    if dry_run:
        print("  [dry] 收藏")
        return True
    code, out, err = run_cmd(["--reuse-existing-tab", "note-bookmark", "--feed-id", feed_id, "--xsec-token", xsec_token])
    return code == 0


def do_comment(feed_id: str, xsec_token: str, comment: str, dry_run: bool) -> bool:
    if dry_run:
        print(f"  [dry] 评论: {comment}")
        return True
    code, out, err = run_cmd([
        "--reuse-existing-tab", "post-comment-to-feed",
        "--feed-id", feed_id,
        "--xsec-token", xsec_token,
        "--content", comment,
    ])
    return code == 0


# ============ 主逻辑 ============

def wander(
    keywords: list[str],
    count: int,
    like_prob: float,
    bookmark_prob: float,
    comment_prob: float,
    dry_run: bool,
):
    print("=" * 60)
    print("🚶 小红书自动闲逛")
    print(f"关键词: {', '.join(keywords)}  目标数: {count}")
    print(f"概率 — 点赞:{like_prob:.0%}  收藏:{bookmark_prob:.0%}  评论:{comment_prob:.0%}")
    if dry_run:
        print("⚠️  DRY-RUN 模式，不实际发送操作")
    print("=" * 60)

    stats = {"like": 0, "bookmark": 0, "comment": 0, "browse": 0, "skip": 0}

    # 每个关键词分配配额
    per_kw = max(1, count // len(keywords))
    remainder = count - per_kw * len(keywords)

    for ki, kw in enumerate(keywords):
        quota = per_kw + (1 if ki < remainder else 0)
        print(f"\n🔍 搜索「{kw}」，计划处理 {quota} 条")

        feeds = search_feeds(kw)
        if not feeds:
            print(f"  ⚠️ 未找到结果，跳过")
            continue

        # 随机打乱，取配额数
        random.shuffle(feeds)
        selected = feeds[:quota]

        for i, feed in enumerate(selected, 1):
            feed_id = feed.get("id", "")
            xsec_token = feed.get("xsecToken", "")
            title = feed.get("title", "（无标题）")

            print(f"\n  [{i}/{len(selected)}] {title[:40]}")

            if not feed_id or not xsec_token:
                print("  ⚠️ 缺少 feed_id/xsec_token，跳过")
                stats["skip"] += 1
                continue

            # 模拟浏览停留
            human_sleep(*DEFAULT_BROWSE_DELAY, "浏览")
            stats["browse"] += 1

            # 点赞
            if random.random() < like_prob:
                ok = do_like(feed_id, xsec_token, dry_run)
                print(f"  👍 点赞: {'✅' if ok else '❌'}")
                if ok:
                    stats["like"] += 1
                human_sleep(*DEFAULT_ACTION_DELAY)

            # 收藏
            if random.random() < bookmark_prob:
                ok = do_bookmark(feed_id, xsec_token, dry_run)
                print(f"  🔖 收藏: {'✅' if ok else '❌'}")
                if ok:
                    stats["bookmark"] += 1
                human_sleep(*DEFAULT_ACTION_DELAY)

            # 评论
            if random.random() < comment_prob:
                comment = generate_comment(title)
                ok = do_comment(feed_id, xsec_token, comment, dry_run)
                print(f"  💬 评论「{comment}」: {'✅' if ok else '❌'}")
                if ok:
                    stats["comment"] += 1
                human_sleep(*DEFAULT_ACTION_DELAY)

    print("\n" + "=" * 60)
    print(f"✅ 闲逛完成 | 浏览:{stats['browse']}  点赞:{stats['like']}  收藏:{stats['bookmark']}  评论:{stats['comment']}  跳过:{stats['skip']}")
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}")


# ============ CLI ============

def main():
    parser = argparse.ArgumentParser(description="小红书自动闲逛")
    parser.add_argument("--keywords", nargs="+", default=["日语学习"], help="搜索关键词列表")
    parser.add_argument("--count", type=int, default=10, help="总处理帖子数（均分到各关键词）")
    parser.add_argument("--like-prob", type=float, default=DEFAULT_LIKE_PROB, help="点赞概率 0-1")
    parser.add_argument("--bookmark-prob", type=float, default=DEFAULT_BOOKMARK_PROB, help="收藏概率 0-1")
    parser.add_argument("--comment-prob", type=float, default=DEFAULT_COMMENT_PROB, help="评论概率 0-1")
    parser.add_argument("--dry-run", action="store_true", help="仅模拟，不发送操作")
    args = parser.parse_args()

    wander(
        keywords=args.keywords,
        count=args.count,
        like_prob=args.like_prob,
        bookmark_prob=args.bookmark_prob,
        comment_prob=args.comment_prob,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
