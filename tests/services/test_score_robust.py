"""评分解析容错 + 重试（score_content/score_gzh）单测。"""
import agent.write as W


def test_extract_json_prose_and_balance():
    assert W._extract_json('前言 {"a":1} 后记') == {"a": 1}
    # 多个对象：取第一个配平对象
    assert W._extract_json('{ "x": "a}b" } tail {"y":2}') == {"x": "a}b"}
    assert W._extract_json('no json here') is None
    assert W._extract_json('{bad json}') is None


def test_score_retry_then_success(monkeypatch):
    calls = {"n": 0}

    def fake(messages, max_tokens=0):
        calls["n"] += 1
        return "不是JSON" if calls["n"] == 1 else '{"爆发点":2,"情绪价值":2,"信息增量":2,"内容深度":1,"标题质量":1,"total":8}'

    monkeypatch.setattr(W.llm, "chat", fake)
    d = W.score_content("t", "b")
    assert d["total"] == 8 and calls["n"] == 2


def test_score_synth_total_when_missing(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0:
                        '{"爆发点":2,"情绪价值":1,"信息增量":2,"内容深度":1,"标题质量":1}')
    d = W.score_content("t", "b")
    assert d["total"] == 7            # sum 合成


def test_score_gzh_avg_when_missing(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0:
                        '{"标题吸引力":8,"叙事质量":6,"公众号适配度":7}')
    d = W.score_gzh("t", "b")
    assert d["total"] == 7            # round(21/3)=7


def test_score_all_fail_total_zero(monkeypatch):
    monkeypatch.setattr(W.llm, "chat", lambda m, max_tokens=0: "永远不是JSON")
    d = W.score_content("t", "b")
    assert d["total"] == 0 and "error" in d
