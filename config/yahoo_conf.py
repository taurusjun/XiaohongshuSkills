"""非敏感配置中心 — Yahoo Pipeline 全局配置"""
import os

# 存储后端: "sqlite" | "notion"（默认 sqlite）
STORAGE_BACKEND = os.environ.get("STORAGE_BACKEND", "sqlite")

# 图集下载缓存目录
GALLERY_CACHE_DIR = os.environ.get("GALLERY_CACHE_DIR", os.path.expanduser("~/.cache/xhs_images"))

# 多关键词并行抓取数量（默认 3）
FETCH_PARALLEL = int(os.environ.get("FETCH_PARALLEL", "3"))
