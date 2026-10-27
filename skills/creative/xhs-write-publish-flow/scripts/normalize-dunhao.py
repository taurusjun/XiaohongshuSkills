#!/usr/bin/env python3
"""顿号行归零 —— 行内第 2 个及以后的「、」替换为中文间隔号 U+00B7。

为什么需要
----------
`batch_precheck.py` 把「行内 `、` >= 2」判为 FAIL（SKILL.md 的顿号预检条款），
同一条也触发 `renwei-pre-commit.py` 的「排比三连(顿号分列)」信号。
SKILL.md 规定枚举一律换中文间隔号，不要逐行手改 —— 手改在批量下必漏项。

用法
----
    python3 normalize-dunhao.py <dir> [<dir> ...]      # 就地修改 draft 目录
    python3 normalize-dunhao.py --check <dir> ...      # 只报告，不写
    python3 normalize-dunhao.py --glob '*_draft_*.md' <dir>

默认 glob 覆盖 `xhs_draft_*.md` 与 `gzh_draft_*.md`。
只动含 >= 2 个「、」的行，且只改第 2 个及以后 —— 行内只留一个顿号的行保持原样，
所以人名两连（「A、B」）不会被破坏。

退出码：0 = 全部干净或已修完；1 = --check 模式下仍有命中。
"""
import argparse
import glob
import os
import sys


def fix_line(line: str) -> str:
    if line.count("、") < 2:
        return line
    idx = [i for i, c in enumerate(line) if c == "、"]
    for j in reversed(idx[1:]):          # 从后往前替换，避免位移
        line = line[:j] + "\u00b7" + line[j + 1:]
    return line


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--check", action="store_true", help="只报告，不写回")
    ap.add_argument("--glob", default="*_draft_*.md")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    hits = 0
    for d in args.dirs:
        files = sorted(glob.glob(os.path.join(d, args.glob)))
        if not files and not args.quiet:
            print(f"no draft matched {d}/{args.glob}")
        for path in files:
            lines = open(path, encoding="utf-8").read().split("\n")
            out, changed = [], 0
            for i, line in enumerate(lines, 1):
                new = fix_line(line)
                if new != line:
                    changed += 1
                    hits += 1
                    if not args.quiet:
                        print(f"{os.path.basename(path)} L{i} -> {new[:100]}")
                out.append(new)
            if changed and not args.check:
                open(path, "w", encoding="utf-8").write("\n".join(out))
            if changed and not args.quiet:
                print(f"{os.path.basename(path)}: {changed} 行已{'检出' if args.check else '修正'}")

    print(f"\n{'检出' if args.check else '修正'} {hits} 行")
    return 1 if (args.check and hits) else 0


if __name__ == "__main__":
    sys.exit(main())
