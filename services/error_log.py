"""统一错误日志：错误除主日志/终端外，**另写一份到 logs/error.log**（含时间戳，可选堆栈）。

用法：from services import error_log as elog; elog.log("…", tb=traceback.format_exc())
"""
import datetime
from pathlib import Path

_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"

__all__ = ["log", "path"]


def path():
    return _LOG_DIR / "error.log"


def log(msg, tb=""):
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_LOG_DIR / "error.log", "a", encoding="utf-8") as f:
            f.write(f"[{now}] {msg}\n")
            if tb:
                f.write(tb.rstrip("\n") + "\n")
            f.write("\n")
    except Exception:  # noqa: BLE001
        pass
