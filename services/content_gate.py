"""写前内容门禁（硬规则=config+服务）：成人产业/非赛道命中 → 标待人工（不阻断写稿）。"""
from services import rules

__all__ = ["check"]

_AD = rules.load("adult_industry.json")
_OT = rules.load("off_topic.json")


def check(title="", body=""):
    """命中成人产业/非赛道 → 返回 {manual_review:1, manual_reason:...}；否则 None（不阻断）。"""
    text = f"{title} {body}"
    ad = [k for k in _AD.get("keywords", []) if k and k in text]
    ot = [k for k in _OT.get("keywords", []) if k and k in text]
    reasons = []
    if ad:
        reasons.append("成人产业/风俗命中: " + "、".join(ad[:5]) +
                       "（核心若为人物弧光/公共议题可考虑改道 gzh）")
    if ot:
        reasons.append("疑似非赛道: " + "、".join(ot[:5]))
    if not reasons:
        return None
    return {"manual_review": 1, "manual_reason": "；".join(reasons)}
