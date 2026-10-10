"""LLM 调用（LiteLLM，OpenAI 兼容 /chat/completions）。

配置来自 env：LITELLM_URL / LITELLM_MODEL / LITELLM_API_KEY（容器 scripts/.env 已具备）。
"""
import json
import os
import socket
import time
import urllib.error
import urllib.request

# 直连（不使用 scripts/.env 里指向宿主 127.0.0.1 的代理）
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# 加载 scripts/.env（LITELLM_URL/MODEL/API_KEY 在此）
try:
    from dotenv import load_dotenv
    from services import paths
    load_dotenv(paths.REPO_ROOT / "scripts" / ".env")
except Exception:
    pass

__all__ = ["chat", "LiteLLMError"]


class LiteLLMError(RuntimeError):
    pass


_RETRY_TRIES = int(os.environ.get("LITELLM_RETRY_TRIES", "3") or 3)
_RETRY_BACKOFF = float(os.environ.get("LITELLM_RETRY_BACKOFF", "3") or 3)


def _post(url, body, timeout):
    """POST + 网络抖动重试（超时/连接失败/HTTP 5xx），指数退避；4xx 直接抛。"""
    data = json.dumps(body).encode("utf-8")
    headers = {"Content-Type": "application/json",
               "Authorization": f"Bearer {os.environ.get('LITELLM_API_KEY', '')}"}
    last = None
    for i in range(_RETRY_TRIES):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST")
            with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:          # 子类，须先于 URLError
            if getattr(e, "code", 0) and e.code < 500:
                raise
            last = e
        except urllib.error.URLError as e:
            last = e
        except (TimeoutError, socket.timeout, ConnectionError, OSError) as e:
            last = e
        if i < _RETRY_TRIES - 1:
            time.sleep(_RETRY_BACKOFF * (2 ** i))
    raise LiteLLMError(f"LLM 请求失败（重试 {_RETRY_TRIES} 次）: {last}")


def _base_url() -> str:
    url = os.environ.get("LITELLM_URL", "").rstrip("/")
    if not url:
        raise LiteLLMError("LITELLM_URL 未配置")
    return url


def _apply_thinking(body: dict, thinking):
    """注入 {"thinking": {"type": enabled|disabled}}；默认 disabled（避免推理占用输出预算）。"""
    if thinking is None:
        thinking = os.environ.get("LITELLM_THINKING", "disabled")
    if thinking:
        body["thinking"] = {"type": thinking}
    return body


def chat(messages, model=None, temperature=0.0, max_tokens=None, timeout=300, thinking=None) -> str:
    url = _base_url() + "/chat/completions"
    # 推理模型会把 token 花在 reasoning_content 上；给足预算（默认取 LITELLM_MAX_TOKENS）
    if max_tokens is None:
        max_tokens = int(os.environ.get("LITELLM_MAX_TOKENS", "3000") or 3000)
    body = {
        "model": model or os.environ.get("LITELLM_MODEL", ""),
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    _apply_thinking(body, thinking)
    data = _post(url, body, timeout)
    return data["choices"][0]["message"]["content"]


def chat_raw(messages, model=None, temperature=0.0, max_tokens=None, tools=None, timeout=300, thinking=None) -> dict:
    """返回完整 assistant message（含 tool_calls），供 function-calling 循环使用。"""
    url = _base_url() + "/chat/completions"
    if max_tokens is None:
        max_tokens = int(os.environ.get("LITELLM_MAX_TOKENS", "3000") or 3000)
    body = {"model": model or os.environ.get("LITELLM_MODEL", ""), "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    _apply_thinking(body, thinking)
    data = _post(url, body, timeout)
    return data["choices"][0]["message"]
