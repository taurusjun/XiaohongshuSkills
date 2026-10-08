"""特征化测试：锁定迁移后 xhs_word_count.py 的行为（迁移前=迁移后）。"""
import subprocess
import sys
from pathlib import Path

import xhs_word_count as wc

SKILL_SCRIPTS = (
    Path(__file__).resolve().parents[2]
    / "skills" / "creative" / "xhs-write-publish-flow" / "scripts"
)
SCRIPT = SKILL_SCRIPTS / "xhs_word_count.py"


def test_content_len_ignores_newlines():
    assert wc.xhs_content_len("abc\nabc") == 6
    assert wc.xhs_content_len("你好\n世界") == 4
    assert wc.xhs_content_len("") == 0


def test_title_len_cjk_fullwidth_and_halfwidth():
    assert wc.xhs_title_len("标题") == 2          # 2 个汉字
    assert wc.xhs_title_len("abc") == 2           # ceil(3*0.5)
    assert wc.xhs_title_len("标a") == 2           # ceil(1 + 0.5)
    assert wc.xhs_title_len("") == 0


def test_cli_content_len():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "hello\nworld"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "10字"           # 11 - 1 换行


def test_cli_title_len():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--title", "标题"],
        capture_output=True, text=True, check=True,
    )
    assert out.stdout.strip() == "2"
