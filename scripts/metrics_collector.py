#!/usr/bin/env python3
"""metrics_collector.py — 每小时通过 creator 导出下载全量数据并追加历史快照"""

import sys, os, json, time, io, re, logging, glob
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from datetime import datetime

import pandas as pd

logging.basicConfig(level=logging.INFO, format="%(asctime)s [metrics] %(message)s")
logger = logging.getLogger("metrics_collector")

CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
DOWNLOAD_DIR = "/tmp"


def collect_all(dry_run: bool = False) -> dict:
    """
    CDP 导航到 creator 数据看板，点击「导出数据」，等待 Excel 下载完成，
    读取全量笔记数据，匹配 DB 已发布文章，写入 metrics_history。
    """
    import requests as _requests
    import websocket as _ws

    now_str = datetime.now().strftime("%Y-%m-%d %H:00")

    # 清理旧下载文件
    for f in glob.glob(f"{DOWNLOAD_DIR}/笔记列表明细表*.xlsx"):
        try: os.remove(f)
        except: pass

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
        # Enable download dir
        ws.send(json.dumps({"id": 1, "method": "Browser.setDownloadBehavior",
                            "params": {"behavior": "allow", "downloadPath": DOWNLOAD_DIR}}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {
            "url": "https://creator.xiaohongshu.com/statistics/data-analysis?source=official"
        }}))
        # Drain navigate response
        for _ in range(5):
            try: ws.recv()
            except: break
        time.sleep(5)

        if dry_run:
            ws.close()
            return {"dry_run": True, "note": "would download and match"}

        # Click 导出数据
        ws.send(json.dumps({"id": 10, "method": "Runtime.evaluate", "params": {
            "expression": """
            (() => {
                for (let el of document.querySelectorAll('*')) {
                    if ((el.textContent||'').trim() === '导出数据') { el.click(); return 'ok'; }
                }
                return 'not found';
            })()
            """,
            "returnByValue": True,
        }}))
        _recv_until(ws, msg_id=10)

        # Wait for download to complete
        logger.info("Waiting for download...")
        downloaded = False
        deadline = time.time() + 30
        ws.settimeout(5)
        while time.time() < deadline:
            try:
                raw = ws.recv()
                msg = json.loads(raw)
                m = msg.get("method", "")
                if m == "Page.downloadProgress":
                    state = msg.get("params", {}).get("state", "")
                    if state == "completed":
                        downloaded = True
                        break
            except Exception:
                continue

        if not downloaded:
            logger.warning("Download did not complete")
            ws.close()
            return {"error": "download_timeout"}

        # Find downloaded file
        time.sleep(2)
        files = sorted(glob.glob(f"{DOWNLOAD_DIR}/笔记列表明细表*.xlsx"),
                       key=os.path.getmtime, reverse=True)
        if not files:
            logger.warning("Downloaded file not found")
            ws.close()
            return {"error": "file_not_found"}

        excel_path = files[0]
        logger.info(f"Reading: {excel_path}")

        # Parse Excel
        df = pd.read_excel(excel_path, skiprows=1)
        df.columns = ["title", "pub_time", "format", "impression", "views",
                      "click_rate", "likes", "comments", "saves", "fans",
                      "share", "watch_time", "danmaku"]

        for col in ["views", "likes", "comments", "saves"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        # Match against DB
        import sqlite3
        from scripts.sqlite_db import DB_PATH
        logger.info(f"DB_PATH: {DB_PATH}")

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        try:
            articles = conn.execute(
                "SELECT key, title FROM news WHERE publish_xhs=1 AND status='active'"
            ).fetchall()

            collected = 0
            for _, row in df.iterrows():
                xhs_title = _normalize(str(row["title"]))
                if not xhs_title:
                    continue
                for art in articles:
                    if _titles_match(xhs_title, _normalize(art["title"])):
                        key = art["key"]
                        conn.execute(
                            """INSERT OR REPLACE INTO metrics_history
                               (news_key, collected_at, views, likes, saves, comments,
                                shares, fans_gained, impression, click_rate, watch_time, danmaku)
                               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                            (key, now_str,
                             int(row["views"]), int(row["likes"]), int(row["saves"]), int(row["comments"]),
                             int(row.get("share", 0) or 0), int(row.get("fans", 0) or 0),
                             int(row.get("impression", 0) or 0), float(row.get("click_rate", 0) or 0),
                             int(row.get("watch_time", 0) or 0), int(row.get("danmaku", 0) or 0)),
                        )
                        conn.execute(
                            """UPDATE news SET xhs_views=?, xhs_likes=?, xhs_saves=?, xhs_comments=?,
                               xhs_shares=?, xhs_fans_gained=?, xhs_impression=?, xhs_click_rate=?,
                               xhs_watch_time=?, xhs_danmaku=?, updated_at=datetime('now','localtime') WHERE key=?""",
                            (int(row["views"]), int(row["likes"]), int(row["saves"]), int(row["comments"]),
                             int(row.get("share", 0) or 0), int(row.get("fans", 0) or 0),
                             int(row.get("impression", 0) or 0), float(row.get("click_rate", 0) or 0),
                             int(row.get("watch_time", 0) or 0), int(row.get("danmaku", 0) or 0),
                             key),
                        )
                        collected += 1
                        break
            conn.commit()
        finally:
            conn.close()

        logger.info(f"Collected: {collected} articles at {now_str}")
    except Exception as e:
        logger.error(f"Collection failed: {e}")
        return {"error": str(e)}
    finally:
        ws.close()

    return {"collected": collected, "time": now_str}


def _recv_until(ws, msg_id: int, timeout: float = 10):
    import websocket as _ws
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
            if json.loads(raw).get("id") == msg_id:
                return
        except Exception:
            continue


def _normalize(s: str) -> str:
    return re.sub(r"[，。！？、\s「」『』【】（）\(\)\,\!\.\?\-—　\"\"]", "", str(s).lower())


def _titles_match(a: str, b: str) -> bool:
    if a == b or a[:8] == b[:8]:
        return True
    if a in b or b in a:
        return True
    from difflib import SequenceMatcher
    return SequenceMatcher(None, a, b).ratio() >= 0.85


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    result = collect_all(dry_run=args.dry_run)
    print(f"[metrics_collector] {result}")
