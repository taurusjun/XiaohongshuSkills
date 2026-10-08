#!/usr/bin/env python3
"""小红书字数计算工具。

两种计算规则：
  标题长度：CJK字符×1 + 其余字符×0.5，向上取整（平台对标题的显示截断规则）
  正文字数：所有字符×1，换行符不计（平台实际字数统计）

用法:
  python3 xhs_word_count.py <文本>                      # 正文字数
  python3 xhs_word_count.py --title <文本>               # 标题长度
  python3 xhs_word_count.py --check <文本> <上限>        # 正文字数检查
  python3 xhs_word_count.py --check-title <文本> <上限>  # 标题长度检查
"""

import math
import sys


def xhs_content_len(text: str) -> int:
    """正文字数：所有字符×1，换行符不计。"""
    return len(text) - text.count('\n')


def xhs_title_len(text: str) -> int:
    """标题长度：CJK汉字/标点/全角符号×1，其余字符（英文/数字/假名/空格）×0.5，向上取整。"""
    cjk = sum(
        1 for c in text
        if '\u4e00' <= c <= '\u9fff'
        or '\u3000' <= c <= '\u303f'
        or '\uff00' <= c <= '\uffef'
    )
    return math.ceil(cjk + (len(text) - cjk) * 0.5)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    if sys.argv[1] == "--title":
        text = " ".join(sys.argv[2:])
        print(f"{xhs_title_len(text)}")

    elif sys.argv[1] == "--check":
        if len(sys.argv) < 4:
            print("用法: python3 xhs_word_count.py --check <文本> <上限>")
            sys.exit(1)
        text, limit = sys.argv[2], int(sys.argv[3])
        count = xhs_content_len(text)
        status = "✅" if count <= limit else "❌ 超了!"
        print(f"{status}  {count}字/{limit}上限")

    elif sys.argv[1] == "--check-title":
        if len(sys.argv) < 4:
            print("用法: python3 xhs_word_count.py --check-title <文本> <上限>")
            sys.exit(1)
        text, limit = sys.argv[2], int(sys.argv[3])
        count = xhs_title_len(text)
        status = "✅" if count <= limit else "❌ 超了!"
        print(f"{status}  标题{count}字/{limit}上限")

    else:
        text = " ".join(sys.argv[1:])
        print(f"{xhs_content_len(text)}字")


if __name__ == "__main__":
    main()
