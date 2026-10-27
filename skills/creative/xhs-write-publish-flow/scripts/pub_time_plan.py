#!/usr/bin/env python3
"""
待发布推荐 · 预发布时间生成器（写稿流程的最后一步）

为「待发布推荐」选出的 5 篇稿子生成**明天**的预发布时间：
固定时段 09:00 / 12:00 / 15:00 / 18:00 / 20:00（东京时间 JST），
每个时段随机前后偏移若干分钟（默认 ±8），**刻意避开整点**。

用法
----
  # 1) 只看时间计划（不碰 DB）
  python3 pub_time_plan.py

  # 2) 指定日期 / 偏移上限 / 随机种子（复现同一批偏移）
  python3 pub_time_plan.py --date 2026-10-09 --jitter 10 --seed 42

  # 3) 换时段（按顺序对应到 key）
  python3 pub_time_plan.py --slots 09:00,12:00,15:00,18:00,20:00

  # 4) 直接把预发布时间写进 DB（--keys 按顺序对应 5 个时段）
  python3 pub_time_plan.py --keys K1,K2,K3,K4,K5 --apply

  # 5) 只写 xhs_pub_time，不动 publish_xhs（默认 --apply 会同时设 publish_xhs=1）
  python3 pub_time_plan.py --keys ... --apply --no-publish-flag

规则
----
* 「明天」= 东京时区的明天（脚本内部固定 JST，与 cron 口径一致）。
* 偏移为区间 [-jitter, +jitter] 内的随机整数，**排除 0**；结果分钟绝不为 :00。
* 同一次调用内 5 个时段**各自独立随机**（不是统一偏移）。
* --apply 只做一件事：`UPDATE news SET xhs_pub_time=?, publish_xhs=1 WHERE key=?`
  （`--no-publish-flag` 则只设 xhs_pub_time）。**不触发发布**，
  不碰 publish_mode / publish_method / related_keys / rewritten_*。
"""

import argparse
import datetime as dt
import os
import random
import sqlite3
import sys

DEFAULT_DB = os.path.expanduser("~/PG/XiaohongshuSkills/data/news_dev.db")
DEFAULT_SLOTS = ["09:00", "12:00", "15:00", "18:00", "20:00"]
JST = dt.timezone(dt.timedelta(hours=9))


def parse_slots(raw):
    slots = []
    for s in raw.split(","):
        s = s.strip()
        if not s:
            continue
        hh, mm = s.split(":")
        slots.append((int(hh), int(mm)))
    if not slots:
        raise ValueError("empty --slots")
    return slots


def build_plan(day, slots, jitter, rng):
    """返回 [(slot_str, 'YYYY-MM-DD HH:MM')]"""
    plan = []
    for (hh, mm) in slots:
        # 随机偏移，排除 0，保证不落在整点
        while True:
            off = rng.randint(-jitter, jitter)
            if off != 0:
                break
        target = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=JST) \
            + dt.timedelta(minutes=off)
        # 双保险：分钟绝不能是 0
        if target.minute == 0:
            target += dt.timedelta(minutes=1 if off > 0 else -1)
        plan.append((f"{hh:02d}:{mm:02d}", target.strftime("%Y-%m-%d %H:%M")))
    return plan


def describe_keys(db, keys):
    """取每条 key 的标题/现状，用于打印和冲突检查。"""
    out = []
    conn = sqlite3.connect(db)
    try:
        for k in keys:
            row = conn.execute(
                "SELECT substr(title,1,34), COALESCE(xhs_pub_time,''), "
                "COALESCE(publish_xhs,0), "
                "CASE WHEN COALESCE(rewritten_content,'')='' THEN 0 ELSE 1 END "
                "FROM news WHERE key=?", (k,)).fetchone()
            out.append((k, row))
    finally:
        conn.close()
    return out


def apply_plan(db, keys, plan, set_publish_flag):
    conn = sqlite3.connect(db)
    ok, bad = [], []
    try:
        for k, (slot, ts) in zip(keys, plan):
            row = conn.execute(
                "SELECT key FROM news WHERE key=?", (k,)).fetchone()
            if not row:
                bad.append((k, "key 不存在"))
                continue
            if set_publish_flag:
                conn.execute(
                    "UPDATE news SET xhs_pub_time=?, publish_xhs=1 WHERE key=?",
                    (ts, k))
            else:
                conn.execute(
                    "UPDATE news SET xhs_pub_time=? WHERE key=?", (ts, k))
            ok.append((k, slot, ts))
        conn.commit()
        # 读回验证
        for k, slot, ts in ok:
            got = conn.execute(
                "SELECT COALESCE(xhs_pub_time,''), COALESCE(publish_xhs,0) "
                "FROM news WHERE key=?", (k,)).fetchone()
            status = "OK" if got[0] == ts else "MISMATCH"
            print(f"  [verify {status}] {k[:12]} xhs_pub_time={got[0]} "
                  f"publish_xhs={got[1]}")
    finally:
        conn.close()
    return ok, bad


def kick_gallery_download(keys, api_base="http://127.0.0.1:5000"):
    """oneway：标记待发布后踢一脚图集下载，不等待下载结果。

    先 GET /api/gallery-status/<key>，已有缓存(done)或正在下载(running)就跳过
    （服务端的 POST 端点本身不查缓存，重复 POST 会重下整本图集）；
    否则 POST 一次——服务端起后台线程后立即返回，串行发完就走，不轮询。
    """
    import json
    import urllib.request

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 绕开 http_proxy
    base = api_base.rstrip('/')
    fired, skipped = [], []
    for k in keys:
        try:
            with opener.open(f"{base}/api/gallery-status/{k}", timeout=3) as resp:
                st = json.loads(resp.read().decode() or '{}').get('status', '')
                # 未检测到图集链接=源站没有图集页，重试也不会变，直接跳过
                if st in ('running', 'done') or st.startswith('error: 未检测到图集链接'):
                    skipped.append((k, '已有缓存/正在下载' if st in ('running', 'done') else '源站无图集'))
                    continue
        except Exception:
            pass                       # status 查不到也照常触发，下载线程自己会报错
        try:
            req = urllib.request.Request(
                f"{base}/api/gallery-download/{k}",
                data=json.dumps({"gallery_url": ""}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with opener.open(req, timeout=3) as resp:
                body = json.loads(resp.read().decode() or "{}")
            if body.get("locked"):
                skipped.append((k, body.get("msg", "locked")))
            else:
                fired.append(k)
        except Exception as e:
            skipped.append((k, str(e)[:80]))
    print(f"\n[图集下载 · oneway] 已触发 {len(fired)} 条"
          + (f"，跳过 {len(skipped)} 条" if skipped else ""))
    for k, why in skipped:
        print(f"  skip {k[:12]} -> {why}")
    return fired, skipped


def main():
    ap = argparse.ArgumentParser(
        description="待发布推荐：生成明天的预发布时间（带随机偏移）")
    ap.add_argument("--date", default=None,
                    help="目标日期 YYYY-MM-DD，默认=东京时区的明天")
    ap.add_argument("--slots", default=",".join(DEFAULT_SLOTS),
                    help="时段，逗号分隔，默认 09:00,12:00,15:00,18:00,20:00")
    ap.add_argument("--jitter", type=int, default=8,
                    help="随机偏移分钟上限（±），默认 8")
    ap.add_argument("--seed", type=int, default=None,
                    help="随机种子（复现同一批偏移用）")
    ap.add_argument("--keys", default=None,
                    help="5 个完整 40 位 key，逗号分隔，按顺序对应时段")
    ap.add_argument("--apply", action="store_true",
                    help="把计划写进 DB（需配 --keys）")
    ap.add_argument("--no-publish-flag", action="store_true",
                    help="--apply 时只写 xhs_pub_time，不设 publish_xhs=1")
    ap.add_argument("--db", default=DEFAULT_DB, help="DB 路径")
    ap.add_argument("--no-gallery", action="store_true",
                    help="--apply 后不踢图集下载（默认会 oneway 触发）")
    ap.add_argument("--api-base", default="http://127.0.0.1:5000",
                    help="webapp 地址，默认 http://127.0.0.1:5000")
    args = ap.parse_args()

    now = dt.datetime.now(JST)
    if args.date:
        y, m, d = (int(x) for x in args.date.split("-"))
        day = dt.date(y, m, d)
    else:
        day = (now + dt.timedelta(days=1)).date()

    slots = parse_slots(args.slots)
    rng = random.Random(args.seed)
    plan = build_plan(day, slots, args.jitter, rng)

    print(f"[预发布时间计划] 日期 {day}（{'今天+1' if not args.date else args.date}） "
          f"偏移上限 ±{args.jitter}min  seed={args.seed}")
    print(f"{'时段':<8}{'预发布时间':<20}")
    for slot, ts in plan:
        print(f"{slot:<8}{ts:<20}")

    keys = [k.strip() for k in args.keys.split(",")] if args.keys else None

    if keys:
        if len(keys) != len(plan):
            print(f"\n[!] --keys 数量({len(keys)}) != 时段数量({len(plan)})",
                  file=sys.stderr)
            sys.exit(2)
        desc = describe_keys(args.db, keys)
        print("\n[待设稿件]")
        for k, row in desc:
            if row is None:
                print(f"  {k[:12]}  <key 不存在>")
                continue
            title, cur, px, has_rc = row
            flag = "" if has_rc else "  [!] rewritten_content 为空"
            print(f"  {k[:12]}  publish_xhs={px}  现有xhs_pub_time='{cur}'  "
                  f"{title}{flag}")

    if args.apply:
        if not keys:
            print("\n[!] --apply 需要 --keys", file=sys.stderr)
            sys.exit(2)
        print("\n[写入 DB]" + ("（同时设 publish_xhs=1）" if not args.no_publish_flag
                              else "（只写 xhs_pub_time）"))
        ok, bad = apply_plan(args.db, keys, plan,
                             not args.no_publish_flag)
        print(f"  成功 {len(ok)} 条 / 失败 {len(bad)} 条")
        if bad:
            for k, why in bad:
                print(f"  FAIL {k} -> {why}")
            sys.exit(1)
        print("  提醒：本脚本不触发发布（禁止 POST /api/trigger-publish）。")
        if not args.no_gallery and not args.no_publish_flag:
            kick_gallery_download(ok, args.api_base)
        else:
            print("\n[图集下载] 跳过（--no-gallery 或 --no-publish-flag）")


if __name__ == "__main__":
    main()
