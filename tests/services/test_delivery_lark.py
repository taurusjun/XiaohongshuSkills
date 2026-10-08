from services.delivery import feishu_md_to_lark


def test_table_becomes_bullets_and_headings_bold():
    md = ("# 每日 Review\n\n"
          "| key | ts | cs | title |\n|---|---|---|---|\n"
          "| `abc` | 4.2 | 1.1 | 标题甲 |\n| `def` | 3.0 | 0.5 | 标题乙 |\n")
    lark = feishu_md_to_lark(md)
    assert "**每日 Review**" in lark
    assert "**标题甲**" in lark and "ts=4.2" in lark and "cs=1.1" in lark
    assert "**标题乙**" in lark
    assert "|---|" not in lark            # 表格分隔行消失
