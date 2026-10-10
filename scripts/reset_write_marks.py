#!/usr/bin/env python3
"""清除「写稿阶段」标记（保留「review 结果」），供强制重写。

**清除（写稿产物）**：rewritten_title/rewritten_content、wechat_title/wechat_content、
  related_keys、channel、publish_mode、publish_method、xhs_pub_time、publish_time、
  manual_review、manual_reason、score_dims（含 akb-top-bullet 标记）；
  并把 preselected/publish_xhs 归 0；同时清掉这些 key 的「写稿失败队列」记录。
**保留（review 结果）**：grade、grade_reason、cluster_keys、channel_hint、akb_type、
  title_score/content_score、title/content_ja 等。

用法：
  python scripts/reset_write_marks.py --date 2026-10-10            # 仅预览（默认）
  python scripts/reset_write_marks.py --date 2026-10-10 --apply    # 真正执行
  python scripts/reset_write_marks.py --keys k1,k2 --apply         # 指定 key
"""
import argparse
import datetime
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # 让 services 可导入

from services import paths  # noqa: E402

_TEXT_FIELDS = ["rewritten_title", "rewritten_content", "wechat_title", "wechat_content",
                "related_keys", "channel", "publish_mode", "publish_method",
                "xhs_pub_time", "publish_time", "manual_reason", "score_dims"]
_INT_FIELDS = {"preselected": 0, "publish_xhs": 0, "manual_review": 0}


def select_keys(conn, date=None, keys=None):
    if keys:
        out = []
        for k in keys:
            r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (k[:40],)).fetchone()
            if r:
                out.append(r[0])
        return out
    d = date or datetime.datetime.now().strftime("%Y-%m-%d")
    rows = conn.execute(
        "SELECT key FROM news WHERE status='active' AND created_at LIKE ? || '%' "
        "AND grade IN ('S','A','AKB','AKB大TOP') AND format IN ('story','news') "
        "AND IFNULL(content_ja,'')<>'' ORDER BY key", (d,)).fetchall()
    return [r[0] for r in rows]


def main(argv=None):
    ap = argparse.ArgumentParser(description="清除写稿标记（保留 review 结果）")
    ap.add_argument("--date", default=datetime.datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--keys", default="", help="逗号分隔的 key 前缀（给定时忽略 --date）")
    ap.add_argument("--apply", action="store_true", help="真正写库（默认仅预览）")
    a = ap.parse_args(argv)
    keys = [k.strip() for k in a.keys.split(",") if k.strip()]
    conn = sqlite3.connect(paths.sqlite_path())
    conn.row_factory = sqlite3.Row
    ks = select_keys(conn, a.date, keys or None)
    print(f"[reset-write-marks] 目标 {len(ks)} 篇（date={a.date}）{'[APPLY]' if a.apply else '[预览]'}")
    for k in ks:
        r = conn.execute("SELECT title,grade,cluster_keys,channel_hint,akb_type,"
                         "length(rewritten_content) rc,length(wechat_content) wc,related_keys "
                         "FROM news WHERE key=?", (k,)).fetchone()
        print(f"  {k[:12]} grade={r['grade']} rc={r['rc'] or 0} wc={r['wc'] or 0} "
              f"| 保留 cluster={bool((r['cluster_keys'] or '').strip())} hint={r['channel_hint'] or '-'} "
              f"akb={r['akb_type'] or '-'} | {(r['title'] or '')[:22]}")
    if not a.apply:
        print("（预览，未改动。加 --apply 执行）")
        return 0
    set_clause = ", ".join([f"{f}=''" for f in _TEXT_FIELDS] + [f"{f}=0" for f in _INT_FIELDS])
    n = 0
    for k in ks:
        conn.execute(f"UPDATE news SET {set_clause} WHERE key=?", (k,))
        n += 1
    conn.commit()
    cleared = 0
    try:
        for k in ks:
            cur = conn.execute("DELETE FROM write_failures WHERE key=?", (k,))
            cleared += cur.rowcount
    except sqlite3.OperationalError:
        pass
    conn.commit()
    try:
        import sys as _s
        _s.path.insert(0, str(paths.REPO_ROOT / "scripts"))
        from sqlite_db import _log_db_error  # noqa: F401  占位，确保脚本环境一致
    except Exception:
        pass
    print(f"[reset-write-marks] 已清除 {n} 篇写稿标记；失败队列移除 {cleared} 条。review 结果已保留。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
