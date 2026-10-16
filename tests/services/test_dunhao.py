from services import dunhao


def test_fix_line_keeps_single_dunhao():
    assert dunhao.fix_line("A、B") == "A、B"


def test_fix_line_replaces_all_when_multiple():
    # ≥3 项 → 全部换 ·（不混用 、/·）
    assert dunhao.fix_line("A、B、C") == "A\u00b7B\u00b7C"
    assert dunhao.fix_line("A、B、C、D") == "A\u00b7B\u00b7C\u00b7D"


def test_fix_text_counts_hits():
    text = "A、B、C\n干净行\nX、Y"
    new, hits = dunhao.fix_text(text)
    assert hits == 1
    assert new.split("\n")[0] == "A\u00b7B\u00b7C"
