from services import validate_tables as vt


VALID = """# 标题

| A | B |
| --- | --- |
| 1 | 2 |

正文
"""

BAD_COLS = """# 标题

| A | B |
| --- | --- |
| 1 | 2 | 3 |

"""


def test_valid_table():
    tables, problems = vt.validate_text(VALID)
    assert tables == 1
    assert problems == []


def test_col_count_mismatch():
    _, problems = vt.validate_text(BAD_COLS)
    assert any("列数不一致" in p for p in problems)
