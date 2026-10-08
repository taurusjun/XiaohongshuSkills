"""待发布推荐 · 预发布时间生成器 —— 唯一实现（迁移自 pub_time_plan.py）。

「明天」= 东京时区明天；每个时段 [-jitter,+jitter] 随机偏移、排除 0、分钟绝不为 :00；
--apply 只 UPDATE news（xhs_pub_time / 可选 publish_xhs=1），**不触发发布**。
DB 路径走 services.paths.sqlite_path()。
"""
import argparse
import datetime as dt
import random
import sys

from services import paths

DEFAULT_SLOTS = ["09:00", "12:00", "15:00", "18:00", "20:00"]
JST = dt.timezone(dt.timedelta(hours=9))

__all__ = ["parse_slots", "build_plan", "describe_keys", "apply_plan",
           "default_db", "main"]


def default_db():
    return paths.sqlite_path()


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
        while True:
            off = rng.randint(-jitter, jitter)
            if off != 0:
                break
        target = dt.datetime(day.year, day.month, day.day, hh, mm, tzinfo=JST) \
            + dt.timedelta(minutes=off)
        if target.minute == 0:
            target += dt.timedelta(minutes=1 if off > 0 else -1)
        plan.append((f"{hh:02d}:{mm:02d}", target.strftime("%Y-%m-%d %H:%M")))
    return plan


def describe_keys(db, keys):
    import sqlite3
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
    import sqlite3
    conn = sqlite3.connect(db)
    ok, bad = [], []
    try:
        for k, (slot, ts) in zip(keys, plan):
            row = conn.execute("SELECT key FROM news WHERE key=?", (k,)).fetchone()
            if not row:
                bad.append((k, "key 不存在"))
                continue
            if set_publish_flag:
                conn.execute("UPDATE news SET xhs_pub_time=?, publish_xhs=1 WHERE key=?", (ts, k))
            else:
                conn.execute("UPDATE news SET xhs_pub_time=? WHERE key=?", (ts, k))
            ok.append((k, slot, ts))
        conn.commit()
        for k, slot, ts in ok:
            got = conn.execute(
                "SELECT COALESCE(xhs_pub_time,''), COALESCE(publish_xhs,0) FROM news WHERE key=?",
                (k,)).fetchone()
            status = "OK" if got[0] == ts else "MISMATCH"
            print(f"  [verify {status}] {k[:12]} xhs_pub_time={got[0]} publish_xhs={got[1]}")
    finally:
        conn.close()
    return ok, bad


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="待发布推荐：生成明天的预发布时间（带随机偏移）")
    ap.add_argument("--date", default=None, help="目标日期 YYYY-MM-DD，默认=东京时区的明天")
    ap.add_argument("--slots", default=",".join(DEFAULT_SLOTS), help="时段，逗号分隔")
    ap.add_argument("--jitter", type=int, default=8, help="随机偏移分钟上限（±），默认 8")
    ap.add_argument("--seed", type=int, default=None, help="随机种子")
    ap.add_argument("--keys", default=None, help="5 个完整 40 位 key，逗号分隔")
    ap.add_argument("--apply", action="store_true", help="把计划写进 DB（需配 --keys）")
    ap.add_argument("--no-publish-flag", action="store_true", help="--apply 时不设 publish_xhs=1")
    ap.add_argument("--db", default=None, help="DB 路径")
    args = ap.parse_args(argv)
    db = args.db or default_db()

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
            print(f"\n[!] --keys 数量({len(keys)}) != 时段数量({len(plan)})", file=sys.stderr)
            sys.exit(2)
        print("\n[待设稿件]")
        for k, row in describe_keys(db, keys):
            if row is None:
                print(f"  {k[:12]}  <key 不存在>")
                continue
            title, cur, px, has_rc = row
            flag = "" if has_rc else "  [!] rewritten_content 为空"
            print(f"  {k[:12]}  publish_xhs={px}  现有xhs_pub_time='{cur}'  {title}{flag}")

    if args.apply:
        if not keys:
            print("\n[!] --apply 需要 --keys", file=sys.stderr)
            sys.exit(2)
        print("\n[写入 DB]" + ("（同时设 publish_xhs=1）" if not args.no_publish_flag
                              else "（只写 xhs_pub_time）"))
        ok, bad = apply_plan(db, keys, plan, not args.no_publish_flag)
        print(f"  成功 {len(ok)} 条 / 失败 {len(bad)} 条")
        if bad:
            for k, why in bad:
                print(f"  FAIL {k} -> {why}")
            sys.exit(1)
        print("  提醒：本脚本不触发发布（禁止 POST /api/trigger-publish）。")
    return 0
