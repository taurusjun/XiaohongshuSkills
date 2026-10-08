"""LLM 调用（LiteLLM，OpenAI 兼容 /chat/completions）。

配置来自 env：LITELLM_URL / LITELLM_MODEL / LITELLM_API_KEY（容器 scripts/.env 已具备）。
"""
import json
import os
import urllib.request

__all__ = ["chat", "LiteLLMError"]


class LiteLLMError(RuntimeError):
    pass


def _base_url() -> str:
    url = os.environ.get("LITELLM_URL", "").rstrip("/")
    if not url:
        raise LiteLLMError("LITELLM_URL 未配置")
    return url


def chat(messages, model=None, temperature=0.0, max_tokens=None, timeout=300) -> str:
    url = _base_url() + "/chat/completions"
    body = {
        "model": model or os.environ.get("LITELLM_MODEL", ""),
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens:
        body["max_tokens"] = max_tokens
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('LITELLM_API_KEY', '')}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]
