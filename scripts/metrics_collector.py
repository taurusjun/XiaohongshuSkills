#!/usr/bin/env python3
"""metrics_collector.py — 每小时通过 creator 导出全量 Excel，追加历史快照"""

import sys, os, json, time, re, logging, glob
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
    import requests as _requests
    import websocket as _ws

    now_str = datetime.now().strftime("%Y-%m-%d %H:00")

    for f in glob.glob(f"{DOWNLOAD_DIR}/笔记列表明细表*.xlsx"):
        try: os.remove(f)
        except: pass

    # 每次创建新 tab，用完关闭
    tab_id = ""
    try:
        resp = _requests.put(f"http://{CDP_HOST}:{CDP_PORT}/json/new", timeout=5)
        new_tab = resp.json()
        tab_id = new_tab.get("id", "")
        ws_url = new_tab.get("webSocketDebuggerUrl", "")
    except Exception as e:
        return {"error": str(e)}

    ws = _ws.create_connection(ws_url, timeout=15)
    try:
        ws.send(json.dumps({"id": 1, "method": "Browser.setDownloadBehavior",
                            "params": {"behavior": "allow", "downloadPath": DOWNLOAD_DIR}}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 3, "method": "Page.navigate", "params": {
            "url": "https://creator.xiaohongshu.com/statistics/data-analysis?source=official"
        }}))
        for _ in range(5):
            try: ws.recv()
            except: break
        time.sleep(5)

        if dry_run:
            ws.close()
            return {"dry_run": True}

        # Click 导出数据
        ws.send(json.dumps({"id": 10, "method": "Runtime.evaluate", "params": {
            "expression": """(()=>{for(let el of document.querySelectorAll('*')){if((el.textContent||'').trim()==='导出数据'){el.click();return'ok'}}return'not found'})()""",
            "returnByValue": True,
        }}))
        _recv_until(ws, msg_id=10)

        logger.info("Waiting for download...")
        downloaded = False
        deadline = time.time() + 30
        ws.settimeout(5)
        while time.time() < deadline:
            try:
                raw = ws.recv()
                msg = json.loads(raw)
                if msg.get("method") == "Page.downloadProgress":
                    if msg.get("params", {}).get("state") == "completed":
                        downloaded = True
                        break
            except Exception:
                continue

        if not downloaded:
            ws.close()
            return {"error": "download_timeout"}

        time.sleep(2)
        files = sorted(glob.glob(f"{DOWNLOAD_DIR}/笔记列表明细表*.xlsx"), key=os.path.getmtime, reverse=True)
        if not files:
            ws.close()
            return {"error": "file_not_found"}

        excel_path = files[0]
        logger.info(f"Reading: {excel_path}")

        df = pd.read_excel(excel_path, skiprows=1)
        df.columns = ["title", "pub_time", "format", "impression", "views",
                      "click_rate", "likes", "comments", "saves", "fans",
                      "share", "watch_time", "danmaku"]

        for col in ["views", "likes", "comments", "saves"]:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)

        import sqlite3
        from scripts.sqlite_db import DB_PATH

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("BEGIN")
        try:
            articles = conn.execute(
                "SELECT key, title, xhs_title, xhs_note_id, rewritten_title, publish_mode FROM news WHERE publish_xhs=1 AND status='active'"
            ).fetchall()

            # Build Excel title index for fast lookup
            excel_titles = {}
            for _, row in df.iterrows():
                t = _normalize(str(row["title"]))
                if t:
                    excel_titles[t] = row

            collected = 0
            from difflib import SequenceMatcher
            for art in articles:
                match_row = None
                # 1. Match by xhs_title (exact or fuzzy)
                xhs_t = _normalize(art["xhs_title"] or "")
                if xhs_t and xhs_t in excel_titles:
                    match_row = excel_titles[xhs_t]
                elif xhs_t:
                    best = 0
                    for et, er in excel_titles.items():
                        s = SequenceMatcher(None, xhs_t, et).ratio()
                        if s > best and s >= 0.85:
                            best = s; match_row = er

                # 1.5: 改写模式 -> match by rewritten_title
                if match_row is None and art["publish_mode"] == 'rewritten':
                    rwt = _normalize(art["rewritten_title"] or "")
                    if rwt and rwt in excel_titles:
                        match_row = excel_titles[rwt]
                    elif rwt:
                        best = 0
                        for et, er in excel_titles.items():
                            s = SequenceMatcher(None, rwt, et).ratio()
                            if s > best and s >= 0.85:
                                best = s; match_row = er

                # 2. Fallback: match by DB title
                if match_row is None:
                    art_t = _normalize(art["title"])
                    if art_t in excel_titles:
                        match_row = excel_titles[art_t]
                    else:
                        best = 0
                        for et, er in excel_titles.items():
                            s = SequenceMatcher(None, art_t, et).ratio()
                            if s > best and s >= 0.7:
                                best = s; match_row = er

                if match_row is not None:
                    key = art["key"]
                    conn.execute(
                        """INSERT OR REPLACE INTO metrics_history
                           (news_key, collected_at, views, likes, saves, comments,
                            shares, fans_gained, impression, click_rate, watch_time, danmaku)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (key, now_str,
                         int(match_row["views"]), int(match_row["likes"]),
                         int(match_row["saves"]), int(match_row["comments"]),
                         int(match_row.get("share", 0) or 0), int(match_row.get("fans", 0) or 0),
                         int(match_row.get("impression", 0) or 0), float(match_row.get("click_rate", 0) or 0),
                         int(match_row.get("watch_time", 0) or 0), int(match_row.get("danmaku", 0) or 0)),
                    )
                    conn.execute(
                        """UPDATE news SET xhs_views=?, xhs_likes=?, xhs_saves=?, xhs_comments=?,
                           xhs_shares=?, xhs_fans_gained=?, xhs_impression=?, xhs_click_rate=?,
                           xhs_watch_time=?, xhs_danmaku=?, updated_at=datetime('now','localtime') WHERE key=?""",
                        (int(match_row["views"]), int(match_row["likes"]),
                         int(match_row["saves"]), int(match_row["comments"]),
                         int(match_row.get("share", 0) or 0), int(match_row.get("fans", 0) or 0),
                         int(match_row.get("impression", 0) or 0), float(match_row.get("click_rate", 0) or 0),
                         int(match_row.get("watch_time", 0) or 0), int(match_row.get("danmaku", 0) or 0),
                         key),
                    )
                    collected += 1

            conn.commit()
            logger.info(f"Collected: {collected} articles at {now_str}")
        except Exception as e:
            conn.rollback()
            logger.error(f"Failed: {e}")
            return {"error": str(e)}
        finally:
            conn.close()
    finally:
        ws.close()
        if tab_id:
            try: _requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json/close/{tab_id}", timeout=3)
            except: pass

    return {"collected": collected, "time": now_str}


def _normalize(s):
    return re.sub(r"[，。！？、\s「」『』【】（）\(\)\,\!\.\?\-—　\"\"]", "", str(s).lower())


def _recv_until(ws, msg_id, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            raw = ws.recv()
            if json.loads(raw).get("id") == msg_id:
                return
        except Exception:
            continue


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    result = collect_all(dry_run=args.dry_run)
    print(f"[metrics_collector] {result}")
