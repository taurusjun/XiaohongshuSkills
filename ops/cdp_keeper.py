#!/usr/bin/env python3
"""容器内 CDP keeper。

- 端口已开则不动；**绝不主动重启 9222**（用 Xvfb+headed 占住端口，使发布链路的 --headless 失效）。
- 追加：若 www 标签停在 /explore（视频流，持续烧 CPU）→ 导航到该账户 profile 页
  （读 data/accounts_meta.json 的 profile_id；无则 about:blank）。只碰 explore，不动其他页。
"""
import json
import sys
import time
import urllib.request

sys.path.insert(0, "/home/user/PG/XiaohongshuSkills")

from scripts.chrome_launcher import ensure_chrome, is_port_open, CDP_PORT  # noqa: E402

INTERVAL = 60
META = "/home/user/PG/XiaohongshuSkills/data/accounts_meta.json"
CUR = "/home/user/PG/XiaohongshuSkills/tmp/current_account.txt"


def _dest_url():
    try:
        meta = json.load(open(META, encoding="utf-8"))
        cur = "default"
        try:
            cur = open(CUR, encoding="utf-8").read().strip() or "default"
        except Exception:
            pass
        pid = (meta.get(cur) or {}).get("profile_id") or ""
        if not pid:
            pid = next((v.get("profile_id") for v in meta.values() if v.get("profile_id")), "")
        if pid:
            return f"https://www.xiaohongshu.com/user/profile/{pid}"
    except Exception:  # noqa: BLE001
        pass
    return "about:blank"


def _de_explore():
    try:
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=5))
    except Exception:  # noqa: BLE001
        return
    dest = _dest_url()
    for t in tabs:
        if t.get("type") == "page" and "www.xiaohongshu.com/explore" in (t.get("url") or ""):
            try:
                import websocket
                ws = websocket.create_connection(t["webSocketDebuggerUrl"], timeout=5)
                ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": dest}}))
                ws.recv()
                ws.close()
                print(f"[cdp_keeper] explore -> {dest}", flush=True)
            except Exception as e:  # noqa: BLE001
                print(f"[cdp_keeper] explore navigate failed: {e}", flush=True)


if __name__ == "__main__":
    print("[cdp_keeper] started", flush=True)
    while True:
        try:
            if not is_port_open(CDP_PORT):
                print(f"[cdp_keeper] port {CDP_PORT} closed, launching headed Chrome...", flush=True)
                ensure_chrome(CDP_PORT, headless=False)
            _de_explore()
        except Exception as e:  # noqa: BLE001
            print(f"[cdp_keeper] error: {e}", flush=True)
        time.sleep(INTERVAL)
