"""契约：CLI 皮 == service 芯（同输入同输出）。"""
from cli import __main__ as cli
from services import word_count


def test_cli_word_count_matches_service(capsys):
    assert cli.main(["word-count", "hello\nworld"]) == 0
    assert capsys.readouterr().out.strip() == "10字"


def test_cli_unknown_cmd():
    assert cli.main(["nope"]) == 2


def test_cli_help():
    assert cli.main(["--help"]) == 0
