"""公众号稿机械检查（对齐 xhs-content-review gzh-review-criteria 的可机械化部分）。
去魅测试/3维评分属判断，交由 LLM；此处做：标题≤30、第一段专名密度、引语存在。
"""
import json
import re
from services import paths
__all__ = ["check"]

_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]{2,}")

def _names():
    try:
        from services import lexicon as _lx
        return [r["term"] for r in _lx.all()
                if r.get("type") in ("person", "group") and r.get("status") != "rejected"]
    except Exception:  # noqa: BLE001
        return []

def check(title: str, body: str):
    from services.word_count import title_len
    probs = []
    if title and title_len(title) > 30:
        probs.append(f"标题 {title_len(title)} 字 > 30（公众号上限）")
    first = (body or "").split("\n\n")[0]
    cnt = sum(1 for n in _names() if n and n in first) + len(_KANA.findall(first))
    if cnt >= 3:
        probs.append(f"第一段专名≈{cnt} 个（≥3，需在首个团名/人名后加括号注释）")
    if body and "「" not in body and "“" not in body:
        probs.append("缺具体引语（建议加「」引述）")
    return probs
