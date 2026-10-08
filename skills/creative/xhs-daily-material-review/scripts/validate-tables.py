#!/usr/bin/env python3
"""Markdown 表格格式一次性校验器 — 对应 xhs-daily-material-review SKILL.md「表格格式铁律」。

用途：写完每日 review 存档后跑一次，确认所有表格能在 Telegram 渲染为可视表格
（而不是退化成纯文本）。首版存档常见 2 类违规都是它抓出来的。

用法：
    python3 validate-tables.py ~/.hermes/daily-reviews/$(TZ=Asia/Tokyo date '+%Y-%m-%d').md

退出码：0 = 全部通过（problems: 0）；1 = 有问题。
在 cron 模式下这是纯 shell 可跑的（不依赖 execute_code / hermes_tools）。

校验项（逐条对应 SKILL.md 铁律）：
  1. 分隔行上方必须是表格行（表头），不能直接接正文
  2. 表头 / 分隔行 / 所有数据行的 `|` 列数必须一致
     —— 单元格内出现 `|` 或 `\\|` 会在这里暴露（列数错位）
  3. 表格前必须有一个空行
  4. 表格前一行必须是 `#` 标题（**粗体行不行**，会退化为纯文本）
  5. 表格后必须有一个空行
  6. 单表 >25 行给出警告（分段时可能跨段）
"""
import re
import sys

SEP = re.compile(r'^\|[\s\-:|]+\|$')


def cols(row: str) -> int:
    """表格行的列数（按未转义的 | 计数）。"""
    return len(row.strip().strip('|').split('|'))


def main(path: str) -> int:
    with open(path, encoding='utf-8') as fh:
        lines = fh.read().split('\n')

    problems, tables = [], 0
    for i, line in enumerate(lines):
        if not (SEP.match(line.strip()) and '-' in line):
            continue
        tables += 1
        tag = f'L{i + 1}'

        # 1/2: 表头与列数
        header = lines[i - 1] if i >= 1 else ''
        if not header.strip().startswith('|'):
            problems.append(f'{tag}: 分隔行上方不是表头行（表格缺表头）')
        elif cols(header) != cols(line):
            problems.append(
                f'{tag}: 表头/分隔行列数不一致（{cols(header)} vs {cols(line)}）'
                f' —— 检查单元格内是否有 | 或 \\|'
            )

        # 数据行列数
        j = i + 1
        while j < len(lines) and lines[j].strip().startswith('|'):
            if cols(lines[j]) != cols(line):
                problems.append(
                    f'L{j + 1}: 数据行列数不一致（{cols(lines[j])} vs {cols(line)}）'
                    f' -> {lines[j][:60]}'
                )
            j += 1

        # 3: 前空行
        if i - 2 >= 0 and lines[i - 2].strip() != '':
            problems.append(f'{tag}: 表格前缺空行')

        # 4: 前一行必须是标题（不是粗体行／正文）
        m = i - 2
        while m >= 0 and lines[m].strip() == '':
            m -= 1
        prev = lines[m].strip() if m >= 0 else ''
        if not (prev.startswith('|') or prev.startswith('#')):
            problems.append(
                f'{tag}: 表格前不是 # 标题 -> "{prev[:50]}"'
                f'（用 **粗体行** 引导会让表格退化为纯文本）'
            )

        # 5: 后空行
        if j < len(lines) and lines[j].strip() != '':
            problems.append(f'L{j + 1}: 表格后缺空行')

        # 6: >25 行警告
        n_rows = j - i
        if n_rows > 25:
            problems.append(f'{tag}: 单表 {n_rows} 行（>25，分段时可能跨段）')

    print(f'tables found: {tables}')
    print(f'problems: {len(problems)}')
    for p in problems:
        print('  -', p)
    return 1 if problems else 0


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(sys.argv[1]))
