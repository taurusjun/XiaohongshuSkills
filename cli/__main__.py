"""统一 CLI：python -m cli <cmd> [args...]  → 分发到 services。"""
import sys

from services import (word_count, precheck, schedule, dunhao, density,
                      validate_tables, import_drafts, review_archive)
from agent import review as _review_full
from agent import write as _write_full

SUBCOMMANDS = {
    "word-count": word_count.main,
    "precheck": precheck.main,
    "schedule": schedule.main,
    "dunhao-fix": dunhao.main_fix,
    "dunhao-normalize": dunhao.main_normalize,
    "density": density.main,
    "validate-tables": validate_tables.main,
    "import-drafts": import_drafts.main,
    "review-archive": review_archive.main,
    "review-full": _review_full.main,
    "write-full": _write_full.main,
}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: python -m cli <cmd> [args...]")
        print("cmds: " + ", ".join(SUBCOMMANDS))
        return 0 if argv else 2
    cmd, rest = argv[0], argv[1:]
    fn = SUBCOMMANDS.get(cmd)
    if not fn:
        print(f"unknown cmd: {cmd}", file=sys.stderr)
        return 2
    return fn(rest) or 0


if __name__ == "__main__":
    sys.exit(main())
