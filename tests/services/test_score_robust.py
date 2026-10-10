"""评分：5 维门槛(gate) + 21 维明细(dims) + 取低 + JSON 容错。"""
import agent.write as W

_G8 = ('{"gate":{"爆发点":{"score":2},"情绪价值":{"score":2},"信息增量":{"score":2},'
       '"内容深度":{"score":1},"标题质量":{"score":1}},'
       '"dims":{"剧情感":{"score":1},"冲突感":{"score":0.5}},"total":999}')
_G5 = ('{"gate":{"爆发点":{"score":1},"情绪价值":{"score":1},"信息增量":{"score":1},'
       '"内容深度":{"score":1},"标题质量":{"score":1}},'
       '"dims":{"剧情感":{"score":0.5}},"total":5}')


def test_extract_json_prose_and_balance():
    assert W._extract_json('前言 {"a":1} 后记') == {"a": 1}
    assert W._extract_json('{ "x": "a}b" } tail {"y":2}') == {"x": "a}b"}
    assert W._extract_json('no json') is None


def test_score_content_gate_sum(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0: _G8)   # 忽略 total:999
    d = W.score_content("t", "b", run_times=1)
    assert d["total"] == 8.0


def test_score_content_takes_min_of_runs(monkeypatch):
    it = iter([_G8, _G5])
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0: next(it))
    assert W.score_content("t", "b", run_times=2)["total"] == 5.0     # 取偏低


def test_score_all_fail_total_zero(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0: "not json")
    d = W.score_content("t", "b", run_times=1)
    assert d["total"] == 0 and "error" in d


def test_score_gzh_avg(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0:
                        '{"标题吸引力":{"score":1},"叙事质量":{"score":0.5},"公众号适配度":{"score":1},"total":999}')
    assert W.score_gzh("t", "b", run_times=1)["total"] == round(10 * 2.5 / 3, 1)
