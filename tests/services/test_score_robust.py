"""5 维评分：每维{score,reason}+建议、容错、缺total合成；gzh 均分。"""
import agent.write as W

_R = lambda n: '{"score":%d,"reason":"r"}' % n
_GOOD = ('{"爆发点":%s,"情绪价值":%s,"信息增量":%s,"内容深度":%s,"标题质量":%s,'
         '"total":8,"建议":"加强开头"}' % (_R(2), _R(2), _R(2), _R(1), _R(1)))


def test_extract_json_prose_and_balance():
    assert W._extract_json('前言 {"a":1} 后记') == {"a": 1}
    assert W._extract_json('{ "x": "a}b" } tail {"y":2}') == {"x": "a}b"}
    assert W._extract_json('no json') is None


def test_score_content_with_reasons(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0: _GOOD)
    d = W.score_content("t", "b")
    assert d["total"] == 8 and d["建议"] == "加强开头"
    assert d["爆发点"]["reason"] == "r"


def test_score_content_synth_total(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0:
                        '{"爆发点":%s,"情绪价值":%s,"信息增量":%s,"内容深度":%s,"标题质量":%s}'
                        % (_R(2), _R(1), _R(1), _R(1), _R(1)))       # 缺 total → 合成 6
    assert W.score_content("t", "b")["total"] == 6


def test_score_all_fail_total_zero(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0: "not json")
    d = W.score_content("t", "b")
    assert d["total"] == 0 and "error" in d


def test_score_gzh_avg(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0:
                        '{"标题吸引力":1,"叙事质量":0.5,"公众号适配度":1,"total":8.3}')
    assert W.score_gzh("t", "b")["total"] == 8.3
