#!/usr/bin/env python3
"""顿号行机械修复 —— 行内第 2 个及以后的「、」→ 中文间隔号 U+00B7

为什么需要它
------------
`batch_precheck.py` 的门禁是「行内 `、` >= 2 即 FAIL」，因为这是 renwei「排比三连」
的真实前置条件。写稿时人名枚举、曲名枚举、平台枚举极易触发，而这是纯机械问题，
不该占用改稿轮次。SKILL.md 的规定做法就是「人名/产品/平台枚举一律换中文间隔号」。

用法
----
    python3 fix_dunhao_lines.py /tmp/drafts1001                 # 原地修目录下所有 draft
    python3 fix_dunhao_lines.py /tmp/drafts1001 --glob 'xhs_draft_*'
    python3 fix_dunhao_lines.py /tmp/drafts1001 --dry-run       # 只看会改哪些行

只改命中行，不动其他内容；改完打印每篇文件的命中行数与修复前的行内容，便于复查
是否误伤（例如某个「、」本来就是引语里的原话，需要人工判断）。

退出码: 0 = 无残留顿号行；1 = 仍有残留（理论上不会，除非行内出现全角/半角混写）
"""
import argparse
import glob
import os
import sys


def fix_line(line: str) -> str:
    """行内第 2 个及以后的「、」换成 U+00B7。保留第 1 个（单顿号是正常中文用法）。"""
    if line.count("、") < 2:
        return line
    idx = [i for i, c in enumerate(line) if c == "、"]
    for j in idx[1:]:
        line = line[:j] + "\u00b7" + line[j + 1:]
    return line


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("dir", help="draft 所在目录")
    ap.add_argument("--glob", default="*_draft_*.md", help="文件匹配（默认所有 draft）")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.dir, args.glob)))
    if not files:
        print(f"no draft matched {args.dir}/{args.glob}")
        return 1

    total_hits = 0
    remaining = 0
    for path in files:
        src = open(path, encoding="utf-8").read()
        out_lines = []
        hits = []
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
        if args.dry_run:
            remaining += len([l for l in out_lines if l.count("、") >= 2])
        else:
            remaining += len(
                [l for l in open(path, encoding="utf-8").read().split("\n") if l.count("、") >= 2]
            )

    print(f"\n{len(files)} 篇扫描完毕，命中 {total_hits} 行"
          f"{'（dry-run，未写入）' if args.dry_run else '，已修复'}；残留 {remaining} 行")
    return 1 if remaining else 0


if __name__ == "__main__":
    sys.exit(main())
