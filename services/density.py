"""批量密度检查 —— 唯一实现（迁移自 batch_density_check.py；不再 shell out word_count）。"""
import argparse
import json
import os
import sys

from services.draft_io import get_news
from services.word_count import content_len

__all__ = ["check_draft", "main"]


def check_draft(body_only: str, content_ja: str, fmt: str, lf):
    b = content_len(body_only)
    j = content_len(content_ja or "")
    density = (b / j * 100) if j else 0
    need800 = (fmt == "story" and lf == 1)
    ok_len = (not need800) or (b >= 800)
    ok_den = density >= 30.0
    verdict = "OK" if (ok_len and ok_den) else ("LEN!" if not ok_len else "DEN!")
    return dict(b_len=b, j_len=j, density=density, verdict=verdict)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 1:
        print("用法: batch_density_check.py <plan.json> [draft_dir]")
        return 1
    plan_path = argv[0]
    draft_dir = argv[1] if len(argv) > 1 else "/tmp/xhs_0831"
    with open(plan_path, encoding="utf-8") as f:
        plan = json.load(f)

    print(f"{'draft':<16} {'fmt/lf':<8} {'body':<6} {'ja':<6} {'density':<8} verdict")
    results = {}
    for df, info in plan.items():
        d = get_news(info["key"])
        with open(os.path.join(draft_dir, df), encoding="utf-8") as f:
            body_only = "\n".join(f.read().split("\n")[1:])
        r = check_draft(body_only, d.get("content_ja"), d.get("format"), d.get("is_long_form"))
        print(f"{df:<16} {d.get('format')}/{d.get('is_long_form'):<7} {r['b_len']:<6} "
              f"{r['j_len']:<6} {r['density']:<7.1f}% {r['verdict']}")
        results[df] = dict(key=info["key"], fmt=d.get("format"), lf=d.get("is_long_form"), **r)

    fail = [k for k, v in results.items() if v["verdict"] != "OK"]
    if fail:
        print(f"\nFAILED ({len(fail)}): {fail}")
        return 1
    print(f"\nALL OK ({len(results)} drafts)")
    return 0
