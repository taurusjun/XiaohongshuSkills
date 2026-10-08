"""批量入库 —— 唯一实现（迁移自 batch_import_drafts.py 模板，改为参数化）。

规则（与迁移前一致）：API PUT 优先，异常/非 ok 回落 sqlite3 UPDATE；
入库后按 rewritten_title 精确比对 + 正文长度>50 验证；gzh 稿走 wechat_ 字段、preselected=0。
"""
import argparse
import json
import os
import sqlite3
import sys

from services import paths
from services.draft_io import write_draft

__all__ = ["import_drafts", "main"]


def import_drafts(draft_dir, dump_path=None, rel_map=None, gzh_set=None,
                  db=None, api_base=None):
    rel_map = rel_map or {}
    gzh_set = set(gzh_set or [])
    db = db or paths.sqlite_path()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        km = {}
        if dump_path and os.path.exists(dump_path):
            with open(dump_path, encoding="utf-8") as f:
                km = {r["key"][:12]: r["key"] for r in json.load(f)["rows"]}

        def full(prefix):
            k = km.get(prefix)
            if k:
                return k
            r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (prefix,)).fetchone()
            return r["key"] if r else None

        ok, gzh_ok, fail = [], [], []
        for fn in sorted(os.listdir(draft_dir)):
            if not fn.endswith(".md") or not (fn.startswith("xhs_draft_") or fn.startswith("gzh_draft_")):
                continue
            p12 = fn.replace("xhs_draft_", "").replace("gzh_draft_", "").replace(".md", "")
            key = full(p12)
            if not key or len(key) != 40:
                fail.append((p12, "KEY_NOT_FOUND"))
                continue
            with open(os.path.join(draft_dir, fn), encoding="utf-8") as f:
                lines = f.read().strip().split("\n")
            title = lines[0].strip().lstrip("#").strip()
            body = "\n".join(lines[1:]).strip()

            if p12 in gzh_set:
                conn.execute("UPDATE news SET wechat_title=?, wechat_content=?, channel='gzh', "
                             "preselected=0, publish_xhs=0 WHERE key=?", (title, body, key))
                conn.commit()
                gzh_ok.append((p12, len(body)))
                continue

            rel = ",".join(filter(None, (full(x) for x in rel_map.get(p12, []))))
            good = False
            try:
                res = write_draft(key, title, body, api_base)
                good = isinstance(res, dict) and res.get("ok")
            except Exception:
                good = False
            if not good:
                try:
                    conn.execute("UPDATE news SET rewritten_title=?, rewritten_content=?, "
                                 "publish_mode='rewritten', preselected=1, publish_xhs=0, "
                                 "related_keys=? WHERE key=?", (title, body, rel, key))
                    conn.commit()
                except Exception as e2:
                    fail.append((p12, f"SQLITE_FAIL {e2}"))
                    continue

            chk = conn.execute("SELECT rewritten_title, length(rewritten_content) rl "
                               "FROM news WHERE key=?", (key,)).fetchone()
            if chk and chk["rewritten_title"] == title and chk["rl"] and chk["rl"] > 50:
                ok.append((p12, chk["rl"]))
            else:
                fail.append((p12, f"VERIFY_FAIL {dict(chk) if chk else None}"))
        return ok, gzh_ok, fail
    finally:
        conn.close()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="批量入库草稿")
    ap.add_argument("--dir", required=True, help="draft 目录")
    ap.add_argument("--dump", help="list dump json（keymap 用，可选）")
    ap.add_argument("--rel", help="REL map 的 json 文件（主key->前缀列表）")
    ap.add_argument("--gzh", default="", help="走公众号的 key 前缀，逗号分隔")
    ap.add_argument("--db", default=None)
    ap.add_argument("--api", default=None)
    args = ap.parse_args(argv)

    rel_map = {}
    if args.rel:
        with open(args.rel, encoding="utf-8") as f:
            rel_map = json.load(f)
    gzh_set = {x.strip() for x in args.gzh.split(",") if x.strip()}

    ok, gzh_ok, fail = import_drafts(args.dir, args.dump, rel_map, gzh_set, args.db, args.api)
    print(f"=== xhs OK {len(ok)} ===")
    for a, b in ok:
        print(f"  {a} len={b}")
    print(f"=== gzh OK {len(gzh_ok)} ===", gzh_ok)
    print(f"=== FAIL {len(fail)} ===")
    for a, b in fail:
        print(f"  {a} {b}")
    return 1 if fail else 0
