"""代理判定规则（与生产对齐）。

约定（2026-10-30）：
  - 代理地址只来自 scripts/.env；业务代码**不得**做端口扫描/漂移发现
  - 端口漂移由 ~/gateway-watchdog.sh 负责（sed .env + 重启消费进程）
  - 名单：PROXY_REQUIRED_DOMAINS（必须代理）/ DIRECT_ONLY_DOMAINS（强制直连）
"""
import pathlib


def test_get_proxies_empty_when_no_proxy(monkeypatch):
    import config.yahoo_conf as y
    monkeypatch.setattr(y, "PROXY_URL", "")
    assert y.get_proxies() == {}


def test_get_proxies_returns_both_schemes(monkeypatch):
    import config.yahoo_conf as y
    monkeypatch.setattr(y, "PROXY_URL", "http://127.0.0.1:20809")
    assert y.get_proxies() == {
        "http": "http://127.0.0.1:20809",
        "https": "http://127.0.0.1:20809",
    }


def test_domain_lists():
    from config.net_fallback import _match, DIRECT_ONLY_DOMAINS, PROXY_REQUIRED_DOMAINS
    assert _match("realsound.jp", DIRECT_ONLY_DOMAINS)
    assert _match("www.realsound.jp", DIRECT_ONLY_DOMAINS)
    assert _match("x.com", PROXY_REQUIRED_DOMAINS)
    assert _match("www.youtube.com", PROXY_REQUIRED_DOMAINS)
    assert not _match("instagram.com", DIRECT_ONLY_DOMAINS)


def test_no_port_scanning_in_code():
    """守卫：业务代码里不允许再出现端口扫描/漂移发现（只允许 watchdog 做）。"""
    root = pathlib.Path(__file__).resolve().parent.parent
    bad = []
    for f in list((root / "config").rglob("*.py")) + list((root / "services").rglob("*.py")):
        if ".venv" in str(f):
            continue
        t = f.read_text(encoding="utf-8", errors="ignore")
        for pat in ("_discover_port", "proxy_alive", "range(20800", "seq 20800"):
            if pat in t:
                bad.append(f"{f.name}:{pat}")
    assert not bad, f"业务代码里出现了端口扫描：{bad}"
