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

# 多关键词并行抓取数量（默认 3）
FETCH_PARALLEL = int(os.environ.get("FETCH_PARALLEL", "3"))

# 代理配置
# 代理地址统一在 scripts/.env 维护（HTTP_PROXY=http://127.0.0.1:20809）
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
