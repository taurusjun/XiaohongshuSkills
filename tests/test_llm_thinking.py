"""思考模式开关：环境变量为空串/未设时都必须兜底 disabled（DeepSeek 默认开思考）。"""
from agent import llm


def test_apply_thinking_defaults_disabled(monkeypatch):
    monkeypatch.delenv("LITELLM_THINKING", raising=False)
    assert llm._apply_thinking({}, None)["thinking"]["type"] == "disabled"
    monkeypatch.setenv("LITELLM_THINKING", "")
    assert llm._apply_thinking({}, None)["thinking"]["type"] == "disabled"      # 空串也要 disabled
    monkeypatch.setenv("LITELLM_THINKING", "enabled")
    assert llm._apply_thinking({}, None)["thinking"]["type"] == "enabled"
    assert llm._apply_thinking({}, "disabled")["thinking"]["type"] == "disabled"
