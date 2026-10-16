"""GLM 客户端（独立于 agent/llm.py）——用于「不同厂商」的独立审计/复评。

端点：scripts/.env 的 GLM_URL / GLM_KEY / GLM_MODEL（默认 119.23.59.147:8080/v1）。
特点：glm-5.3-flash 是推理模型 → 默认 thinking=disabled + 给足 max_tokens；
      **直连不走代理**（trust_env=False）。
"""
import json
import os
import re

import requests

__all__ = ["chat", "GLMError"]

_DEFAULT_URL = "http://119.23.59.147:8080/v1"
_DEFAULT_MODEL = "glm-5.3-flash"

# 从 scripts/.env 兜底读取
try:
    from services import paths as _p
    _envf = _p.REPO_ROOT / "scripts" / ".env"
    if _envf.exists():
        for _line in _envf.read_text(encoding="utf-8").splitlines():
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())
except Exception:  # noqa: BLE001
    pass

_S = requests.Session()
_S.trust_env = False          # 直连，忽略 HTTP(S)_PROXY


class GLMError(RuntimeError):
    pass


def _url() -> str:
    return (os.environ.get("GLM_URL") or _DEFAULT_URL).rstrip("/")


def _model() -> str:
    return os.environ.get("GLM_MODEL") or _DEFAULT_MODEL


def chat(prompt: str, system: str = "", max_tokens: int = 2500, temperature: float = 0.2,
         timeout: int = 240, thinking: str = "disabled") -> str:
    """调用 GLM，返回文本（content；空则回退 reasoning）。"""
    key = os.environ.get("GLM_KEY", "")
    if not key:
        raise GLMError("GLM_KEY 未配置（scripts/.env）")
    msgs = ([{"role": "system", "content": system}] if system else []) + \
           [{"role": "user", "content": prompt}]
    body = {"model": _model(), "messages": msgs, "max_tokens": max_tokens,
            "temperature": temperature, "thinking": {"type": thinking}}
    try:
        r = _S.post(f"{_url()}/chat/completions",
                    headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                    json=body, timeout=timeout)
        r.raise_for_status()
        d = r.json()
    except Exception as e:  # noqa: BLE001
        raise GLMError(f"GLM 请求失败: {e}") from e
    ch = (d or {}).get("choices")
    if not ch:
        raise GLMError(f"GLM 响应无 choices: {str(d)[:200]}")
    m = ch[0].get("message") or {}
    c = (m.get("content") or "").strip()
    return c or (m.get("reasoning") or "").strip()


def chat_json(prompt: str, system: str = "", max_tokens: int = 2500, timeout: int = 240):
    """调用并解析 JSON（从文本里提取第一个 {...}）。"""
    txt = chat(prompt, system=system, max_tokens=max_tokens, timeout=timeout)
    m = re.search(r"\{.*\}", txt or "", re.S)
    if not m:
        raise GLMError(f"GLM 未返回 JSON: {txt[:200]!r}")
    return json.loads(m.group(0))
