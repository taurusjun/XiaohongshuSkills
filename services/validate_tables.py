"""Markdown 表格格式校验 —— 唯一实现（迁移自 validate-tables.py）。"""
import re
import sys

SEP = re.compile(r"^\|[\s\-:|]+\|$")

__all__ = ["cols", "validate", "main"]


def cols(row: str) -> int:
    return len(row.strip().strip("|").split("|"))


def validate_text(text: str):
    """返回 (tables, problems)。"""
    lines = text.split("\n")
    problems, tables = [], 0
    for i, line in enumerate(lines):
        if not (SEP.match(line.strip()) and "-" in line):
            continue
        tables += 1
        tag = f"L{i + 1}"
        header = lines[i - 1] if i >= 1 else ""
        if not header.strip().startswith("|"):
            problems.append(f"{tag}: 分隔行上方不是表头行（表格缺表头）")
        elif cols(header) != cols(line):
            problems.append(f"{tag}: 表头/分隔行列数不一致（{cols(header)} vs {cols(line)}）"
                            f" —— 检查单元格内是否有 | 或 \\|")
        j = i + 1
        while j < len(lines) and lines[j].strip().startswith("|"):
            if cols(lines[j]) != cols(line):
                problems.append(f"L{j + 1}: 数据行列数不一致（{cols(lines[j])} vs {cols(line)}）"
                                f" -> {lines[j][:60]}")
            j += 1
        if i - 2 >= 0 and lines[i - 2].strip() != "":
            problems.append(f"{tag}: 表格前缺空行")
        m = i - 2
        while m >= 0 and lines[m].strip() == "":
            m -= 1
        prev = lines[m].strip() if m >= 0 else ""
        if not (prev.startswith("|") or prev.startswith("#")):
            problems.append(f"{tag}: 表格前不是 # 标题 -> \"{prev[:50]}\""
                            f"（用 **粗体行** 引导会让表格退化为纯文本）")
        if j < len(lines) and lines[j].strip() != "":
            problems.append(f"L{j + 1}: 表格后缺空行")
        n_rows = j - i
        if n_rows > 25:
            problems.append(f"{tag}: 单表 {n_rows} 行（>25，分段时可能跨段）")
    return tables, problems


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print("用法: validate-tables.py <markdown文件>")
        return 2
    with open(argv[0], encoding="utf-8") as fh:
        tables, problems = validate_text(fh.read())
    print(f"tables found: {tables}")
    print(f"problems: {len(problems)}")
    for p in problems:
        print("  -", p)
    return 1 if problems else 0
