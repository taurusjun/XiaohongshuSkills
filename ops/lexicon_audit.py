#!/usr/bin/env python3
"""术语表体检：状态/类型分布、无中译、alias 冲突。"""
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from services import lexicon as LX  # noqa: E402


def main():
    rows = LX.all()
    print("总数", len(rows))
    print("status", dict(Counter(r["status"] for r in rows)))
    print("type", dict(Counter(r["type"] for r in rows)))
    empty = [r["term"] for r in rows
             if r["action"] == "translate" and not r["zh"] and r["type"] != "font"]
    print("translate 无 zh:", len(empty), empty[:12])
    alias = {}
    for r in rows:
        for a in (r.get("aliases") or "").split(","):
            a = a.strip()
            if a:
                alias.setdefault(a, []).append(r["term"])
    print("alias 冲突:", {a: v for a, v in alias.items() if len(v) > 1})
    return 0


if __name__ == "__main__":
    sys.exit(main())
