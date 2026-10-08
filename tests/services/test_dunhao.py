from services import dunhao


def test_fix_line_keeps_single_dunhao():
    assert dunhao.fix_line("A、B") == "A、B"


def test_fix_line_replaces_second_and_later():
    assert dunhao.fix_line("A、B、C") == "A、B\u00b7C"
    assert dunhao.fix_line("A、B、C、D") == "A、B\u00b7C\u00b7D"


def test_fix_text_counts_hits():
    text = "A、B、C\n干净行\nX、Y"
    new, hits = dunhao.fix_text(text)
    assert hits == 1
    assert new.split("\n")[0] == "A、B\u00b7C"
