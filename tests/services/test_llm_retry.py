"""LLM 请求网络抖动重试（agent.llm._post）单测。"""
import json
import urllib.error

import agent.llm as L


class _Resp:
    def __init__(self, obj):
        self._b = json.dumps(obj).encode()

    def read(self):
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_post_retries_then_success(monkeypatch):
    calls = {"n": 0}

    class Opener:
        def open(self, req, timeout=0):
            calls["n"] += 1
            if calls["n"] < 3:
                raise urllib.error.URLError("boom")
            return _Resp({"ok": True})

    monkeypatch.setattr(L, "_NO_PROXY_OPENER", Opener())
    monkeypatch.setattr(L, "_RETRY_BACKOFF", 0)
    assert L._post("http://x/y", {"a": 1}, 5) == {"ok": True}
    assert calls["n"] == 3


def test_post_gives_up_after_tries(monkeypatch):
    class Opener:
        def open(self, req, timeout=0):
            raise urllib.error.URLError("always")

    monkeypatch.setattr(L, "_NO_PROXY_OPENER", Opener())
    monkeypatch.setattr(L, "_RETRY_BACKOFF", 0)
    monkeypatch.setattr(L, "_RETRY_TRIES", 2)
    try:
        L._post("http://x/y", {}, 5)
        assert False, "should raise"
    except L.LiteLLMError:
        pass


def test_post_4xx_no_retry(monkeypatch):
    calls = {"n": 0}

    class Opener:
        def open(self, req, timeout=0):
            calls["n"] += 1
            raise urllib.error.HTTPError("u", 400, "bad", {}, None)

    monkeypatch.setattr(L, "_NO_PROXY_OPENER", Opener())
    monkeypatch.setattr(L, "_RETRY_BACKOFF", 0)
    try:
        L._post("http://x/y", {}, 5)
        assert False, "should raise"
    except urllib.error.HTTPError:
        pass
    assert calls["n"] == 1
