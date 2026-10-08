"""顿号行归零 —— 唯一实现（合并 normalize-dunhao.py 与 fix_dunhao_lines.py 的重复逻辑）。

行内第 2 个及以后的「、」替换为中文间隔号 U+00B7；只留第 1 个（单顿号是正常用法，
人名两连「A、B」不被破坏）。
"""
import argparse
import glob
import os
import sys

__all__ = ["fix_line", "fix_text", "main_fix", "main_normalize"]

MIDDOT = "\u00b7"


def fix_line(line: str) -> str:
    if line.count("、") < 2:
        return line
    idx = [i for i, c in enumerate(line) if c == "、"]
    for j in reversed(idx[1:]):          # 从后往前替换，避免位移
        line = line[:j] + MIDDOT + line[j + 1:]
    return line


def fix_text(text: str):
    """返回 (新文本, 命中行数)。"""
    out, hits = [], 0
    for line in text.split("\n"):
        new = fix_line(line)
        if new != line:
            hits += 1
        out.append(new)
    return "\n".join(out), hits


def main_fix(argv=None) -> int:
    """fix_dunhao_lines.py 的行为（单目录，--dry-run）。"""
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="draft 所在目录")
    ap.add_argument("--glob", default="*_draft_*.md")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    files = sorted(glob.glob(os.path.join(args.dir, args.glob)))
    if not files:
        print(f"no draft matched {args.dir}/{args.glob}")
        return 1

    total_hits = 0
    remaining = 0
    for path in files:
        src = open(path, encoding="utf-8").read()
        out_lines, hits = [], []
        for i, line in enumerate(src.split("\n"), 1):
            new = fix_line(line)
            if new != line:
                hits.append((i, line))
            out_lines.append(new)
        if hits:
            total_hits += len(hits)
            name = os.path.basename(path)
            print(f"{name}  顿号行 ×{len(hits)}")
            for i, before in hits:
                after = fix_line(before)
                print(f"   L{i} before: {before[:100]}")
                if not args.dry_run:
                    print(f"   L{i} after : {after[:100]}")
            if not args.dry_run:
                open(path, "w", encoding="utf-8").write("\n".join(out_lines))
        text = "\n".join(out_lines) if args.dry_run else open(path, encoding="utf-8").read()
        remaining += len([l for l in text.split("\n") if l.count("、") >= 2])

    print(f"\n{len(files)} 篇扫描完毕，命中 {total_hits} 行"
          f"{'（dry-run，未写入）' if args.dry_run else '，已修复'}；残留 {remaining} 行")
    return 1 if remaining else 0


def main_normalize(argv=None) -> int:
    """normalize-dunhao.py 的行为（多目录，--check/--quiet）。"""
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("dirs", nargs="+")
    ap.add_argument("--check", action="store_true", help="只报告，不写回")
    ap.add_argument("--glob", default="*_draft_*.md")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    hits = 0
    for d in args.dirs:
        files = sorted(glob.glob(os.path.join(d, args.glob)))
        if not files and not args.quiet:
            print(f"no draft matched {d}/{args.glob}")
        for path in files:
            src = open(path, encoding="utf-8").read()
            new_text, changed = fix_text(src)
            if changed:
                hits += changed
                if not args.quiet:
                    for i, line in enumerate(src.split("\n"), 1):
                        fixed = fix_line(line)
                        if fixed != line:
                            print(f"{os.path.basename(path)} L{i} -> {fixed[:100]}")
                    print(f"{os.path.basename(path)}: {changed} 行已{'检出' if args.check else '修正'}")
                if not args.check:
                    open(path, "w", encoding="utf-8").write(new_text)

    print(f"\n{'检出' if args.check else '修正'} {hits} 行")
    return 1 if (args.check and hits) else 0
