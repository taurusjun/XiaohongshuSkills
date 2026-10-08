from services import draft_io


def test_strip_md_title():
    assert draft_io.strip_md_title("## 标题") == "标题"
    assert draft_io.strip_md_title("# 标题") == "标题"
    assert draft_io.strip_md_title("标题") == "标题"


def test_render_dump_not_found():
    out = draft_io.render_dump([{"key": "abc"}], ["zzz"])
    assert "NOT FOUND" in out


def test_render_dump_basic():
    rows = [{"key": "abcdef", "title": "T", "format": "news", "is_long_form": 0,
             "content_ja": "あ" * 3, "title_score": 1, "content_score": 2,
             "link": "u", "summary": "s"}]
    out = draft_io.render_dump(rows, ["abc"])
    assert "===== abcdef | T | fmt=news lf=0 | ja_len=3" in out
    assert "--- content_ja ---" in out
