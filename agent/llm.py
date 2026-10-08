"""LLM 调用（LiteLLM，OpenAI 兼容 /chat/completions）。

配置来自 env：LITELLM_URL / LITELLM_MODEL / LITELLM_API_KEY（容器 scripts/.env 已具备）。
"""
import json
import os
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


def _base_url() -> str:
    url = os.environ.get("LITELLM_URL", "").rstrip("/")
    if not url:
        raise LiteLLMError("LITELLM_URL 未配置")
    return url


def chat(messages, model=None, temperature=0.0, max_tokens=None, timeout=300) -> str:
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
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('LITELLM_API_KEY', '')}"},
        method="POST",
    )
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]["content"]


def chat_raw(messages, model=None, temperature=0.0, max_tokens=None, tools=None, timeout=300) -> dict:
    """返回完整 assistant message（含 tool_calls），供 function-calling 循环使用。"""
    url = _base_url() + "/chat/completions"
    if max_tokens is None:
        max_tokens = int(os.environ.get("LITELLM_MAX_TOKENS", "3000") or 3000)
    body = {"model": model or os.environ.get("LITELLM_MODEL", ""), "messages": messages,
            "temperature": temperature, "max_tokens": max_tokens}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ.get('LITELLM_API_KEY', '')}"},
        method="POST")
    with _NO_PROXY_OPENER.open(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
    return data["choices"][0]["message"]
