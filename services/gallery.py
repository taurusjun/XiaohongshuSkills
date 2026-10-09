"""图集获取（**待发布必跑**）—— 复用 webapp 接口，不重复实现下载。

原 skill：发布前对 待发布队列(publish_xhs=pending) 跑一次 gallery-download。
底层：POST /api/gallery-download/<key>（留空则用 DB gallery_url / 从原文检测）
      → 轮询 GET /api/gallery-status/<key> 直到 done/error。
默认跳过已有缓存的（幂等）。
"""
import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request

from services import paths

__all__ = ["pending_keys", "trigger", "status", "sync", "main"]


def _base() -> str:
    return (os.environ.get("XHS_WEBAPI_BASE") or "http://127.0.0.1:5000").rstrip("/")


def pending_keys(db=None):
    """待发布队列：publish_xhs=1 且 publish_time 为空（按预发布时间排序）。"""
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        return [r[0] for r in conn.execute(
            "SELECT key FROM news WHERE COALESCE(publish_xhs,0)=1 AND COALESCE(publish_time,'')='' "
            "ORDER BY COALESCE(xhs_pub_time,'')")]
    finally:
        conn.close()


def _post(path, payload=None, timeout=15):
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(_base() + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or "{}")


def _get(path, timeout=10):
    with urllib.request.urlopen(_base() + path, timeout=timeout) as r:
        return json.loads(r.read() or "{}")


def trigger(key, gallery_url=""):
    return _post(f"/api/gallery-download/{key}", {"gallery_url": gallery_url or ""})


def status(key):
    return _get(f"/api/gallery-status/{key}")


def sync(keys=None, force=False, timeout=180, wait=True, poll=3):
    """对待发布队列逐篇触发图集下载并等待；返回 [(key, 状态)]。"""
    ks = list(keys) if keys else pending_keys()
    out = []
    for k in ks:
        try:
            if not force and status(k).get("status") == "done":
                out.append((k, "skip(已有缓存)"))
                continue
        except Exception:
            pass
        try:
            trigger(k)
        except Exception as e:  # noqa: BLE001
            out.append((k, f"trigger失败:{e}"))
            continue
        if not wait:
            out.append((k, "started"))
            continue
        deadline = time.time() + timeout
        st = "running"
        while time.time() < deadline:
            time.sleep(poll)
            try:
                st = status(k).get("status", "")
            except Exception:
                continue
            if st == "done" or str(st).startswith("error"):
                break
        out.append((k, st))
    return out


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="待发布图集获取（gallery-download）")
    ap.add_argument("--keys", default=None, help="指定 key（逗号分隔）；默认=待发布队列")
    ap.add_argument("--force", action="store_true", help="强制重下（含已有缓存）")
    ap.add_argument("--no-wait", action="store_true")
    ap.add_argument("--timeout", type=int, default=180)
    a = ap.parse_args(argv)
    keys = [k.strip() for k in a.keys.split(",")] if a.keys else None
    results = sync(keys=keys, force=a.force, timeout=a.timeout, wait=not a.no_wait)
    ok = 0
    for k, st in results:
        flag = "OK" if (st == "done" or str(st).startswith("skip")) else ("err" if str(st).startswith("error") else st)
        print(f"  [{flag}] {k[:12]} {st}")
        if st == "done" or str(st).startswith("skip"):
            ok += 1
    print(f"图集：{ok}/{len(results)} 完成")
    return 0 if ok == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
