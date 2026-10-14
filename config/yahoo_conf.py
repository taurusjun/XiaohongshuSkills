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


def resolve_image_path(p: str) -> str:
    """把 DB 里存的图片绝对路径，按**当前** GALLERY_CACHE_DIR 归一化。

    迁移场景：宿主写的 /Users/user/.cache/xhs_images/<key>/x.jpg 进容器后路径失效。
    这里取「/xhs_images/」之后的相对部分，重新拼到当前缓存目录（配置化）。
    存在的原路径原样返回；否则返回归一化后的候选（调用方再判存在）。
    """
    if not p:
        return p
    if os.path.exists(p):
        return p
    marker = "/xhs_images/"
    if marker in p:
        rel = p.split(marker, 1)[1]
        return os.path.join(os.path.expanduser(GALLERY_CACHE_DIR), rel)
    return p

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


def get_proxies() -> dict:
    """返回 requests 格式的代理字典，无代理时返回空字典。"""
    if not PROXY_URL:
        return {}
    return {"http": PROXY_URL, "https": PROXY_URL}


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
