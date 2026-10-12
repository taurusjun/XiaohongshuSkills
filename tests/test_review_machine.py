from agent import review


def test_extract_machine_json():
    txt = ('## 二、x\n\n正文\n\n```json\n'
           '{"grades": {"aaaa11112222": "S"}, "clusters": [["aaaa11112222", "bbbb33334444"]], '
           '"channel_hint": {"aaaa11112222": "gzh"}, "akb_type": {"aaaa11112222": "bullet"}, '
           '"feedback": {"pattern": "### 133. X", "trend_row": "| d | x | y | z |"}}\n```\n')
    machine, cleaned = review._extract_machine_json(txt)
    assert machine["grades"] == {"aaaa11112222": "S"}
    assert machine["clusters"] == [["aaaa11112222", "bbbb33334444"]]
    assert machine["channel_hint"] == {"aaaa11112222": "gzh"}
    assert machine["akb_type"] == {"aaaa11112222": "bullet"}
    assert machine["feedback"] == {"pattern": "### 133. X", "trend_row": "| d | x | y | z |"}
    assert "```json" not in cleaned and "## 二、x" in cleaned


def test_extract_machine_json_absent():
    m, cleaned = review._extract_machine_json("## 二、无json")
    assert m is None and cleaned == "## 二、无json"


def test_run_structured_json_path(monkeypatch):
    """新格式：LLM 返回单个 JSON 对象 → run 解析 + 模板渲染 + persist 收 grade。"""
    import json
    from agent import review
    data = {"grades": {"aaaa11112222": {"grade": "S", "reason": "事件"}},
            "clusters": [{"keys": ["aaaa11112222", "bbbb33334444"], "main": "aaaa11112222", "grade": "S"}],
            "layer4": {"conclusion": "本期无发布数据"}}
    monkeypatch.setattr(review, "prepare_package", lambda d: {"rows": [{"key": "aaaa11112222" * 3, "title": "T"}]})
    monkeypatch.setattr(review, "build_messages", lambda d, pkg: [])
    monkeypatch.setattr(review.llm, "chat", lambda *a, **k: json.dumps(data, ensure_ascii=False))
    import services.review_archive as ra
    monkeypatch.setattr(ra, "build_archive", lambda d, single_table=False: "## 一、全量")
    captured = {}
    monkeypatch.setattr(review, "persist", lambda *a, **k: captured.update({"grades": a[1]}) or (1, 1, 0, 0, 0))
    monkeypatch.setattr(review, "_deliver", lambda *a, **k: None, raising=False)
    out = review.run("2026-10-12")
    assert captured["grades"] == {"aaaa11112222": "S"}          # dict→grade 已展平
    assert "## 四、" in out and "### S级" in out                  # 报告由模板渲染
