import sqlite3  # noqa: F401  (保持与其它测试一致)
from pathlib import Path
import pytest
from agent import write
from services import paths


def test_grade_keys_from_archive_bold(monkeypatch, tmp_path):
    d = tmp_path / "data" / "reviews"
    d.mkdir(parents=True)
    (d / "2026-10-09-review-full.md").write_text(
        "# x\n\n## 四、分级结果\n\n**S级**\n- `aaaa11112222` 强冲突事件\n"
        "- `bbbb33334444` 名场面\n\n**A级**\n- `cccc55556666` 有故事性\n\n"
        "**B级（轻量跑量）**\n`dddd77778888`、`eeee99990000`\n\n## 五、x\n",
        encoding="utf-8")
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    keys = write._grade_keys_from_archive()
    assert "aaaa11112222" in keys and "bbbb33334444" in keys and "cccc55556666" in keys
    assert "dddd77778888" not in keys          # B级 不选
