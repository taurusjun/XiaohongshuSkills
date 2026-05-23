#!/usr/bin/env python3
"""metrics_collector.py — 每小时通过 CDP 拦截 creator API JSON 追加历史快照"""

import sys, os, json, time, logging
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime

logging.basicConfig(level=logging.INFO, format="%(asctime)s [metrics] %(message)s")
logger = logging.getLogger("metrics_collector")

CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))


def collect_all(dry_run: bool = False) -> dict:
    """CDP 拦截 creator 页面的 analyze/list JSON 响应，按 note_id 精确匹配"""
    import requests as _requests
    import websocket as _ws

    now_str = datetime.now().strftime("%Y-%m-%d %H:00")

    try:
        resp = _requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5)
        tabs = resp.json()
        ws_url = tabs[0].get("webSocketDebuggerUrl", "")
        if not ws_url:
            return {"error": "no_ws_url"}
    except Exception as e:
        return {"error": str(e)}

    ws = _ws.create_connection(ws_url, timeout=15)
    try:
        ws.send(json.dumps({"id": 1, "method": "Network.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {
            "url": "https://creator.xiaohongshu.com/statistics/data-analysis?source=official"
        }}))
        # Drain only the navigate response (id=3), not unsolicited network events
        _drain_until(ws, msg_id=3)

        if dry_run:
            ws.close()
            return {"dry_run": True}

        # Intercept analyze/list JSON response
        captured_body = ""
        request_ids = []
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
            ws.close()
            return {"error": "no_response"}

        data = json.loads(captured_body)
        notes = data.get("data", {}).get("note_infos", [])

        # Match against DB: prefer note_id, fallback to title
        import sqlite3
        from scripts.sqlite_db import DB_PATH

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        try:
            articles = conn.execute(
                "SELECT key, title, xhs_note_id FROM news WHERE publish_xhs=1 AND status='active'"
            ).fetchall()

            # Build lookup: note_id → key, title → key
            note_map = {}
            title_list = []
            for art in articles:
                if art["xhs_note_id"]:
                    note_map[art["xhs_note_id"]] = art["key"]
                title_list.append(art)

            collected = 0
            for n in notes:
                nid = n.get("id", "")
                key = None
                # 1. Exact match by note_id
                if nid and nid in note_map:
                    key = note_map[nid]
                else:
                    # 2. Fallback: title match
                    xhs_title = _normalize(n.get("title", ""))
                    best_score = 0
                    for art in title_list:
                        score = _title_score(xhs_title, _normalize(art["title"]))
                        if score > best_score and score >= 0.7:
                            best_score = score
                            key = art["key"]
                    # 3. If matched and article had no note_id, save it
                    if key and nid and best_score >= 0.85:
                        conn.execute("UPDATE news SET xhs_note_id=? WHERE key=?", (nid, key))
                        note_map[nid] = key

                if key:
                    _write_metrics(conn, key, n, now_str)
                    collected += 1

            conn.commit()
            logger.info(f"Collected: {collected} articles at {now_str}")
        except Exception as e:
            conn.rollback()
            logger.error(f"Collection failed: {e}")
            return {"error": str(e)}
        finally:
            conn.close()
    finally:
        ws.close()

    return {"collected": collected, "time": now_str}


def _write_metrics(conn, key, note, now_str):
    conn.execute(
        """INSERT OR REPLACE INTO metrics_history
           (news_key, collected_at, views, likes, saves, comments,
            shares, fans_gained, impression, click_rate, watch_time, danmaku)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
        (key, now_str,
         note.get("read_count", 0), note.get("like_count", 0),
         note.get("fav_count", 0), note.get("comment_count", 0),
         note.get("share_count", 0) or 0, note.get("increase_fans_count", 0) or 0,
         note.get("imp_count", 0), note.get("coverClickRate", 0) or 0,
         note.get("view_time_avg", 0) or 0, note.get("danmaku_count", 0) or 0),
    )
    conn.execute(
        """UPDATE news SET xhs_views=?, xhs_likes=?, xhs_saves=?, xhs_comments=?,
           xhs_shares=?, xhs_fans_gained=?, xhs_impression=?, xhs_click_rate=?,
           xhs_watch_time=?, xhs_danmaku=?, updated_at=datetime('now','localtime') WHERE key=?""",
        (note.get("read_count", 0), note.get("like_count", 0),
         note.get("fav_count", 0), note.get("comment_count", 0),
         note.get("share_count", 0) or 0, note.get("increase_fans_count", 0) or 0,
         note.get("imp_count", 0), note.get("coverClickRate", 0) or 0,
         note.get("view_time_avg", 0) or 0, note.get("danmaku_count", 0) or 0,
         key),
    )


def _normalize(s):
    return __import__('re').sub(r"[，。！？、\s「」『』【】（）\(\)\,\!\.\?\-—　\"\"]", "", str(s).lower())


def _drain_until(ws, msg_id, timeout=10):
    import websocket as _ws
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
            if json.loads(raw).get("id") == msg_id:
                return
        except Exception:
            continue

def _title_score(a, b):
    if a == b: return 1.0
    if a[:8] == b[:8]: return 0.9
    if a in b or b in a: return 0.85
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio()


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    result = collect_all(dry_run=args.dry_run)
    print(f"[metrics_collector] {result}")
