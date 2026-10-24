#!/usr/bin/env python3
"""容器内 CDP keeper。

- 端口已开则不动；**绝不主动重启 9222**（用 Xvfb+headed 占住端口，使发布链路的 --headless 失效）。
- 2026-10-24：**不再处理 /explore**（用户明确不需要）；职责仅剩「关掉多余空白页」
  与「只剩空白页时导航到创作者首页」。
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
    """explore → **创作者后台首页**。

    ⚠️ 2026-10-24：原来导到「该账户 profile 页」，但 profile 页自带视频流、同样持续烧 CPU
    （容器 CPU 长期 16–60%）。改为创作者首页（数据后台，无视频流）。
    """
    return "https://creator.xiaohongshu.com/new/home"


def _de_explore():
    try:
        tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json", timeout=5))
    except Exception:  # noqa: BLE001
        return
    dest = _dest_url()

    # 关掉多余空白页（chrome://newtab/ · about:blank）；至少保留 1 个 page，
    # 若只剩空白页 → 把第一个导航到目标页（而不是留 newtab，视频/动画也烧 CPU）
    pages = [t for t in tabs if t.get("type") == "page"]
    blanks = [t for t in pages if (t.get("url") or "") in ("chrome://newtab/", "about:blank")]
    keep = 1 if len(pages) - len(blanks) == 0 else 0
    for t in blanks[keep:]:
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/close/{t['id']}", timeout=5).read()
            print("[cdp_keeper] closed blank tab", flush=True)
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
            _de_explore()
        except Exception as e:  # noqa: BLE001
            print(f"[cdp_keeper] error: {e}", flush=True)
        time.sleep(INTERVAL)
