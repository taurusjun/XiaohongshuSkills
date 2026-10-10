"""硬规则 config 加载（写稿 skill 复刻：硬规则=config+服务，prompt 只留判断类）。"""
import functools
import json

from services import paths

__all__ = ["load", "thresholds", "xhs_th", "gzh_th"]


@functools.lru_cache(maxsize=None)
def load(name):
    p = paths.REPO_ROOT / "config" / name
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def thresholds():
    return load("review_thresholds.json")


def xhs_th():
    return thresholds().get("xhs", {})


def gzh_th():
    return thresholds().get("gzh", {})
