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
