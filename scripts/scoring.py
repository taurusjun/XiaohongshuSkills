#!/usr/bin/env python3
"""scoring.py — 低分诊断与行动决策，纯函数无副作用"""

from enum import Enum

from scripts.sqlite_db import load_active_dimensions


class Action(str, Enum):
    DISCARD = "DISCARD"
    REGENERATE = "REGENERATE"
    WAIT_GALLERY = "WAIT_GALLERY"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    PUBLISH = "PUBLISH"


def diagnose_low_score(article: dict, scores: dict,
                       publish_threshold: float = 3.0,
                       retry_threshold: float = 2.0) -> Action:
    """纯函数，确定文章应采取的行动。

    scores: {dimension: {"value": float, "reason": str}}
    """
    content_score = article.get("content_score", 0)
    title_score = article.get("title_score", 0)

    # 图片不足且评分边界 → 等待图集
    if not article.get("gallery_images") and content_score < publish_threshold:
        return Action.WAIT_GALLERY

    # 明确垃圾信号 → 直接丢弃
    discard_dims = ["离题", "负面情绪", "主动讨赏"]
    for d in discard_dims:
        if scores.get(d, {}).get("value", 0) >= 1:
            return Action.DISCARD

    combined = title_score + content_score
    if combined >= publish_threshold * 2:
        return Action.PUBLISH
    if combined >= retry_threshold * 2:
        return Action.REGENERATE
    if combined < retry_threshold * 2:
        return Action.DISCARD
    return Action.HUMAN_REVIEW


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
        dims = load_active_dimensions()
    except Exception:
        dims = []
    minus_dims = {d["name"] for d in dims if d.get("direction") == "minus"}
    if not minus_dims:
        minus_dims = {"离题", "啰嗦重复", "主动讨赏", "负面情绪", "简单通知", "震惊体", "概括全部"}
    return [d for d in minus_dims if scores.get(d, {}).get("value", 0) >= 1]
