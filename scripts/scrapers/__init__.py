#!/usr/bin/env python3
"""图集下载共享工具 — 各站点 <site>_dl.py 的公共依赖"""

import requests
from pathlib import Path

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "ja-JP,ja;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

IMG_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Sec-Fetch-Dest": "image",
    "Sec-Fetch-Mode": "no-cors",
    "Sec-Fetch-Site": "same-site",
}


def download_images(urls: list[str], out_dir: Path, referer_url: str = "") -> int:
    """Download image URLs to out_dir. Returns count of successfully downloaded files."""
    out_dir.mkdir(parents=True, exist_ok=True)
    count = 0
    for idx, url in enumerate(urls):
        ext = url.rsplit(".", 1)[-1].split("?")[0]
        if ext not in ("jpg", "jpeg", "png", "webp", "gif"):
            ext = "jpg"
        dest = out_dir / f"{idx + 1:02d}.{ext}"
        hdrs = dict(IMG_HEADERS)
        if referer_url:
            hdrs["Referer"] = referer_url
        try:
            r = requests.get(url, headers=hdrs, timeout=30)
            r.raise_for_status()
            dest.write_bytes(r.content)
            count += 1
        except Exception:
            pass
    return count


def cdp_page_html(url: str, port: int = 9222, wait: float = 8.0) -> str:
    """用 CDP 真实 Chrome 拿渲染后的页面 HTML，用于拦截 requests 的站点（WAF 人机验证 / TLS 指纹）。
    新开 tab 不复用已有 tab，取完关闭。失败返回空字符串。"""
    import json as _json
    import time as _time
    try:
        import websocket
    except ImportError:
        print("  ⚠️ 需要安装: pip install websocket-client")
        return ""
    try:
        from urllib.parse import quote as _quote
        r = requests.put(f"http://127.0.0.1:{port}/json/new?{_quote(url, safe='')}", timeout=10)
        tab_id = r.json()["id"]
        _time.sleep(wait)
        tabs = requests.get(f"http://127.0.0.1:{port}/json", timeout=5).json()
        tab = next((t for t in tabs if t["id"] == tab_id), None)
        if not tab:
            return ""
        ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)
        ws.send(_json.dumps({"id": 1, "method": "Runtime.evaluate",
                             "params": {"expression": "document.documentElement.outerHTML"}}))
        html = ""
        while True:
            msg = _json.loads(ws.recv())
            if msg.get("id") == 1:
                html = msg.get("result", {}).get("result", {}).get("value", "") or ""
                break
        ws.close()
        try:
            requests.get(f"http://127.0.0.1:{port}/json/close/{tab_id}", timeout=5)
        except Exception:
            pass
        return html
    except Exception as e:
        print(f"  ⚠️ CDP 取页失败: {e}")
        return ""
