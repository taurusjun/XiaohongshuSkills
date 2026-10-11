#!/usr/bin/env python3
"""回溯补全 kana_autopromoted / kana_pending 的「来源」src（改造前入表的词条没有 src）。

按 词条/语境 在 news 的 rewritten_content / wechat_content / content_ja 里反查，命中即写回 src（全长 key）。
用法：python3 backfill_kana_src.py
"""
import json
import os
import sqlite3
import sys

DB = os.environ.get("SQLITE_PATH", "/Users/user/PG/xhs-storage/data/news_dev.db")
DATA = os.environ.get("XHS_DATA_DIR", "/Users/user/PG/xhs-storage/data")
FILES = [os.path.join(DATA, "kana_autopromoted.jsonl"), os.path.join(DATA, "kana_pending.jsonl")]
COLS = ("rewritten_content", "wechat_content", "content_ja")


def find_src(conn, term, ctx):
    probes = []
    if ctx:
        probes.append(ctx.strip()[:16])
    probes.append(term)
    for probe in probes:
        if not probe:
            continue
        for col in COLS:
            try:
                r = conn.execute(
                    f"SELECT key FROM news WHERE {col} LIKE ? ORDER BY created_at DESC LIMIT 1",
                    (f"%{probe}%",)).fetchone()
            except Exception:                                   # noqa: BLE001
                continue
            if r:
                return r[0]
    return ""


def main():
    conn = sqlite3.connect(DB)
    total = hit = 0
    for path in FILES:
        if not os.path.exists(path):
            continue
        rows, changed = [], 0
        for ln in open(path, encoding="utf-8"):
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:                                   # noqa: BLE001
                continue
            if not r.get("src"):
                total += 1
                src = find_src(conn, r.get("term", ""), r.get("ctx", ""))
                if src:
                    r["src"] = src
                    changed += 1
                    hit += 1
            rows.append(r)
        with open(path, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print(f"{path}: 写回 {changed} 条")
    print(f"共待补 {total}，命中 {hit}，未命中 {total - hit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
