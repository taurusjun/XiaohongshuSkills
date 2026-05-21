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
USE_PROXY = int(os.environ.get("USE_PROXY", "0"))
PROXY_URL = os.environ.get("PROXY_URL", "http://127.0.0.1:10090")

# 封面图视觉评分开关（需 VISION_MODEL 支持图片输入，暂不可用）
VISION_ENABLED = False
