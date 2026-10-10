"""写稿失败队列：落表/到期重试/标记成功(不删)/转待人工 单测。"""
import datetime

from services import write_failures as wf


def _iso(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_PATH", str(tmp_path / "wf.db"))


def test_record_due_and_resolve_marks_success(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    wf.record("k1", "URLError: timeout", stage="exception", tb="Traceback (most recent call last):\n...")
    rows = wf.list_open()
    assert len(rows) == 1 and rows[0]["status"] == "pending"
    assert rows[0]["traceback"].startswith("Traceback")
    assert wf.due() == []                                   # 未到期（1 小时后）
    assert len(wf.due(datetime.datetime.now() + datetime.timedelta(hours=2))) == 1
    wf.mark_resolved("k1", "重试成功")                        # 标记成功，不删除
    assert wf.list_open() == []
    allr = wf.list_all()
    assert len(allr) == 1 and allr[0]["status"] == "resolved"


def test_mark_manual_keeps_traceback(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    wf.record("k2", "gate: 密度不足")
    wf.mark_manual("k2", "再次失败", tb="TB2")
    r = wf.list_open("needs_manual")[0]
    assert r["retry_count"] == 1 and r["traceback"] == "TB2" and r["status"] == "needs_manual"


def test_record_upsert_accumulates(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    wf.record("k3", "a", attempts=1)
    wf.record("k3", "b", attempts=2)
    r = wf.list_all()[0]
    assert r["attempts"] == 3 and r["reason"] == "b"


def test_set_due_now(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    wf.record("k4", "x")
    assert wf.due() == []
    wf.set_due_now("k4")
    assert len(wf.due()) == 1


def test_categorize():
    assert wf.categorize("URLError: <urlopen error timed out>", "exception", "URLError") == "llm_network"
    assert wf.categorize("LiteLLMError: LLM 请求失败", "exception") == "llm_network"
    assert wf.categorize("ValueError: boom", "exception") == "exception_other"
    assert wf.categorize("密度 22.0% < 30%") == "gate_density"
    assert wf.categorize("story 正文 700 字 < 800（硬门禁）") == "gate_length"
    assert wf.categorize("story 正文 `##` 小标题 0 个 < 2") == "gate_structure"
    assert wf.categorize("假名 9 > 5") == "gate_kana"
    assert wf.categorize("正文为空（LLM 未产出正文）") == "empty_body"
    assert wf.categorize("renwei 意义拔高") == "renwei"


def test_stats_and_category_persist(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    wf.record("a1", "密度 10% < 30%")
    wf.record("a2", "密度 12% < 30%")
    wf.record("a3", "URLError: timed out", stage="exception", tb="urllib.error.URLError")
    st = wf.stats(30)
    assert st["total"] == 3
    cats = {x["category"]: x["n"] for x in st["by_category"]}
    assert cats["gate_density"] == 2 and cats["llm_network"] == 1


def test_warn_level(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    wf.warn("kw", "门禁/评分豁免：假名 6>5; 内容评分 7<9", category="gate_exempt")
    r = wf.list_all()[0]
    assert r["level"] == "warn" and r["status"] == "resolved" and r["category"] == "gate_exempt"
    assert wf.list_open() == []          # warn 不进入重试队列
