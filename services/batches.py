"""批次 manifest + 跨日漏写检测（硬规则=config+服务）。

每次写稿落 data/write_batches/<date>.json：本次处理的 keys + 结果 + 跳过清单(keys+reason)。
下次启动读最近 manifest：消费跳过清单（不重写纯重复/待人工），并对缺失 manifest 的近期日期告警（疑似漏写日）。
"""
import json
from datetime import datetime, timedelta

from services import paths, rules

__all__ = ["save", "load", "recent", "covered_keys", "skipped_map", "missed_days"]

_DIRNAME = "write_batches"


def _cfg():
    return rules.thresholds().get("batch", {})


def _dir():
    d = paths.REPO_ROOT / "data" / _DIRNAME
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(day):
    return _dir() / f"{day}.json"


def save(day, data):
    try:
        _path(day).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def load(day):
    p = _path(day)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def recent(days=None):
    days = days or _cfg().get("window_days", 3)
    keep = _cfg().get("manifest_keep", 14)
    today = datetime.now().date()
    out = [load(str(today - timedelta(days=i))) for i in range(days)]
    cutoff = today - timedelta(days=keep)
    for p in _dir().glob("*.json"):
        try:
            if datetime.strptime(p.stem, "%Y-%m-%d").date() < cutoff:
                p.unlink()
        except Exception:  # noqa: BLE001
            pass
    return out


def covered_keys(manifests):
    keys = set()
    for m in manifests:
        for r in m.get("processed", []):
            keys.add(r.get("key") if isinstance(r, dict) else r)
        for s in m.get("skipped", []):
            keys.add(s.get("key") if isinstance(s, dict) else s)
    return {k for k in keys if k}


def skipped_map(manifests):
    """key → reason（最近优先）。供 pick_candidates 消费跳过清单。"""
    out = {}
    for m in manifests:
        for s in m.get("skipped", []):
            if isinstance(s, dict) and s.get("key"):
                out.setdefault(s["key"], s.get("reason", ""))
    return out


def missed_days(active_days_exist, days=None):
    """给定『本应活跃的日期集合』，返回近期缺少 manifest 的日期（疑似漏写日）。"""
    days = days or _cfg().get("window_days", 3)
    today = datetime.now().date()
    miss = []
    for i in range(days):
        d = today - timedelta(days=i)
        if str(d) in active_days_exist and not _path(str(d)).exists():
            miss.append(str(d))
    return miss
