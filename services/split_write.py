"""关联素材体量过大 → 拆多篇写（密度极限 fallback）。

旧 skill 明确：默认**不拆**（用户要「一篇」就不拆）；仅当用户允许或体量确实过大时走。
本模块提供判定与分组；`cli write-full --split-large` 显式开启。
"""
from services.news import get_by_key
from services.word_count import content_len

DEFAULT_THRESHOLD = 3000
__all__ = ["cluster_text_len", "should_split", "split_groups"]


def _keys(cand):
    return list(dict.fromkeys([cand["key"]] + [k.strip() for k in (cand.get("cluster_keys") or "").split(",") if k.strip()]))


def cluster_text_len(cand):
    """main + cluster 全部 content_ja 的 xhs 字数合计。"""
    n = 0
    for k in _keys(cand):
        r = get_by_key(k) or {}
        n += content_len(r.get("content_ja") or "")
    return n


def should_split(cand, threshold=DEFAULT_THRESHOLD):
    return cluster_text_len(cand) > threshold


def split_groups(cand, threshold=DEFAULT_THRESHOLD):
    """把 cluster 按体量粗分为若干 ≤threshold 的组，每组用组内主 key（第一项）。"""
    groups, cur, cur_len = [], [], 0
    for k in _keys(cand):
        r = get_by_key(k) or {}
        L = content_len(r.get("content_ja") or "")
        if cur and cur_len + L > threshold:
            groups.append(cur)
            cur, cur_len = [], 0
        cur.append(k)
        cur_len += L
    if cur:
        groups.append(cur)
    return groups
