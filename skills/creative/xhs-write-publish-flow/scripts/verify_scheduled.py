#!/usr/bin/env python3
"""排期落库验收器（出版推荐阶段最后一步的机械校验）

用法
----
  python3 verify_scheduled.py                 # 校验明天（JST）的排期
  python3 verify_scheduled.py --date 2026-11-06
  python3 verify_scheduled.py --keys K1,K2    # 只校验指定 key（12 位前缀或 40 位全键）

为什么需要它
------------
`pub_time_plan.py --apply` 返回 0 不等于万事大吉，实测踩到两种假成功：
  1) 排期根本没落库（cron 跑完稿子后工具轮次用尽，最后一步没执行）
  2) 落库了，但图集缓存里只有一张 og:image 回退单图（状态却是 done，真图集永远不补）
本脚本把「xhs_pub_time 非空 / publish_xhs=1 / 正文非空 / 图片张数」四项一次列全。

判读
----
  images=0  → 图集没下，先 POST /api/gallery-download/<key> 再看
  images=1  → 可疑：多半是 og:image 回退；除非 gallery-status 返回
              "error: 未检测到图集链接"（Yahoo 单图文），否则必须强制重下
  退出码 0 = 全部合格；1 = 有不合格项
"""

import argparse
import datetime as dt
import os
import sqlite3
import sys

DEFAULT_DB = os.path.expanduser("~/PG/XiaohongshuSkills/data/news_dev.db")
CACHE_DIR = os.path.expanduser("~/.cache/xhs_images")
JST = dt.timezone(dt.timedelta(hours=9))
MIN_IMAGES = 2          # 单图视为可疑，交给人判 gallery-status 再定


def image_count(key):
    d = os.path.join(CACHE_DIR, key)
    if not os.path.isdir(d):
        return 0
    return len([f for f in os.listdir(d)
                if f.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".mp4"))
                and not f.startswith(".") and not f.startswith("cover.")])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=(dt.datetime.now(JST) + dt.timedelta(days=1))
                    .strftime("%Y-%m-%d"), help="要校验的排期日期（默认 JST 明天）")
    ap.add_argument("--keys", default="", help="指定 key，逗号分隔（前缀或全键）")
    ap.add_argument("--db", default=DEFAULT_DB)
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    try:
        if args.keys:
            rows = []
            for k in [x.strip() for x in args.keys.split(',') if x.strip()]:
                rows += conn.execute(
                    "SELECT * FROM news WHERE key LIKE ? ORDER BY xhs_pub_time",
                    (k + '%',)).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM news WHERE xhs_pub_time LIKE ? ORDER BY xhs_pub_time",
                (args.date + '%',)).fetchall()
    finally:
        conn.close()

    if not rows:
        print(f"[FAIL] {args.date} 没有任何排期行 —— 排期那一步没有落库")
        return 1

    bad = []
    print(f"[排期验收] {args.date}  共 {len(rows)} 条")
    for r in rows:
        key = r["key"]
        n_img = image_count(key)
        body = r["rewritten_content"] or ""
        problems = []
        if not (r["xhs_pub_time"] or ""):
            problems.append("xhs_pub_time 空")
        if int(r["publish_xhs"] or 0) != 1:
            problems.append("publish_xhs!=1")
        if not body.strip():
            problems.append("正文空")
        if n_img < MIN_IMAGES:
            problems.append(f"图片 {n_img} 张（单图需查 gallery-status）")
        flag = "OK " if not problems else "WARN"
        if problems:
            bad.append((key, problems))
        print(f"  [{flag}] {key[:12]} {r['xhs_pub_time']} pub={r['publish_xhs']} "
              f"字={len(body)} 图={n_img} | {r['title'][:26]}")

    if bad:
        print("\n有问题的行：")
        for k, problems in bad:
            print(f"  {k[:12]} -> {'; '.join(problems)}")
        return 1
    print("\n全部合格（仍不要触发发布：POST /api/trigger-publish 一律禁止）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
