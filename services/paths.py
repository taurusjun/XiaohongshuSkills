"""路径/连接配置的统一来源（env 化，禁止散落硬编码）。"""
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def sqlite_path() -> str:
    return os.environ.get("SQLITE_PATH") or str(REPO_ROOT / "data" / "news_dev.db")


def api_base() -> str:
    return os.environ.get("XHS_API_BASE") or os.environ.get("XHS_WEBAPI_BASE") or "http://127.0.0.1:5000"


def workspace_dir() -> Path:
    return Path(os.environ.get("XHS_WORKSPACE") or (Path.home() / ".hermes" / "workspace"))


def profiles_base() -> str:
    return os.environ.get("XHS_PROFILES_BASE") or os.path.join(
        os.environ.get("LOCALAPPDATA", os.path.expanduser("~")),
        "Google", "Chrome", "XiaohongshuProfiles",
    )
