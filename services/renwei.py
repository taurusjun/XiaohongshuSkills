"""Renwei 审读机械规则（对齐 renwei-pre-commit.py 的真实条件）。
- 排比三连(顿号分列)：renwei 真条件 = 正则 AND 行含「、」AND 顿号数>=2
- 加粗滥用：用 **加粗** 当小标题（长文小标题必须 ##）
"""
import re
__all__ = ["check"]
_PARALLEL = re.compile(r"([^，。！？]{2,8}[，、]){2,}[^，。！？]{2,8}(的|是|和)")

def check(text: str):
    probs = []
    for i, l in enumerate(text.split("\n"), 1):
        s = l.strip()
        if s.startswith("**") and s.endswith("**") and 4 <= len(s) <= 40 and s.count("**") == 2:
            probs.append(f"L{i}: 加粗滥用（小标题应用 ## 而非 **）")
        if l.count("、") >= 2 and _PARALLEL.search(l):
            probs.append(f"L{i}: 排比三连(顿号分列)")
    return probs
