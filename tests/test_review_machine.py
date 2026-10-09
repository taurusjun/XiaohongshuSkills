from agent import review


def test_extract_machine_json():
    txt = ('## 二、x\n\n正文\n\n```json\n'
           '{"grades": {"aaaa11112222": "S"}, "clusters": [["aaaa11112222", "bbbb33334444"]], '
           '"feedback": {"pattern": "### 133. X", "trend_row": "| d | x | y | z |"}}\n```\n')
    grades, clusters, feedback, cleaned = review._extract_machine_json(txt)
    assert grades == {"aaaa11112222": "S"}
    assert clusters == [["aaaa11112222", "bbbb33334444"]]
    assert feedback == {"pattern": "### 133. X", "trend_row": "| d | x | y | z |"}
    assert "```json" not in cleaned and "## 二、x" in cleaned


def test_extract_machine_json_absent():
    g, c, fb, cleaned = review._extract_machine_json("## 二、无json")
    assert g is None and c is None and fb is None and cleaned == "## 二、无json"
