#!/usr/bin/env python3
"""容器内 CDP keeper。

- 端口已开则不动；**绝不主动重启 9222**（用 Xvfb+headed 占住端口，使发布链路的 --headless 失效）。
- 职责仅剩「关掉多余空白页」与「只剩空白页时导航到创作者首页」。
  2026-10-25：彻底移除 explore 相关函数与命名（用户明确不需要）。
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


def _clean_tabs():
    """关掉多余空白页；只剩空白页时导航到创作者首页。不处理 /explore。"""
    try:
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=5))
    except Exception:  # noqa: BLE001
        return
    dest = "https://creator.xiaohongshu.com/new/home"
    pages = [t for t in tabs if t.get("type") == "page"]
    blanks = [t for t in pages if (t.get("url") or "") in ("chrome://newtab/", "about:blank")]
    keep = 1 if len(pages) - len(blanks) == 0 else 0
    for t in blanks[keep:]:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/close/{t['id']}", timeout=5).read()
            print("[cdp_keeper] closed blank tab", flush=True)
        except Exception:  # noqa: BLE001
            pass
    # 关掉 www 的 /explore（视频流，持续烧 CPU；用户明确不需要）
    for t in pages:
        u = t.get("url") or ""
        if "www.xiaohongshu.com/explore" in u:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/close/{t['id']}", timeout=5).read()
                print("[cdp_keeper] closed explore tab", flush=True)
            except Exception:  # noqa: BLE001
                pass
    if keep and blanks:
        try:
            import websocket
            ws = websocket.create_connection(blanks[0]["webSocketDebuggerUrl"], timeout=5)
            ws.send(json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": dest}}))
            ws.recv(); ws.close()
            print(f"[cdp_keeper] blank -> {dest}", flush=True)
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    print("[cdp_keeper] started", flush=True)
    while True:
        try:
            if not is_port_open(CDP_PORT):
                print(f"[cdp_keeper] port {CDP_PORT} closed, launching headed Chrome...", flush=True)
                ensure_chrome(CDP_PORT, headless=False)
            _clean_tabs()
        except Exception as e:  # noqa: BLE001
            print(f"[cdp_keeper] error: {e}", flush=True)
        time.sleep(INTERVAL)
