"""标题适配（硬规则=config+服务）：边界优先截断；无边界→回退 LLM 重写（限 1 次）。"""
from services import rules
from services.word_count import title_len

__all__ = ["fit", "limit_for"]

_SEPS = "、，｜（(・· 　"
_TH = rules.thresholds()


def limit_for(channel="xhs"):
    if channel == "gzh":
        return _TH.get("gzh", {}).get("title_max", 30)
    return _TH.get("xhs", {}).get("title_max", 20)


def fit(title, limit=20):
    """优先在分隔符处截断到 <=limit；无法则机械砍尾并置 need_llm=True。返回 (title, need_llm)。"""
    if not title or title_len(title) <= limit:
        return title, False
    best = None
    for i in range(len(title) - 1, 0, -1):
        if title[i] in _SEPS and title_len(title[:i]) <= limit:
            best = title[:i].rstrip(_SEPS)
            if best:
                break
    if best:
        return best, False
    t = title
    while t and title_len(t) > limit:
        t = t[:-1].rstrip()
    return t, True
