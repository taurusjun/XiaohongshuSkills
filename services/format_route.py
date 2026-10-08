"""体裁/字数路由（机械规则，来自 SKILL longform-vs-900-routing / 6-29-db-format-routing）。"""
__all__ = ["route"]

EXPORT_THRESHOLD = 3000

def route(content_ja_len: int, fmt: str = "news", lf: int = 0) -> dict:
    if (content_ja_len or 0) > EXPORT_THRESHOLD:
        return {"publish_method": "export", "target": "longform",
                "ratio": "0.31-0.33", "note": "按原文×0.31~0.33 写足，不压字数"}
    if fmt == "story" and lf == 1:
        return {"publish_method": "post", "target": "story900",
                "note": "story lf=1 ≥800字且 ≥2 个 ##（900±50）"}
    return {"publish_method": "post", "target": "news900", "note": "news ≤900，无 ##"}
