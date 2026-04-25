#!/usr/bin/env python3
"""
小红书自动闲逛脚本
- search 模式：按关键词搜索帖子，在 search_result 页点击 card
- explore 模式：在 /explore 发现页点击 card
- 全程模拟鼠标：点击 card 打开 modal → 在 modal 里点赞/收藏/评论 → ESC 关闭
- 不使用 Page.navigate 到 /explore/{id}，避免 SPA 拦截
- 支持多关键词、概率配置、评论模板

用法:
    python xhs_wander.py --keywords "日语学习" "AKB" --count 10
    python xhs_wander.py --keywords "日语学习" --like-prob 0.6 --bookmark-prob 0.3
    python xhs_wander.py --page explore --count 10
    python xhs_wander.py --dry-run
"""

import argparse
import os
import random
import sys
import time
from datetime import datetime

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from cdp_publish import XiaohongshuPublisher, CDPError  # noqa: E402

MY_USER_ID = os.environ.get("XHS_MY_USER_ID", "")

# ============ 默认配置 ============

DEFAULT_LIKE_PROB     = 0.25
DEFAULT_BOOKMARK_PROB = 0.10
DEFAULT_COMMENT_PROB  = 0.05
DEFAULT_BROWSE_DELAY  = (6, 12)
DEFAULT_ACTION_DELAY  = (3, 6)
DEFAULT_NOTE_DELAY    = (8, 12)
MAX_LIKES_PER_SESSION = 10

LITELLM_URL     = os.environ.get("LITELLM_URL", "")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL   = os.environ.get("LITELLM_MODEL", "")

COMMENT_TEMPLATES = [
    "学到了！",
    "写得好！",
    "谢谢分享～",
    "这个角度真的很棒！",
]

RISK_KEYWORDS = ["主页不见了", "你的主页", "账号异常", "操作频繁", "请稍后再试"]
INACCESSIBLE_KEYWORDS = [
    "当前笔记暂时无法浏览", "你访问的页面不见了", "已被删除",
    "内容不存在", "笔记不存在", "已失效", "私密笔记",
]


# ============ 工具函数 ============

def human_sleep(low: float, high: float, label: str = ""):
    secs = random.uniform(low, high)
    if label:
        print(f"  ⏱  {label} 等待 {secs:.1f}s...")
    time.sleep(secs)


def generate_comment(title: str, content_snippet: str = "") -> str:
    if not LITELLM_API_KEY:
        return random.choice(COMMENT_TEMPLATES)
    try:
        import requests as _req
        prompt = (
            f"请为以下小红书帖子写一条简短自然的中文评论（10-20字，口语化，不要emoji太多）：\n"
            f"标题：{title}\n"
            f"内容片段：{content_snippet[:100] if content_snippet else '（无）'}"
        )
        resp = _req.post(
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
            content = resp.json()["choices"][0]["message"].get("content") or ""
            if content.strip():
                return content.strip()
    except Exception as e:
        print(f"  ⚠️ LiteLLM 评论生成失败: {e}")
    return random.choice(COMMENT_TEMPLATES)


def get_dom_feed_info(pub: XiaohongshuPublisher) -> dict[str, str]:
    """Return {feed_id: title} mapping from cards currently rendered in the DOM.

    Titles extracted from the card's .title element, <img alt="">, or first
    text line — in that priority order.  This is used as a fallback when the
    API response doesn't include a displayTitle (e.g. video notes).
    """
    result = pub._evaluate(r"""
(function(){
    var sections = Array.from(document.querySelectorAll('section.note-item'));
    var out = {};
    sections.forEach(function(sec){
        var a = sec.querySelector('a[href*="/explore/"]');
        var m = a ? a.href.match(/\/explore\/([a-f0-9]{20,})/) : null;
        if (!m) return;
        var fid = m[1];
        var t = (sec.querySelector('.title') || {}).innerText;
        if (!t) t = (sec.querySelector('img') || {}).alt;
        if (!t) t = (sec.innerText || '').trim().split('\n')[0];
        if (t) t = t.trim();
        out[fid] = t || '';
    });
    return out;
})()
""")
    return result if isinstance(result, dict) else {}


def check_page_accessible(pub: XiaohongshuPublisher) -> bool:
    """Return False if the modal shows an inaccessible/deleted note page."""
    text = pub._evaluate(
        "(function(){ return document.body ? document.body.innerText : ''; })()"
    ) or ""
    return not any(k in text for k in INACCESSIBLE_KEYWORDS)


def check_risk_page(pub: XiaohongshuPublisher) -> bool:
    """Return True if a risk-control page is detected."""
    text = pub._evaluate(
        "(function(){ return document.body ? document.body.innerText : ''; })()"
    ) or ""
    return any(k in text for k in RISK_KEYWORDS)


def process_note_in_modal(
    pub: XiaohongshuPublisher,
    title: str,
    like_prob: float,
    bookmark_prob: float,
    comment_prob: float,
    stats: dict,
    dry_run: bool,
) -> bool:
    """执行 modal 内的点赞/收藏/评论操作，返回是否触发风控。"""
    if dry_run:
        if random.random() < like_prob:
            print("  👍 点赞: [dry]")
        if random.random() < bookmark_prob:
            print("  🔖 收藏: [dry]")
        return False

    # 检查笔记是否可访问
    if not check_page_accessible(pub):
        print("  ⏭  帖子不可访问，关闭跳过")
        pub.close_note_modal()
        stats["skip"] += 1
        return False

    if check_risk_page(pub):
        print("  🚨 检测到风控提示，停止操作！")
        return True  # risk triggered

    acted = False  # track whether any action was taken

    # 点赞
    if stats["like"] < MAX_LIKES_PER_SESSION and random.random() < like_prob:
        try:
            res = pub.toggle_like_in_modal(desired=True)
            changed = res.get("changed", False)
            if not res.get("ok"):
                print("  👍 点赞: ❌")
            elif changed:
                print("  👍 点赞: ✅")
                stats["like"] += 1
                acted = True
            else:
                print("  👍 点赞: 👍 已赞")
        except CDPError as e:
            print(f"  👍 点赞: ❌ ({e})")
        human_sleep(*DEFAULT_ACTION_DELAY)

    # 收藏
    if random.random() < bookmark_prob:
        try:
            res = pub.toggle_bookmark_in_modal(desired=True)
            changed = res.get("changed", False)
            if not res.get("ok"):
                print("  🔖 收藏: ❌")
            elif changed:
                print("  🔖 收藏: ✅")
                stats["bookmark"] += 1
                acted = True
            else:
                print("  🔖 收藏: 🔖 已收藏")
        except CDPError as e:
            print(f"  🔖 收藏: ❌ ({e})")
        human_sleep(*DEFAULT_ACTION_DELAY)

    # 评论
    if random.random() < comment_prob:
        comment_text = generate_comment(title)
        try:
            ok = pub.post_comment_in_modal(comment_text)
            print(f"  💬 评论「{comment_text}」: {'✅' if ok else '❌'}")
            if ok:
                stats["comment"] += 1
                acted = True
        except CDPError as e:
            print(f"  💬 评论: ❌ ({e})")
        human_sleep(*DEFAULT_ACTION_DELAY)

    # 无操作时模拟人类浏览停留（modal 弹出就秒关会被 XHS 检测为异常）
    if not acted:
        print(f"  📖 正在浏览…")
        human_sleep(*DEFAULT_BROWSE_DELAY)

    return False  # no risk


# ============ 主逻辑 ============

def wander_search(
    pub: XiaohongshuPublisher,
    keywords: list[str],
    count: int,
    like_prob: float,
    bookmark_prob: float,
    comment_prob: float,
    dry_run: bool,
    stats: dict,
) -> bool:
    """search_result 模式：按关键词搜索后逐条处理。返回是否触发风控。"""
    per_kw    = max(1, count // len(keywords))
    remainder = count - per_kw * len(keywords)

    for ki, kw in enumerate(keywords):
        if stats.get("_risk"):
            break
        quota = per_kw + (1 if ki < remainder else 0)
        print(f"\n🔍 搜索「{kw}」，计划处理 {quota} 条")

        try:
            result = pub.search_feeds(keyword=kw)
            feeds  = result.get("feeds", [])
        except CDPError as e:
            print(f"  ❌ 搜索失败: {e}")
            continue

        if not feeds:
            print("  ⚠️ 未找到结果，跳过")
            continue

        feed_meta: dict[str, dict] = {f["id"]: f for f in feeds if f.get("id")}

        # DOM card info: {feed_id: title} for fallback when API has no displayTitle
        dom_feed_info = get_dom_feed_info(pub)
        dom_ids = list(dom_feed_info.keys())
        eligible = [fid for fid in dom_ids if fid in feed_meta]
        if not eligible:
            print("  ⚠️ DOM 中没有匹配的 card，跳过")
            continue

        random.shuffle(eligible)
        selected_ids = eligible[:quota]
        print(f"  DOM 可见 {len(dom_ids)} 张，匹配 {len(eligible)} 张，处理 {len(selected_ids)} 张")

        for i, feed_id in enumerate(selected_ids, 1):
            if stats.get("_risk"):
                break

            feed      = feed_meta[feed_id]
            note_card = feed.get("noteCard", {})
            title     = (note_card.get("displayTitle") or feed.get("title")
                         or dom_feed_info.get(feed_id) or "（无标题）")
            author_id = (note_card.get("user") or {}).get("userId", "")

            print(f"\n  [{i}/{len(selected_ids)}] {title[:40]}")

            if MY_USER_ID and author_id == MY_USER_ID:
                print("  ⏭  自己的帖子，跳过")
                stats["skip"] += 1
                continue

            human_sleep(*DEFAULT_BROWSE_DELAY, "浏览")
            stats["browse"] += 1

            if dry_run:
                process_note_in_modal(pub, title, like_prob, bookmark_prob, comment_prob, stats, dry_run=True)
                if i < len(selected_ids):
                    human_sleep(*DEFAULT_NOTE_DELAY, "下一条")
                continue

            # 点击 card，失败重试一次
            modal_opened = pub.click_note_card_in_search(feed_id)
            if not modal_opened:
                time.sleep(1.5)
                modal_opened = pub.click_note_card_in_search(feed_id)
            if not modal_opened:
                print("  ⚠️ modal 未打开，跳过")
                stats["skip"] += 1
                continue

            # 等待 modal DOM 完全渲染（图片/视频加载），避免点击被遮挡
            time.sleep(1.5)

            risk = process_note_in_modal(pub, title, like_prob, bookmark_prob, comment_prob, stats, dry_run=False)
            if risk:
                stats["_risk"] = True
                break

            pub.close_note_modal()

            if i < len(selected_ids):
                human_sleep(*DEFAULT_NOTE_DELAY, "下一条")

    return bool(stats.get("_risk"))


def wander_explore(
    pub: XiaohongshuPublisher,
    count: int,
    like_prob: float,
    bookmark_prob: float,
    comment_prob: float,
    dry_run: bool,
    stats: dict,
) -> bool:
    """explore 发现页模式：直接从 /explore 逐条处理。返回是否触发风控。"""
    print(f"\n🌐 进入 explore 发现页，计划处理 {count} 条")
    pub._navigate("https://www.xiaohongshu.com/explore")
    time.sleep(2)

    dom_ids = pub.get_dom_feed_ids_explore()
    if not dom_ids:
        print("  ⚠️ DOM 中未找到 card，跳过")
        return False

    random.shuffle(dom_ids)
    selected_ids = dom_ids[:count]
    print(f"  DOM 可见 {len(dom_ids)} 张，处理 {len(selected_ids)} 张")

    for i, feed_id in enumerate(selected_ids, 1):
        if stats.get("_risk"):
            break

        print(f"\n  [{i}/{len(selected_ids)}] {feed_id}")

        human_sleep(*DEFAULT_BROWSE_DELAY, "浏览")
        stats["browse"] += 1

        if dry_run:
            process_note_in_modal(pub, feed_id, like_prob, bookmark_prob, comment_prob, stats, dry_run=True)
            if i < len(selected_ids):
                human_sleep(*DEFAULT_NOTE_DELAY, "下一条")
            continue

        # 点击 card，失败重试一次
        modal_opened = pub.click_note_card_in_explore(feed_id)
        if not modal_opened:
            time.sleep(1.5)
            modal_opened = pub.click_note_card_in_explore(feed_id)
        if not modal_opened:
            print("  ⚠️ modal 未打开，跳过")
            stats["skip"] += 1
            continue

        # explore 模式没有 API 标题，从 modal 内读取
        title = pub._evaluate(
            "(function(){ var t=document.querySelector('.note-container .title, .note-detail .title, #detail-title'); "
            "return t ? t.innerText.trim() : ''; })()"
        ) or feed_id

        risk = process_note_in_modal(pub, title, like_prob, bookmark_prob, comment_prob, stats, dry_run=False)
        if risk:
            stats["_risk"] = True
            break

        pub.close_note_modal()

        if i < len(selected_ids):
            human_sleep(*DEFAULT_NOTE_DELAY, "下一条")

    return bool(stats.get("_risk"))


def wander(
    page: str,
    keywords: list[str],
    count: int,
    like_prob: float,
    bookmark_prob: float,
    comment_prob: float,
    dry_run: bool,
):
    print("=" * 60)
    print("🚶 小红书自动闲逛")
    if page == "explore":
        print(f"模式: explore 发现页  目标数: {count}")
    else:
        print(f"模式: search  关键词: {', '.join(keywords)}  目标数: {count}")
    print(f"概率 — 点赞:{like_prob:.0%}  收藏:{bookmark_prob:.0%}  评论:{comment_prob:.0%}")
    if dry_run:
        print("⚠️  DRY-RUN 模式，不实际发送操作")
    print("=" * 60)

    stats: dict = {"like": 0, "bookmark": 0, "comment": 0, "browse": 0, "skip": 0}

    pub = XiaohongshuPublisher()
    pub.connect(reuse_existing_tab=True)

    try:
        if page == "explore":
            wander_explore(pub, count, like_prob, bookmark_prob, comment_prob, dry_run, stats)
        else:
            wander_search(pub, keywords, count, like_prob, bookmark_prob, comment_prob, dry_run, stats)
    finally:
        pub.disconnect()

    print("\n" + "=" * 60)
    if stats.get("_risk"):
        print("⚠️  因风控提示提前终止")
    print(
        f"✅ 闲逛完成 | 浏览:{stats['browse']}  点赞:{stats['like']}  "
        f"收藏:{stats['bookmark']}  评论:{stats['comment']}  跳过:{stats['skip']}"
    )
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}")


# ============ CLI ============

def main():
    parser = argparse.ArgumentParser(description="小红书自动闲逛")
    parser.add_argument("--page", choices=["search", "explore"], default="search",
                        help="闲逛模式：search（按关键词搜索）或 explore（发现页）")
    parser.add_argument("--keywords", nargs="+", default=["日语学习"],
                        help="搜索关键词列表（仅 search 模式有效）")
    parser.add_argument("--count",        type=int,   default=10,                  help="总处理帖子数")
    parser.add_argument("--like-prob",    type=float, default=DEFAULT_LIKE_PROB)
    parser.add_argument("--bookmark-prob",type=float, default=DEFAULT_BOOKMARK_PROB)
    parser.add_argument("--comment-prob", type=float, default=DEFAULT_COMMENT_PROB)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    wander(
        page=args.page,
        keywords=args.keywords,
        count=args.count,
        like_prob=args.like_prob,
        bookmark_prob=args.bookmark_prob,
        comment_prob=args.comment_prob,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
