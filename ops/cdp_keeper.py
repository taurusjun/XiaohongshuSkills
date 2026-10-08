#!/usr/bin/env python3
"""容器内 CDP keeper。
语义与原 ~/cdp-keeper.sh 一致：端口已开则直接返回；**绝不主动重启 9222**。
由 cdp-keeper(60s) 用 Xvfb + headed 先占住 9222，使发布链路传下来的 --headless 自动失效。
"""
import sys
import time

sys.path.insert(0, "/home/user/PG/XiaohongshuSkills")

from scripts.chrome_launcher import ensure_chrome, is_port_open, CDP_PORT  # noqa: E402

INTERVAL = 60

if __name__ == "__main__":
    print("[cdp_keeper] started", flush=True)
    while True:
        try:
            if is_port_open(CDP_PORT):
                pass
            else:
                print(f"[cdp_keeper] port {CDP_PORT} closed, launching headed Chrome...", flush=True)
                ensure_chrome(CDP_PORT, headless=False)
        except Exception as e:  # noqa: BLE001
            print(f"[cdp_keeper] error: {e}", flush=True)
        time.sleep(INTERVAL)
