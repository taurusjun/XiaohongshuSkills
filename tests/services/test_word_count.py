"""services.word_count 单元测试（唯一实现）。"""
from services import word_count as wc


def test_content_len():
    assert wc.content_len("abc\nabc") == 6
    assert wc.content_len("你好\n世界") == 4
    assert wc.content_len("") == 0


def test_title_len():
    assert wc.title_len("标题") == 2
    assert wc.title_len("abc") == 2       # ceil(1.5)
    assert wc.title_len("标a") == 2       # ceil(1.5)
    assert wc.title_len("") == 0


def test_main_content(capsys):
    assert wc.main(["hello\nworld"]) == 0
    assert capsys.readouterr().out.strip() == "10字"


def test_main_title(capsys):
    assert wc.main(["--title", "标题"]) == 0
    assert capsys.readouterr().out.strip() == "2"


def test_main_check_over(capsys):
    wc.main(["--check", "abcd", "3"])
    assert "❌ 超了!" in capsys.readouterr().out


def test_main_no_args(capsys):
    assert wc.main([]) == 1
