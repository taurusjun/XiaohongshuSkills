from services import feedback_patterns as fp


def _mk(tmp_path):
    f = tmp_path / "fb.md"
    f.write_text(
        "# t\n\n### 132. old\n- x\n\n## 跨会话趋势表\n\n"
        "| 日期 | 新增模式 | 来源数据 | 说明 |\n|---|---|---|---|\n"
        "| 2026-10-08 | #1 | src | d |\n", encoding="utf-8")
    return f


def test_update_appends_pattern_and_row(tmp_path, monkeypatch):
    f = _mk(tmp_path)
    monkeypatch.setattr(fp, "_fb_path", lambda: f)
    ok, _ = fp.update("### 133. new\n- y", "| 2026-10-09 | #2 | src2 | d2 |", date="2026-10-09")
    txt = f.read_text(encoding="utf-8")
    assert ok
    assert txt.index("### 133.") < txt.index("## 跨会话趋势表")
    assert txt.rstrip().endswith("| 2026-10-09 | #2 | src2 | d2 |")
    # 无同日注释行时也必须落在表末（回归：旧实现会插到表中间）


def test_update_idempotent_same_date(tmp_path, monkeypatch):
    f = _mk(tmp_path)
    monkeypatch.setattr(fp, "_fb_path", lambda: f)
    fp.update("### 133. new\n- y", "| 2026-10-09 | #2 | src2 | d2 |", date="2026-10-09")
    fp.update("### 134. again\n- z", "| 2026-10-09 | #3 | src3 | d3 |", date="2026-10-09")
    txt = f.read_text(encoding="utf-8")
    rows = [l for l in txt.split("\n") if l.startswith("| 2026-10-09 |")]
    assert len(rows) == 1 and "#3" in rows[0]      # 同日趋势行被替换
    assert txt.count("### 134.") == 0              # 同日模式节跳过
    assert txt.count("### 133.") == 1


def test_update_no_content(tmp_path, monkeypatch):
    f = _mk(tmp_path)
    monkeypatch.setattr(fp, "_fb_path", lambda: f)
    ok, msg = fp.update("", "", date="2026-10-09")
    assert not ok
