#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""第4层发布数据回顾：全量分页拉取 + 字段体检 + 三段时间分层 + 异常清单。

用法:
    python3 layer4_aggregate.py                    # now = 本机当前时间
    python3 layer4_aggregate.py 2026-09-21 02:32   # 显式指定 now（cron 环境建议显式传 TZ=Asia/Tokyo 的时间）

为什么需要它（9/21 实测教训）:
  1. 只拉 3 页 offset=0/250/500 只得 750/762 条，漏掉的 11 条恰好是当天最新发布的一批，
     分层表 24-48h / <24h 会整段为空 —— 必须翻页到空页。
  2. 曝光字段是 `xhs_impression`（单数）。写成 `xhs_impressions` 只会静默得到 None。
  3. `xhs_pub_time` 有三种格式（空 / 10 字符纯日期 / T 分隔），只支持一种会把
     10 字符日期整批误判为「不可解析」。
  4. `xhs_ctr` 这个 key 在 API 返回里根本不存在，报告应写「CTR 数据缺位」。

本脚本只读不写：不调用 update.sh、不 PUT /api/news、不修改任何记录。
"""
import datetime
import json
import sys
import urllib.request
from collections import Counter, defaultdict

BASE = "http://127.0.0.1:5000/api/news"
LIMIT = 250
MAX_PAGES = 16


def fetch_all():
    """按 offset 步进拉全部 published，返回 (rows, api_published)。"""
    rows, api_published = {}, None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # 等价 curl --noproxy '*'
    for page in range(MAX_PAGES):
        off = page * LIMIT
        url = (f"{BASE}?publish_xhs=published&sort_by=xhs_pub_time"
               f"&sort_dir=DESC&limit={LIMIT}&offset={off}")
        req = urllib.request.Request(url, headers={"User-Agent": "hermes-layer4"})
        with opener.open(req, timeout=60) as resp:
            d = json.loads(resp.read().decode("utf-8"))
        batch = d.get("rows", [])
        if d.get("published") is not None:
            api_published = d["published"]
        print(f"  offset={off:>4}  rows={len(batch):>3}  api_published={api_published}")
        if not batch:
            break
        for r in batch:
            rows[r.get("id")] = r
        if len(batch) < LIMIT:
            break
    return list(rows.values()), api_published


def parse_dt(p):
    """兼容三种 xhs_pub_time 格式。"""
    if not p:
        return None
    p = str(p).strip()
    if len(p) >= 16:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
            try:
                return datetime.datetime.strptime(p[:16], fmt)
            except ValueError:
                pass
        return None
    if len(p) == 10:
        try:
            return datetime.datetime.strptime(p, "%Y-%m-%d")
        except ValueError:
            return None
    return None


def num(r, k):
    try:
        return int(r.get(k) or 0)
    except (TypeError, ValueError):
        return 0


def title_of(r):
    return (r.get("rewritten_title") or r.get("title") or "")[:42]


def main():
    if len(sys.argv) >= 3:
        now = datetime.datetime.strptime(sys.argv[1] + " " + sys.argv[2], "%Y-%m-%d %H:%M")
    else:
        now = datetime.datetime.now()
    print(f"now = {now:%Y-%m-%d %H:%M}")

    print("\n== 1. 全量分页拉取 ==")
    rows, api_published = fetch_all()
    print(f"unique = {len(rows)}   api_published = {api_published}")
    if api_published and len(rows) != api_published:
        print(f"  [warn] 差 {api_published - len(rows)} 条（pending/边界条）；"
              f"若差额较大先确认是否漏翻页")

    for r in rows:
        dt = parse_dt(r.get("xhs_pub_time"))
        r["_dt"] = dt
        r["_h"] = (now - dt).total_seconds() / 3600 if dt else None
        r["_inter"] = sum(num(r, k) for k in
                          ("xhs_likes", "xhs_comments", "xhs_saves", "xhs_shares"))

    print("\n== 2. 字段体检 ==")
    ctr_key = sum(1 for r in rows if "xhs_ctr" in r)
    print(f"xhs_ctr key 存在条数 = {ctr_key}"
          + ("   → 报告写「CTR 数据缺位」，用 xhs_click_rate 替代" if ctr_key == 0 else ""))
    print(f"xhs_click_rate 有值 = {sum(1 for r in rows if r.get('xhs_click_rate'))}/{len(rows)}")
    miss = [r["id"] for r in rows if "xhs_impression" not in r]
    if miss:
        print(f"  [warn] {len(miss)} 条无 xhs_impression 字段（注意是单数）: {miss[:5]}")

    print("\n== 3. 时间分层 ==")
    b = defaultdict(list)
    for r in rows:
        h = r["_h"]
        key = ("future" if (h is not None and h < 0) else
               "nopub" if h is None else
               "<24h" if h < 24 else
               "24-48h" if h < 48 else ">48h")
        b[key].append(r)
    for k in (">48h", "24-48h", "<24h", "future", "nopub"):
        print(f"  {k:>7} = {len(b[k])}")
    mx = max((r["_dt"] for r in rows if r["_dt"]), default=None)
    if mx:
        gap = (now - mx).total_seconds() / 3600
        print(f"  MAX_PUB = {mx:%Y-%m-%d %H:%M}  距 now {gap:.1f}h  → "
              + ("⚠️ 发布管道停摆告警（>48h 无新发布，写进趋势表说明列）" if gap > 48
                 else "发布管道未停摆"))
    if b["future"]:
        print("  [定时队列] 未来 pub_time，0 阅读属预期，单独一行说明，不可下「冷门」结论：")
        for r in b["future"]:
            print(f"    id={r['id']} {r.get('xhs_pub_time')} {title_of(r)}")

    print("\n== 4. publish_mode 分布 ==")
    for label, subset in (("all", rows), (">48h", b[">48h"])):
        c = Counter(r.get("publish_mode") or "none" for r in subset)
        agg = {}
        for m in c:
            vs = [num(r, "xhs_views") for r in subset if (r.get("publish_mode") or "none") == m]
            vs.sort()
            agg[m] = (sum(vs) // len(vs), vs[len(vs) // 2])
        print(f"  [{label}] " + "  ".join(
            f"{m}={n}(avg{agg[m][0]}/med{agg[m][1]})" for m, n in c.most_common()))

    print("\n== 5. >48h 阅读 Top10 / 最低5 ==")

    def line(r):
        return (f"  {r['id']} {r.get('xhs_pub_time')} {r['_h']:.1f}h "
                f"v={num(r,'xhs_views')} imp={num(r,'xhs_impression')} inter={r['_inter']} "
                f"cr={r.get('xhs_click_rate')} {r.get('publish_mode')} | {title_of(r)}")

    g = sorted(b[">48h"], key=lambda r: -num(r, "xhs_views"))
    for r in g[:10]:
        print(line(r))
    print("  --- lowest 5 ---")
    for r in g[-5:]:
        print(line(r))

    print("\n== 6. 零曝光清单 (views == 0) ==")
    z = sorted([r for r in rows if num(r, "xhs_views") == 0],
               key=lambda r: r["_dt"] or datetime.datetime(1900, 1, 1), reverse=True)
    print(f"  共 {len(z)} 条")
    for r in z:
        h = f"{r['_h']:.1f}h" if r["_h"] is not None else "nopub"
        print(f"    {r['id']} {r.get('xhs_pub_time')} {h} "
              f"imp={num(r,'xhs_impression')} {title_of(r)}")

    print("\n== 7. 口径异常 v>imp ==")
    hit = [r for r in rows if num(r, "xhs_views") > num(r, "xhs_impression")]
    if not hit:
        print("  无")
    for r in hit:
        print(f"  {r['id']} {r.get('xhs_pub_time')} v={num(r,'xhs_views')} "
              f"imp={num(r,'xhs_impression')} {title_of(r)}")

    print("\n== 8. 24-48h / <24h 原始读数（只记录不下结论）==")
    for k in ("24-48h", "<24h"):
        for r in sorted(b[k], key=lambda r: -r["_h"]):
            print(f"  [{k}] {line(r)}")


if __name__ == "__main__":
    main()
