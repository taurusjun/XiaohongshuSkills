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


def _fake_opener(seq):
    """seq: 依次返回的 bytes 体。"""
    calls = {"n": 0}
    class R:
        def __init__(self, b): self._b = b
        def read(self): return self._b
        def __enter__(self): return self
        def __exit__(self, *a): return False
    class O:
        def open(self, req, timeout=None):
            i = min(calls["n"], len(seq) - 1); calls["n"] += 1
            return R(seq[i])
    return O(), calls


def test_post_retries_on_empty_body(monkeypatch):
    from agent import llm
    o, calls = _fake_opener([b"", b'{"choices":[{"message":{"content":"ok"}}]}'])
    monkeypatch.setattr(llm, "_NO_PROXY_OPENER", o)
    monkeypatch.setattr(llm, "_RETRY_BACKOFF", 0)
    assert llm._post("https://x", {}, 5)["choices"]
    assert calls["n"] == 2                      # 空体 → 重试一次成功


def test_post_retries_on_missing_choices(monkeypatch):
    from agent import llm
    o, calls = _fake_opener([b'{"error":{"message":"rate limited"}}',
                             b'{"choices":[{"message":{"content":"ok"}}]}'])
    monkeypatch.setattr(llm, "_NO_PROXY_OPENER", o)
    monkeypatch.setattr(llm, "_RETRY_BACKOFF", 0)
    assert llm._post("https://x", {}, 5)["choices"]
    assert calls["n"] == 2


def test_post_gives_up_after_retries(monkeypatch):
    from agent import llm
    o, calls = _fake_opener([b"", b"", b""])
    monkeypatch.setattr(llm, "_NO_PROXY_OPENER", o)
    monkeypatch.setattr(llm, "_RETRY_BACKOFF", 0)
    monkeypatch.setattr(llm, "_RETRY_TRIES", 3)
    with pytest.raises(llm.LiteLLMError):
        llm._post("https://x", {}, 5)
    assert calls["n"] == 3
