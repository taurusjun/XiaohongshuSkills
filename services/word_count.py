"""小红书字数计算 —— 唯一实现（迁移自 skill/scripts/xhs_word_count.py）。

规则：
  标题长度：CJK 汉字/中日标点/全角符号 ×1，其余（英文/数字/假名/空格）×0.5，向上取整
  正文字数：所有字符 ×1，换行符不计
"""

import math

__all__ = ["content_len", "title_len", "main"]

_DOC = """小红书字数计算。
  xhs_word_count.py <文本>                      # 正文字数
  xhs_word_count.py --title <文本>               # 标题长度
  xhs_word_count.py --check <文本> <上限>        # 正文字数检查
  xhs_word_count.py --check-title <文本> <上限>  # 标题长度检查
"""


def content_len(text: str) -> int:
    """正文字数：所有字符×1，换行符不计。"""
    return len(text) - text.count("\n")


def title_len(text: str) -> int:
    """标题长度：CJK/全角 ×1，其余 ×0.5，向上取整。"""
    cjk = sum(
        1 for c in text
        if "\u4e00" <= c <= "\u9fff"
        or "\u3000" <= c <= "\u303f"
        or "\uff00" <= c <= "\uffef"
    )
    return math.ceil(cjk + (len(text) - cjk) * 0.5)


def main(argv=None) -> int:
    """CLI 入口（行为与迁移前脚本一致）。返回退出码。"""
    import sys
    argv = sys.argv if argv is None else ["xhs_word_count", *argv]
    if len(argv) < 2:
        print(_DOC)
        return 1
    if argv[1] == "--title":
        print(f"{title_len(' '.join(argv[2:]))}")
    elif argv[1] == "--check":
        if len(argv) < 4:
            print("用法: python3 xhs_word_count.py --check <文本> <上限>")
            return 1
        count, limit = content_len(argv[2]), int(argv[3])
        print(f"{'✅' if count <= limit else '❌ 超了!'}  {count}字/{limit}上限")
    elif argv[1] == "--check-title":
        if len(argv) < 4:
            print("用法: python3 xhs_word_count.py --check-title <文本> <上限>")
            return 1
        count, limit = title_len(argv[2]), int(argv[3])
        print(f"{'✅' if count <= limit else '❌ 超了!'}  标题{count}字/{limit}上限")
    else:
        print(f"{content_len(' '.join(argv[1:]))}字")
    return 0
