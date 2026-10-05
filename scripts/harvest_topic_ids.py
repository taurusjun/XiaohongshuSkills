#!/usr/bin/env python3
"""harvest_topic_ids.py — 批量采集「话题名 → 小红书话题 id」并写入 topic_cache

背景
----
小红书的话题搜索接口需要签名头（X-s / X-S-Common / X-t），裸 fetch 会被 406。
但实测：**同一套签名头可用于不同关键词**（签名按时间戳+会话算，与关键词无关），
所以整批采集只需敲一次字触发页面发请求、截获签名，之后全部走直接 fetch。

用法
----
    .venv/bin/python scripts/harvest_topic_ids.py                # 默认每 10 个停 3-5 秒
    .venv/bin/python scripts/harvest_topic_ids.py --every 20     # 每 20 个停一次（更快）
    .venv/bin/python scripts/harvest_topic_ids.py --limit 500    # 只处理 500 个

注意
----
- 可断点续跑：已在 topic_cache 里的标签自动跳过，中断后重跑不浪费
- 需要发布页有编辑器（用于采集签名）；无 VNC 也可以（connect 会唤醒页面渲染）
- 日志：/tmp/bulk_harvest.log（可用 tail -f 观察）
"""
import sys, os, json, time, random, sqlite3, argparse
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.getcwd())
sys.path.insert(0, os.path.join(os.getcwd(), "scripts"))

import publish_pipeline as pp
from cdp_publish import XiaohongshuPublisher
from config.yahoo_conf import DB_PATH
from sqlite_db import upsert_topic_id, get_topic_id

LOG = "/tmp/bulk_harvest.log"
# 断点位置（data/logs/ 已被 gitignore，不会污染工作区）
STATE = os.path.join(os.getcwd(), "data", "logs", "harvest_progress.json")


def load_progress():
    try:
        with open(STATE, encoding="utf-8") as f:
            return json.load(f).get("last_name")
    except Exception:
        return None


def save_progress(name):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w", encoding="utf-8") as f:
            json.dump({"last_name": name, "ts": time.strftime("%Y-%m-%d %H:%M:%S")}, f)
    except Exception:
        pass


def log(msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_all_tags():
    conn = sqlite3.connect(DB_PATH)
    raw = set()
    for (tags,) in conn.execute("SELECT tags FROM news WHERE tags IS NOT NULL AND tags != ''"):
        for t in tags.split(","):
            t = t.strip()
            if t:
                raw.add(t)
    conn.close()
    safe = sorted([t for t in raw if pp._SAFE_TAG_RE.match(t)])
    return raw, safe


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--every", type=int, default=10, help="每处理 N 个标签停顿一次（默认 10）")
    ap.add_argument("--delay-min", type=float, default=3.0)
    ap.add_argument("--delay-max", type=float, default=5.0)
    ap.add_argument("--limit", type=int, default=0, help="最多处理 N 个（0 = 全部）")
    ap.add_argument("--restart", action="store_true", help="忽略断点，从头开始")
    args = ap.parse_args()

    raw, safe = load_all_tags()
    log("库里标签 %d 个；含符号剔除 %d 个；待处理 %d 个" % (len(raw), len(raw) - len(safe), len(safe)))

    p = XiaohongshuPublisher()
    p.connect(target_url_prefix="https://creator.xiaohongshu.com", reuse_existing_tab=True)
    if not pp._activate_editor(p):
        log("!! 找不到编辑器（需要发布页已填好内容），退出")
        sys.exit(1)

    sig = None
    hit = miss = fail = 0
    consec_fail = 0
    processed = 0
    start = time.time()

    resume_after = None if args.restart else load_progress()
    if resume_after and resume_after not in set(safe):
        log("断点 %s 已不在标签列表中，从头开始" % resume_after)
        resume_after = None
    skipping = bool(resume_after)
    if skipping:
        log("从断点续跑（跳过 %s 之前的标签）" % resume_after)

    for i, name in enumerate(safe, 1):
        if skipping:
            if name == resume_after:
                skipping = False
            continue
        if get_topic_id(name):
            continue
        if args.limit and processed >= args.limit:
            log("达到 --limit %d，停止" % args.limit)
            break
        processed += 1
        got = False
        for attempt in (1, 2):
            if sig is None:
                sig = pp._capture_topic_signature(p, name)
                if sig is None:
                    log("!! 签名采集失败，退避 30s")
                    time.sleep(30)
                    continue
            topic, status = pp._fetch_topic_with_signature(p, sig, name, timeout_seconds=8.0)
            if status == "expired":
                sig = pp._capture_topic_signature(p, name)
                if sig:
                    topic, status = pp._fetch_topic_with_signature(p, sig, name, timeout_seconds=8.0)
            if status == "ok" and topic and topic.get("id"):
                upsert_topic_id(name, topic["id"], topic.get("link"), "bulk")
                hit += 1
                got = True
                consec_fail = 0
                break
            if status == "no_match":
                miss += 1
                got = True
                consec_fail = 0
                break
            if attempt == 1:
                time.sleep(1.0)
        if not got:
            fail += 1
            consec_fail += 1
            if consec_fail % 10 == 1:
                log("连续失败 %d 次，最近一个: %s" % (consec_fail, name))
            if consec_fail >= 30:
                log("!! 连续失败 30 次，疑似被限流，暂停 5 分钟后继续")
                time.sleep(300)
                consec_fail = 0
        save_progress(name)
        if processed % 50 == 0:
            log("进度 已处理 %d（遍历到 %d/%d）| 命中 %d 无匹配 %d 失败 %d | 已用 %.0f 分"
                % (processed, i, len(safe), hit, miss, fail, (time.time() - start) / 60))
        if args.every and i % args.every == 0:
            time.sleep(random.uniform(args.delay_min, args.delay_max))

    log("完成：已处理 %d，命中 %d，无匹配 %d，失败 %d" % (processed, hit, miss, fail))
    p.disconnect()


if __name__ == "__main__":
    main()
