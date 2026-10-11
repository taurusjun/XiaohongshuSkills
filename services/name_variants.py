"""形近字/异体字自动替换 + 主角名核对（数据来自 lexicon，type=variant）。以 content_ja 为字形事实源。"""
import re

from services import lexicon as _lx

__all__ = ["replace", "suspect_names", "check"]


def _maps():
    vars_, keep = {}, set()
    try:
        for r in _lx.all(type="variant"):
            if r.get("status") == "rejected":
                continue
            if r.get("action") == "keep":
                keep.add(r["term"])
            elif r.get("zh"):
                vars_[r["term"]] = r["zh"]
    except Exception:  # noqa: BLE001
        pass
    return vars_, keep


def replace(text):
    """把形近字/异体字替换为正字；保留字（人名约定）不动。返回 (new_text, changes)。"""
    out, changes = text or "", []
    vars_, keep = _maps()
    for wrong, right in vars_.items():
        if not wrong or wrong == right or wrong in keep:
            continue
        if wrong in out:
            out = out.replace(wrong, right)
            changes.append(f"{wrong}->{right}")
    return out, changes


def suspect_names(text):
    """返回含『形近变体字』的疑似人名 token（供 content_ja 核对）。"""
    vars_, keep = _maps()
    bad = {k for k, v in vars_.items() if k not in keep and k != v}
    return [tok for tok in re.findall(r"[\u4e00-\u9fff]{2,6}", text or "")
            if any(c in bad for c in tok)]


def check(title, body, content_ja):
    """主角名核对：疑似变体名（替换后）是否能在 content_ja 找到；找不到 → 告警列表。"""
    src = content_ja or ""
    warns = []
    for tok in suspect_names(f"{title} {body}"):
        fixed, _ = replace(tok)
        if tok in src or fixed in src:
            continue
        warns.append(f"疑似形近字名「{tok}」（content_ja 未见；已按表替换为「{fixed}」，请确认）")
    return warns
