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

__all__ = ["chat", "chat_raw", "usage", "reset_usage", "LiteLLMError"]


class LiteLLMError(RuntimeError):
    pass


# ── token 用量统计（进程内累计；跑完打印）────────────────────────────
_USAGE = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
          "cache_hit_tokens": 0, "cache_miss_tokens": 0}


def usage() -> dict:
    return dict(_USAGE)


def reset_usage() -> None:
    for k in _USAGE:
        _USAGE[k] = 0


def _add_usage(data: dict) -> None:
    u = (data or {}).get("usage") or {}
    _USAGE["calls"] += 1
    _USAGE["prompt_tokens"] += int(u.get("prompt_tokens") or 0)
    _USAGE["completion_tokens"] += int(u.get("completion_tokens") or 0)
    _USAGE["total_tokens"] += int(u.get("total_tokens") or 0)
    _USAGE["cache_hit_tokens"] += int(u.get("prompt_cache_hit_tokens")
                                     or u.get("cache_read_input_tokens") or 0)
    _USAGE["cache_miss_tokens"] += int(u.get("prompt_cache_miss_tokens")
                                      or u.get("cache_creation_input_tokens") or 0)


def format_usage(label="") -> str:
    u = _USAGE
    return (f"[token] {label}调用={u['calls']} 输入={u['prompt_tokens']} 输出={u['completion_tokens']} "
            f"合计={u['total_tokens']}（缓存命中={u['cache_hit_tokens']} 未命中={u['cache_miss_tokens']}）")


_RETRY_TRIES = int(os.environ.get("LITELLM_RETRY_TRIES", "3") or 3)
_TIMEOUT = int(os.environ.get("LITELLM_TIMEOUT", "60") or 60)
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
    try:
        from services import error_log as _elog
        _elog.log(f"LLM 请求失败（重试 {_RETRY_TRIES} 次）: {last}")
    except Exception:  # noqa: BLE001
        pass
    raise LiteLLMError(f"LLM 请求失败（重试 {_RETRY_TRIES} 次）: {last}")


def _base_url() -> str:
    url = os.environ.get("LITELLM_URL", "").rstrip("/")
    if not url:
        raise LiteLLMError("LITELLM_URL 未配置")
    return url


def _apply_thinking(body: dict, thinking):
    """注入 {"thinking": {"type": enabled|disabled}}；默认 disabled（避免推理占用输出预算）。"""
    if thinking is None:
        # 注意：环境变量「存在但为空串」时 os.environ.get(k, default) 会返回 ""，
        # 之前 `if thinking:` 判假 → thinking 字段被丢弃 → 模型走默认（思考模式默认开）→
        # 内容全进 reasoning_content、content 为空。这里用 or 兜底成 disabled。
        thinking = (os.environ.get("LITELLM_THINKING") or "disabled").strip()
    if thinking:
        body["thinking"] = {"type": thinking}
    return body


def chat(messages, model=None, temperature=0.0, max_tokens=None, timeout=None, thinking=None, json_mode=False) -> str:
    timeout = timeout or _TIMEOUT
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
    if json_mode:                                   # DeepSeek JSON Output：保证合法 JSON
        body["response_format"] = {"type": "json_object"}
    _apply_thinking(body, thinking)
    data = _post(url, body, timeout)
    _add_usage(data)
    ch0 = data["choices"][0]
    msg = ch0["message"]
    content = (msg.get("content") or "").strip()
    if not content:
        # 推理模型偶发把内容全放进 reasoning_content（content 为空）→ 兜底提取
        reasoning = (msg.get("reasoning_content") or "").strip()
        if reasoning:
            import re as _re
            m = _re.search(r"\{.*\}", reasoning, _re.S)
            content = (m.group(0) if m else reasoning).strip()
        if not content:
            try:
                from services import error_log as _elog
                _elog.log(f"LLM 返回空 content（finish_reason={ch0.get('finish_reason')}）")
            except Exception:  # noqa: BLE001
                pass
    return content


def chat_raw(messages, model=None, temperature=0.0, max_tokens=None, tools=None, timeout=None, thinking=None) -> dict:
    timeout = timeout or _TIMEOUT
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
    _add_usage(data)
    return data["choices"][0]["message"]
