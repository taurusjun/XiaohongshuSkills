#!/usr/bin/env python3
"""metrics_collector.py — 每小时通过 creator API 回收实发数据并追加历史快照"""

import sys, os, json, time, logging, re
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [metrics] %(message)s")
logger = logging.getLogger("metrics_collector")

CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))


def collect_all(dry_run: bool = False) -> dict:
    """
    通过 CDP Network 拦截 creator 页面的 analyze/list API 响应，
    提取最近 7 天笔记数据，匹配 DB 中已发布文章，写入 metrics_history。
    """
    import requests as _requests
    import websocket as _ws

    # 获取 Chrome tab
    try:
        resp = _requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5)
        tabs = resp.json()
        if not tabs:
            logger.warning("No Chrome tabs found")
            return {"error": "no_tabs"}
        ws_url = tabs[0].get("webSocketDebuggerUrl", "")
        if not ws_url:
            logger.warning("No WebSocket URL")
            return {"error": "no_ws_url"}
    except Exception as e:
        logger.warning(f"Cannot connect to Chrome: {e}")
        return {"error": str(e)}

    ws = _ws.create_connection(ws_url, timeout=15)
    collected = 0
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {
            "url": "https://creator.xiaohongshu.com/statistics/data-analysis?source=official"
        }}))
        ws.recv()

        # Intercept analyze/list response
        request_ids = []
        captured_body = ""
        deadline = time.time() + 25
        ws.settimeout(5)
        while time.time() < deadline:
            try:
                raw = ws.recv()
                msg = json.loads(raw)
                m = msg.get("method", "")
                pid = msg.get("params", {}).get("requestId", "")

                if m == "Network.responseReceived":
                    url = msg.get("params", {}).get("response", {}).get("url", "")
                    if "analyze/list" in url:
                        request_ids.append(pid)

                if m == "Network.loadingFinished" and pid in request_ids:
                    ws.send(json.dumps({"id": 200, "method": "Network.getResponseBody",
                                        "params": {"requestId": pid}}))
                    body_raw = json.loads(ws.recv())
                    body = body_raw.get("result", {}).get("body", "")
                    if body and '"note_infos"' in body:
                        captured_body = body
                        break
            except Exception:
                continue

        if not captured_body:
            logger.warning("Could not capture analyze/list response")
            return {"error": "no_response"}

        data = json.loads(captured_body)
        notes = data.get("data", {}).get("note_infos", [])

        if dry_run:
            return {"dry_run": True, "notes_found": len(notes)}

        # Match against DB by title
        from scripts.sqlite_db import _connect, record_metrics
        now_str = datetime.now().strftime("%Y-%m-%d %H:00")

        with _connect() as db:
            pub_articles = db.execute(
                "SELECT key, title FROM news WHERE publish_xhs=1 AND status='active'"
            ).fetchall()

        for n in notes:
            xhs_title = _normalize(n.get("title", ""))
            if not xhs_title:
                continue
            for art in pub_articles:
                art_title = _normalize(art["title"])
                if _titles_match(xhs_title, art_title):
                    if not dry_run:
                        record_metrics(
                            art["key"], now_str,
                            views=n.get("read_count", 0),
                            likes=n.get("like_count", 0),
                            saves=n.get("fav_count", 0),
                            comments=n.get("comment_count", 0),
                        )
                    collected += 1
                    break

        logger.info(f"Collected: {collected} articles at {now_str}")
    finally:
        ws.close()

    return {"collected": collected, "time": now_str}


def _normalize(s: str) -> str:
    return re.sub(r"[，。！？、\s「」『』【】（）\(\)\,\!\.\?\-—　]", "", str(s).lower())


def _titles_match(a: str, b: str) -> bool:
    """Fuzzy title match"""
    if a == b:
        return True
    if a[:8] == b[:8]:
        return True
    if a in b or b in a:
        return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() >= 0.85


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="XHS 实发数据回收器（每小时）")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    result = collect_all(dry_run=args.dry_run)
    print(f"[metrics_collector] {result}")
