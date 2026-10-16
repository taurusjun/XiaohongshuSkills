"""requests 代理失败自动改走直连重试（进程级，安装一次全局生效）。

背景：本机代理（scripts/.env 的 HTTP_PROXY）时好时坏，断的时候所有走代理的请求都在连接 / TLS 握手
阶段失败（SSLEOFError、ProxyError、超时），而日本新闻站直连本来就是通的。这里在
requests.Session.request 外包一层：请求「实际走了代理」且失败在连接/握手/超时阶段时，对不需要代理的
域名改走直连重试一次。requests.get / requests.post 和各种 Session 最终都经过 Session.request，
所以 gallery_fetch、scrapers/*、web/gallery_downloader、yahoo 脚本里的调用都会覆盖到。

不回退的情况：
- 需要代理才能访问的域名（X / Instagram / YouTube / Google / TikTok / Telegram 等）：直连必然失败，只会白等；
- 本来就没走代理的请求（如 CDP 的 127.0.0.1、NO_PROXY 里的域名、显式 proxies={"http": None, ...}）。

关闭自动回退：环境变量 NET_DIRECT_FALLBACK=0。

另外，DIRECT_ONLY_DOMAINS 里的域名一律强制直连（不受上面的开关影响）：这些站的 WAF 拦的是代理出口 IP，
代理正常时返回的是 405 验证页（不抛异常，自动回退兜不住），只有直连能拿到内容。
"""
import os
from urllib.parse import urlparse

# 需要代理才能访问的域名（含子域名）
PROXY_REQUIRED_DOMAINS = (
    "x.com", "twitter.com", "twimg.com", "t.co", "fxtwitter.com", "vxtwitter.com",
    "instagram.com", "cdninstagram.com", "fbcdn.net", "facebook.com",
    "youtube.com", "youtu.be", "googlevideo.com", "ytimg.com",
    "google.com", "googleapis.com", "gstatic.com", "googleusercontent.com",
    "tiktok.com", "tiktokcdn.com", "tiktokv.com",
    "telegram.org", "t.me",
)


# 必须直连的域名（WAF 拦代理出口 IP；本机直连无 WAF）
DIRECT_ONLY_DOMAINS = (
    "realsound.jp",
)
_DIRECT = {"http": None, "https": None}  # None 会覆盖环境变量里的代理


def _match(host: str, domains) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def _needs_proxy(host: str) -> bool:
    return _match(host, PROXY_REQUIRED_DOMAINS)


def _proxy_in_effect(session, url: str, proxies) -> bool:
    """这次请求是否真的会走代理（与 requests 的合并规则一致）。"""
    import requests
    scheme = urlparse(url).scheme or "http"
    if proxies:  # 显式传了 proxies；{"http": None, "https": None} 表示明确直连
        return bool(proxies.get(scheme) or proxies.get("all"))
    if not session.trust_env:  # proxies 为空时 requests 会回落到环境变量里的代理
        return False
    env = requests.utils.get_environ_proxies(url)
    return bool(env.get(scheme) or env.get("all"))


def install() -> None:
    import requests
    from requests import exceptions as rex

    if getattr(requests.Session.request, "_xhs_direct_fallback", False):
        return  # 已安装
    fallback_on = os.environ.get("NET_DIRECT_FALLBACK", "1") != "0"
    orig = requests.Session.request

    def request(self, method, url, *args, **kwargs):
        host = (urlparse(url).hostname or "").lower()
        if not args and host and _match(host, DIRECT_ONLY_DOMAINS):
            kwargs["proxies"] = dict(_DIRECT)
            return orig(self, method, url, **kwargs)
        try:
            return orig(self, method, url, *args, **kwargs)
        except (rex.ConnectionError, rex.Timeout) as e:
            if (not fallback_on or args or not host or _needs_proxy(host)
                    or not _proxy_in_effect(self, url, kwargs.get("proxies"))):
                raise
            print(f"  ↪ 代理请求失败（{type(e).__name__}），改走直连重试: {host}")
            direct = dict(kwargs)
            direct["proxies"] = dict(_DIRECT)
            try:
                return orig(self, method, url, **direct)
            except Exception as e2:
                try:
                    from sqlite_db import _log_db_error
                    _log_db_error(f"代理失败后直连也失败: {url} | 代理: {e} | 直连: {e2}")
                except Exception:
                    pass
                raise e

    request._xhs_direct_fallback = True
    requests.Session.request = request
