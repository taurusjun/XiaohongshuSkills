"""契约：容器对外发布的"宿主端口"必须避开生产占用（5000/9222/9223…）。

容器内 5000/5001/9222 只在容器网络命名空间；对外一律重映射到 15000/15001/19222，
避免与生产 webapp(5000)/Chrome CDP(9222/9223) 冲突。
"""
import re

import yaml

from services import paths

ALLOWED = {15000, 15001, 19222}
FORBIDDEN = {5000, 5001, 9222, 9223}
COMPOSE_FILES = ("docker-compose.yml", "docker-compose.override.mac.yml")


def _dotenv():
    f = paths.REPO_ROOT / ".env"
    d = {}
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                d[k.strip()] = v.strip()
    return d


def _resolve(s, env):
    return re.sub(r"\$\{(\w+)(?::-([^}]*))?\}",
                  lambda m: env.get(m.group(1), m.group(2) or ""), s)


def _host_ports(env):
    out = []
    for name in COMPOSE_FILES:
        f = paths.REPO_ROOT / name
        if not f.exists():
            continue
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for svc in (doc.get("services") or {}).values():
            for p in (svc.get("ports") or []):
                s = _resolve(str(p), env)
                parts = s.split(":")
                host = parts[-2] if len(parts) >= 3 else parts[0]
                out.append((s, int(re.sub(r"\D", "", host.split("/")[0]) or "0")))
    return out


def test_host_ports_avoid_production():
    for s, port in _host_ports(_dotenv()):
        assert port in ALLOWED, f"宿主端口 {port} 不在允许集 {ALLOWED}：{s}"
        assert port not in FORBIDDEN, f"宿主端口 {port} 撞生产：{s}"


def test_compose_defaults_are_safe():
    """即使 .env 丢失，compose 默认宿主端口也必须安全（否则会撞生产）。"""
    for s, port in _host_ports({}):
        assert port in ALLOWED and port not in FORBIDDEN, f"默认端口不安全：{s}"
