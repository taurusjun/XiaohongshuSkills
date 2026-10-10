"""渠道规则（机械先判 xhs/gzh；边界由 LLM 兜底）。

判据关键词来自 config/routing.json（硬规则=config+服务）。
皆中/皆不中 → 低置信度 ambiguous，交给 LLM 复判。
"""
import re

from services import rules

__all__ = ["route", "route_detail"]

_R = rules.load("routing.json")
_GZH = re.compile("|".join(map(re.escape, _R.get("gzh", []))) or r"$^", re.I)
_XHS_HINT = re.compile("|".join(map(re.escape, _R.get("xhs_hint", []))) or r"$^", re.I)


def route_detail(title: str = "", body: str = ""):
    """返回 (hint, confidence)。confidence: high|mid|low。"""
    text = f"{title} {body[:500]}"
    g, x = bool(_GZH.search(text)), bool(_XHS_HINT.search(text))
    if g and x:
        return "ambiguous", "low"
    if g:
        return "gzh", "mid"
    if x:
        return "xhs", "mid"
    return "xhs", "low"


def route(title: str = "", body: str = "") -> str:
    h, _ = route_detail(title, body)
    return "xhs" if h == "ambiguous" else h
