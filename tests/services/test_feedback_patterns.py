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


def test_runtime_path_env_and_seed(tmp_path, monkeypatch):
    from pathlib import Path
    seed = tmp_path / "seed.md"
    seed.write_text("## 跨会话趋势表\n\n| a | b |\n|---|---|\n| x | y |\n", encoding="utf-8")
    monkeypatch.setattr(fp, "_seed_path", lambda: seed)
    monkeypatch.setenv("XHS_FEEDBACK_MD", str(tmp_path / "rt.md"))
    p = fp._fb_path()
    assert Path(p) == tmp_path / "rt.md"
    assert p.exists()                      # 首次使用从种子拷贝
    assert p.read_text(encoding="utf-8") == seed.read_text(encoding="utf-8")


def test_insert_assigns_no_and_relevant_hits(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(fp.paths, "sqlite_path", lambda: str(db))
    monkeypatch.setattr(fp, "_ensure_seed", lambda: None)      # 临时库不灌历史
    rows = fp.insert_patterns([
        {"title": "分析性长标题零曝光（X）", "tags": "娱乐,分析性标题", "body": "- 规律：分析性长标题无初始分发"},
        {"title": "经济+数字对比有流量（Y）", "tags": "经济,数字对比", "body": "- 规律：经济类目数字对比钩子有效"},
    ], date="2026-10-09")
    assert [r["no"] for r in rows] == [1, 2]
    assert "经济+数字对比有流量" in fp.relevant("FRUITS 经济 数字对比")


def test_update_row_and_get(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setattr(fp.paths, "sqlite_path", lambda: str(db))
    monkeypatch.setattr(fp, "_ensure_seed", lambda: None)
    fp.insert_patterns([{"title": "T", "body": "b", "category": "娱乐",
                         "direction": "零曝光", "action": "慎用"}], date="2026-10-09")
    assert fp.update_row(1, {"action": "优先", "entities": "甲,乙"})
    r = fp.get_pattern(1)
    assert r["action"] == "优先" and r["entities"] == "甲,乙" and r["category"] == "娱乐"
    assert fp.update_row(1, {}) is False
