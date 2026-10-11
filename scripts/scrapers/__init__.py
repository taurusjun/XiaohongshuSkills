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
    新开 tab 不复用已有 tab，取完一定关闭（含异常）。失败返回空字符串。"""
    import json as _json
    import time as _time
    try:
        import websocket
    except ImportError:
        print("  ⚠️ 需要安装: pip install websocket-client")
        return ""
    try:
        with cdp_new_tab(port=port, url=url) as tab:
            _time.sleep(wait)
            ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=15)
            try:
                ws.send(_json.dumps({"id": 1, "method": "Runtime.evaluate",
                                     "params": {"expression": "document.documentElement.outerHTML"}}))
                while True:
                    msg = _json.loads(ws.recv())
                    if msg.get("id") == 1:
                        return msg.get("result", {}).get("result", {}).get("value", "") or ""
            finally:
                ws.close()
    except Exception as e:
        print(f"  ⚠️ CDP 取页失败: {e}")
        try:
            from sqlite_db import _log_db_error
            _log_db_error(f"CDP 取页失败: {url}: {e}")
        except Exception:
            pass
        return ""


# ============ CDP 导航 + 等待（公共） ============
from contextlib import contextmanager as _contextmanager


@_contextmanager
def cdp_new_tab(host: str = "127.0.0.1", port: int = 9222, url: str = ""):
    """新开一个专用 tab，with 块结束时一定关闭（含异常、提前 return）。产出 tab 的 JSON
    （含 id、webSocketDebuggerUrl）。新建失败直接抛异常，由调用方按失败处理。
    注意 /json/close 是异步的（返回 'Target is closing'），关闭后短时间内 /json 里可能还看得到它。"""
    from urllib.parse import quote as _quote
    endpoint = f"http://{host}:{port}/json/new" + (f"?{_quote(url, safe='')}" if url else "")
    tab = requests.put(endpoint, timeout=10).json()
    try:
        if not tab.get("webSocketDebuggerUrl"):
            raise RuntimeError(f"新建 tab 缺少 webSocketDebuggerUrl: {tab}")
        yield tab
    finally:
        if tab.get("id"):
            try:
                requests.get(f"http://{host}:{port}/json/close/{tab['id']}", timeout=5)
            except Exception:
                pass

import itertools as _itertools
_CDP_IDS = _itertools.count(100000)  # 与调用方手写的小 id（1~9）错开


def cdp_eval(ws, expression: str, timeout: float = 10.0):
    """在 ws 对应页面执行 JS，返回值（returnByValue）。
    只认本次请求 id 的响应，途中的事件和旧请求的迟到响应都跳过。
    JS 抛异常返回 None；超过 timeout 抛 websocket.WebSocketTimeoutException。"""
    import json as _json
    import time as _time
    import websocket as _websocket
    mid = next(_CDP_IDS)
    ws.send(_json.dumps({"id": mid, "method": "Runtime.evaluate",
                         "params": {"expression": expression, "returnByValue": True}}))
    deadline = _time.time() + timeout
    old_timeout = ws.gettimeout()
    try:
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                raise _websocket.WebSocketTimeoutException("cdp_eval timed out")
            ws.settimeout(remaining)
            msg = _json.loads(ws.recv())
            if msg.get("id") != mid:
                continue
            res = msg.get("result", {})
            if "exceptionDetails" in res:
                return None
            return res.get("result", {}).get("value")
    finally:
        ws.settimeout(old_timeout)


def cdp_navigate_and_wait(ws, url: str, ready_js: str,
                          timeout: float = 20.0, poll: float = 0.3) -> str:
    """导航到 url，等待「本次导航产生的新文档」满足 ready_js。返回 'ok' | 'timeout' | 'nav_error'。
    - 导航前给旧文档打标记 window.__xhs_old_doc，只有新文档（无标记）满足 ready_js 才算 ok，
      避免复用 tab 时把 tab 里原来的页面误判为已就绪；
    - Page.navigate 返回 errorText，或落到 chrome-error:// 错误页 → 'nav_error'；
    - 到截止时间仍未就绪（含 Page.navigate 迟迟不返回、evaluate 超时）→ 'timeout'。
    不依赖 load / DOMContentLoaded 事件（广告等子资源卡住时 load 可能永不触发）。"""
    import json as _json
    import time as _time
    import websocket as _websocket
    deadline = _time.time() + timeout
    old_timeout = ws.gettimeout()
    try:
        try:
            cdp_eval(ws, "window.__xhs_old_doc = 1", timeout=min(5.0, timeout))
        except _websocket.WebSocketTimeoutException:
            pass  # 旧页面卡住打不上标记：仍要等 Page.navigate 响应（导航已提交）后再轮询

        mid = next(_CDP_IDS)
        ws.send(_json.dumps({"id": mid, "method": "Page.navigate", "params": {"url": url}}))
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                return "timeout"
            ws.settimeout(remaining)
            try:
                msg = _json.loads(ws.recv())
            except _websocket.WebSocketTimeoutException:
                return "timeout"
            if msg.get("id") == mid:
                if msg.get("error") or msg.get("result", {}).get("errorText"):
                    return "nav_error"
                break

        expr = ("location.href.startsWith('chrome-error://') ? 'ERR' : "
                "((!window.__xhs_old_doc) && (" + ready_js + ")) ? 'OK' : ''")
        while True:
            remaining = deadline - _time.time()
            if remaining <= 0:
                return "timeout"
            try:
                v = cdp_eval(ws, expr, timeout=min(5.0, remaining))
            except _websocket.WebSocketTimeoutException:
                v = None
            if v == "ERR":
                return "nav_error"
            if v == "OK":
                return "ok"
            _time.sleep(min(poll, max(0.0, deadline - _time.time())))
    finally:
        ws.settimeout(old_timeout)
