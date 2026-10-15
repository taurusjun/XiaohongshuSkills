"""llm.chat 对「200 但无 choices」的响应要明确报错，而不是 KeyError。"""
import pytest
from agent import llm


def test_chat_no_choices_raises(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {"error": {"message": "rate limited"}})
    monkeypatch.setenv("LITELLM_URL", "https://x")
    with pytest.raises(llm.LiteLLMError) as e:
        llm.chat([{"role": "user", "content": "hi"}])
    assert "choices" in str(e.value)


def test_chat_empty_content_falls_back_reasoning(monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda *a, **k: {
        "choices": [{"finish_reason": "stop", "message": {"content": "", "reasoning_content": '{"ok": 1}'}}],
        "usage": {}})
    monkeypatch.setenv("LITELLM_URL", "https://x")
    assert llm.chat([{"role": "user", "content": "hi"}]) == '{"ok": 1}'
