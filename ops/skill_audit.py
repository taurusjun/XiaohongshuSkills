#!/usr/bin/env python3
"""skill 一致性审计 CLI。用法：
  python ops/skill_audit.py extract --skill <path> [--size 160]
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services import skill_audit as SA  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("extract")
    e.add_argument("--skill", required=True)
    e.add_argument("--out", default="config/skill_audit/items.json")
    e.add_argument("--size", type=int, default=160)
    j = sub.add_parser("judge")
    j.add_argument("--items", default="config/skill_audit/items.json")
    j.add_argument("--out", default=None)
    j.add_argument("--skill", default="xhs-write-publish-flow/SKILL.md")
    a = ap.parse_args(argv)
    root = Path(__file__).resolve().parent.parent
    if a.cmd == "extract":
        SA.run(a.skill, root, root / a.out, size=a.size)
    elif a.cmd == "judge":
        import datetime as _dt, json as _json
        items = _json.loads((root / a.items).read_text(encoding="utf-8"))
        SA.judge_all(items, root)
        (root / a.items).write_text(_json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
        md = SA.render(items, a.skill, _dt.date.today().isoformat())
        out = root / (a.out or f"data/audits/{_dt.date.today().isoformat()}-skill-conformance.md")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")
        print(f"[skill_audit] 报告 → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
