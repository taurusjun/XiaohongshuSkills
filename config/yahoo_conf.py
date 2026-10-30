"""非敏感配置中心 — Yahoo Pipeline 全局配置"""
import os
from pathlib import Path

# 存储后端: "sqlite" | "notion"（默认 sqlite）
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "sqlite")

# SQLite 数据库路径（默认开发库 data/news_dev.db）
DB_PATH = os.environ.get("SQLITE_PATH",
    str(Path(__file__).resolve().parent.parent / "data" / "news_dev.db"))

# 图集下载缓存目录
GALLERY_CACHE_DIR = os.environ.get("GALLERY_CACHE_DIR", os.path.expanduser("~/.cache/xhs_images"))


def to_rel_path(p: str) -> str:
    """绝对路径 → 缓存根下的**相对路径** `<key>/<file>`（DB 只存相对路径，不带根）。

    约定：DB 里不存根，读的时候按各部署自己的 GALLERY_CACHE_DIR 拼。
    """
    if not p:
        return p
    q = str(p).replace("\\", "/")
    marker = "/xhs_images/"
    if marker in q:
        return q.split(marker, 1)[1]
    # 兜底：按最后两段（<key>/<file>）
    parts = [x for x in q.split("/") if x]
    return "/".join(parts[-2:]) if len(parts) >= 2 else q


def resolve_image_path(p: str) -> str:
    """把 DB 里存的图片路径解析成**当前部署**下的真实绝对路径。

    约定（2026-10-18 起）：DB 存**相对路径** `<key>/<file>` → 直接拼当前 GALLERY_CACHE_DIR。
    历史兼容：老数据是绝对路径（含迁移过来的宿主/容器路径）→ 按 `<key>/<file>` 后缀重接根。
    存在的原路径原样返回。
    """
    if not p:
        return p
    q = str(p)
    if q.startswith(("http://", "https://")):        # 远程 URL → 原样（契约不变）
        return p
    if os.path.exists(q):
        return q
    root = os.path.expanduser(GALLERY_CACHE_DIR)
    if not os.path.isabs(q):                         # 相对路径：新约定（DB 只存 <key>/<file>）
        return os.path.join(root, q.lstrip("/"))
    return os.path.join(root, to_rel_path(q))        # 老绝对路径（迁移数据）：按后缀重接根

# 多关键词并行抓取数量（默认 3）
FETCH_PARALLEL = int(os.environ.get("FETCH_PARALLEL", "3"))

# 代理配置
# 代理地址统一在 scripts/.env 维护（HTTP_PROXY=http://127.0.0.1:20808）
# 其他脚本通过以下函数或变量获取，不要在代码里硬编码端口号

def _load_env():
    """加载 scripts/.env 到环境变量（仅补充未设置的）。"""
    env_file = Path(__file__).resolve().parent.parent / "scripts" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

_load_env()

USE_PROXY = int(os.environ.get("USE_PROXY", "0"))
# PROXY_URL 从 .env 的 HTTP_PROXY 读取，无硬编码默认值
PROXY_URL = (
    os.environ.get("PROXY_URL") or
    os.environ.get("HTTP_PROXY") or
    os.environ.get("http_proxy") or
    ""
)
NO_PROXY = os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or ""


_PROXY_STATE = {"ts": 0.0, "ok": None, "url": None}
PROXY_PORT_RANGE = range(20800, 20821)   # VeloceMac Mixed 端口漂移区间（见 ops/recover-host.sh）


def _tcp_ok(host: str, port: int, timeout: float = 0.35) -> bool:
    import socket
    try:
        socket.create_connection((host, port), timeout=timeout).close()
        return True
    except Exception:  # noqa: BLE001
        return False


def _discover_port(host: str):
    """当前配置端口不通时，在 20800-20820 里找活的那一个（生产靠 check_proxy 退直连，这里更进一步）。"""
    for port in PROXY_PORT_RANGE:
        if _tcp_ok(host, port):
            return port
    return None


def proxy_url() -> str:
    """当前应使用的代理 URL（含端口漂移自愈）；不可用时返回空串。

    结果缓存 60s：失败过就扫描一次漂移区间，找到了就用它并打印一条提示。
    """
    import time as _t
    from urllib.parse import urlparse as _up

    if not PROXY_URL:
        return ""
    now = _t.time()
    if _PROXY_STATE["ok"] is not None and now - _PROXY_STATE["ts"] < 60:
        return _PROXY_STATE["url"] or ""

    u = _up(PROXY_URL)
    host, port = u.hostname, (u.port or 80)
    if _tcp_ok(host, port):
        _PROXY_STATE.update(ts=now, ok=True, url=PROXY_URL)
        return PROXY_URL

    alt = _discover_port(host)
    if alt:
        url = f"{u.scheme}://{host}:{alt}"
        if alt != port:
            print(f"  ↻ 代理端口漂移：配置 {port} 不通，实际 {alt} → 自动切换")
        _PROXY_STATE.update(ts=now, ok=True, url=url)
        return url

    _PROXY_STATE.update(ts=now, ok=False, url=None)
    return ""


def proxy_alive(timeout: float = 2.0, ttl: float = 60.0) -> bool:
    """代理是否可用（含端口漂移自愈）。保留旧签名，内部走 proxy_url()。"""
    return bool(proxy_url())


def get_proxies() -> dict:
    """返回 requests 格式的代理字典；代理不可达时返回空字典（自动直连）。"""
    url = proxy_url()
    if not url:
        return {}
    return {"http": url, "https": url}


def get_socks_proxy() -> str:
    """返回 SOCKS5 代理 URL（供 Telegram/yt-dlp 等使用）。"""
    if not PROXY_URL:
        return ""
    url = PROXY_URL
    if url.startswith("http://"):
        url = "socks5h://" + url[len("http://"):]
    return url


def get_proxy_env() -> dict:
    """返回供 subprocess 使用的代理环境变量。"""
    if not PROXY_URL:
        return {}
    return {
        "HTTP_PROXY": PROXY_URL, "HTTPS_PROXY": PROXY_URL,
        "http_proxy": PROXY_URL, "https_proxy": PROXY_URL,
        "NO_PROXY": NO_PROXY, "no_proxy": NO_PROXY,
    }

# 封面图视觉评分开关（需 VISION_MODEL 支持图片输入，暂不可用）
VISION_ENABLED = False

# 代理断开时，对不需要代理的域名自动改走直连重试（requests 全局生效，见 config/net_fallback.py）
from config import net_fallback as _net_fallback
_net_fallback.install()
