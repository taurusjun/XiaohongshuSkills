#!/usr/bin/env python3
"""scoring.py — 低分诊断与行动决策，纯函数无副作用"""

from enum import Enum


class Action(str, Enum):
    DISCARD = "DISCARD"
    REGENERATE = "REGENERATE"
    WAIT_GALLERY = "WAIT_GALLERY"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    PUBLISH = "PUBLISH"


def diagnose_low_score(article: dict, scores: dict,
                       publish_threshold: float = 3.0,
                       retry_threshold: float = 2.0) -> Action:
    """纯函数，确定文章应采取的行动。优先级：DISCARD > WAIT_GALLERY > REGENERATE > HUMAN_REVIEW

    scores: {dimension: {"value": float, "reason": str}}
    """
    def _val(dim: str) -> float:
        return float(scores.get(dim, {}).get("value", 0))

    content_score = article.get("content_score", 0)
    title_score = article.get("title_score", 0)
    combined = title_score + content_score

    # 已达发布线
    if combined >= publish_threshold * 2:
        return Action.PUBLISH

    # 优先级 1: DISCARD — 话题本身无料
    # topic_potential = 名人+热点+冲突感+猎奇感+用户共鸣 之和
    topic_potential = (_val("名人") + _val("热点") + _val("冲突感") +
                       _val("猎奇感") + _val("用户共鸣"))
    if topic_potential <= 1 and title_score < publish_threshold:
        return Action.DISCARD

    # 优先级 2: WAIT_GALLERY — 有图片缺口（无论生成质量如何，重生成解决不了图片问题）
    if not article.get("gallery_images") and not article.get("image_url"):
        return Action.WAIT_GALLERY

    # 优先级 3: REGENERATE — 生成质量差（话题有潜力）
    quality_issues = _val("啰嗦重复") + _val("离题")
    if quality_issues >= 1 and combined >= retry_threshold * 2:
        return Action.REGENERATE

    # 优先级 4: HUMAN_REVIEW — 原因不明确
    if combined >= retry_threshold:
        return Action.HUMAN_REVIEW

    return Action.DISCARD


import logging as _logging
_dedup_logger = _logging.getLogger("dedup")


def dedup_today_candidates(keyword: str = "") -> None:
    """对今日入库的候选文章按 keyword 分组去重，重复的标记 discarded。

    算法：标题字符集重叠 > 50% 视为同一事件，保留 title_score 最高的一篇。
    打印详细日志供人工审查。
    """
    import sqlite3, os
    from datetime import datetime

    from scripts.sqlite_db import DB_PATH
    db_path = DB_PATH
    today = datetime.now().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    where = "DATE(created_at)=? AND status='active'"
    params = [today]
    if keyword:
        where += " AND fetch_by=?"
        params.append(keyword)
    rows = conn.execute(
        f"SELECT key, title, title_score, fetch_by FROM news WHERE {where} ORDER BY title_score DESC",
        params
    ).fetchall()
    conn.close()

    if not rows:
        return

    # 按 fetch_by 分组
    from collections import defaultdict
    groups: dict[str, list] = defaultdict(list)
    for r in rows:
        groups[r["fetch_by"] or ""].append(dict(r))

    total_discarded = 0
    for kw, articles in groups.items():
        _dedup_logger.info(f"[dedup] keyword='{kw}' 候选 {len(articles)} 篇（已按 title_score 降序）")
        kept, discarded = [], []
        for art in articles:  # 已按 title_score 降序
            title = art["title"] or ""
            sa = set(title)
            duplicate_of = None
            for k in kept:
                sb = set(k["title"] or "")
                overlap = len(sa & sb) / max(len(sa | sb), 1)
                if overlap > 0.5:
                    duplicate_of = k
                    break
            if duplicate_of:
                discarded.append((art, duplicate_of, overlap))
            else:
                kept.append(art)

        _dedup_logger.info(f"[dedup]   保留 {len(kept)} 篇，丢弃 {len(discarded)} 篇重复")
        for art, dup_of, overlap in discarded:
            _dedup_logger.info(
                f"[dedup]   ✂️  丢弃: 「{art['title'][:30]}」(score={art['title_score']:.2f})"
                f" ← 重复于 「{dup_of['title'][:30]}」(score={dup_of['title_score']:.2f})"
                f" overlap={overlap:.0%}"
            )
            conn = sqlite3.connect(db_path)
            conn.execute(
                "UPDATE news SET status='discarded', updated_at=datetime('now') WHERE key=?",
                (art["key"],)
            )
            conn.commit()
            conn.close()
            total_discarded += 1

        for art in kept:
            _dedup_logger.info(f"[dedup]   ✅ 保留: 「{art['title'][:30]}」(score={art['title_score']:.2f})")

    _dedup_logger.info(f"[dedup] 完成，共丢弃 {total_discarded} 篇重复候选")


HINT_MAP = {
    "啰嗦重复": "请精简正文，删除重复表述，每句话只出现一次核心信息。",
    "离题": "请确保正文紧密围绕标题主题，不要偏离到无关内容。",
    "震惊体": "请用平实、客观的语言重写标题，避免夸张和过度渲染。",
    "简单通知": "请在标题中加入悬念、数字或疑问句等吸引点击的元素。",
    "概括全部": "请在标题中保留悬念，不要让读者看完标题就知道全部信息。",
    "主动讨赏": "请移除正文中的索要点赞/收藏/关注话术，用自然的评论引导替代。",
    "负面情绪": "请减少负面情绪渲染，以客观中立的视角重写。",
}


def get_failed_dims(scores: dict) -> list[str]:
    """返回 direction=minus 且 value >= 1 的维度列表"""
    try:
        from scripts.sqlite_db import load_active_dimensions
        dims = load_active_dimensions()
    except Exception:
        dims = []
    minus_dims = {d["name"] for d in dims if d.get("direction") == "minus"}
    if not minus_dims:
        minus_dims = {"离题", "啰嗦重复", "主动讨赏", "负面情绪", "简单通知", "震惊体", "概括全部"}
    return [d for d in minus_dims if scores.get(d, {}).get("value", 0) >= 1]
