#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""待发布标题体检：缺人名 / 超20字 / 假名 / 日文新字体。

10/23 立的规矩：改写标题必须含主角名或团名（粉丝先认人再读钩子）。
本脚本是「清库前先看清现状」的探针，不改任何数据。

用法
----
    python3 scripts/audit_pending_titles.py                  # 真待发(publish_xhs=pending)
    python3 scripts/audit_pending_titles.py --mode marked    # 已标记但未进 pending 的历史稿
    python3 scripts/audit_pending_titles.py --mode all --limit 200

判据
----
FAIL  标题 > 20 字（CJK×1，其余×0.5，与 xhs_word_count.py 同一算法源）
FAIL  标题含日文新字体（発/沢/竜/広/芸/戦/髪/浜/歩/県/術…；嶋/壱/嶌 只在 WARN 组，人名保留）
FAIL  标题含假名
WARN  疑似缺人名：标题用「她/他/他们/她们」当主语，或 DB 标题的人名前缀没出现在标题里
WARN  待发稿 publish_mode=normal 但 rewritten_content 为空（会发 DB 标题原文）

注意：API 的 ?publish_xhs=1 / =0 **不是过滤器**（10/23 实测都返回全表 9047 条）。
真待发只能查 ?publish_xhs=pending。见 SKILL.md「API 的 publish_xhs=1 不是过滤器」。
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request

BASE = "http://127.0.0.1:5000/api/news"
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from xhs_word_count import xhs_title_len  # 单一算法源
except Exception:  # pragma: no cover
    def xhs_title_len(s):
        return sum(1 if "\u4e00" <= c <= "\u9fff" else 0.5 for c in s)

FAIL_SHINTAI = "発恵価歳竜徳沢辺栄広芸戦売買読選抜強実気対経済髪浜歩県術塩込"
WARN_SHINTAI = "嶋壱嶌"
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
PRONOUN = re.compile(r"(^|[，。：\s])(她|他|她们|他们)")
GROUP_PREFIX = re.compile(
    r"^(AKB48?|乃木坂46|樱坂46|榉坂46|日向坂46|Snow ?Man|timelesz|浪花男子|CUTIE STREET|"
    r"KEY TO LIT|KO1KEYZ|HiHi Jets|早安少女组)"
)


def api(url, tries=3):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            return json.load(urllib.request.urlopen(req, timeout=25))
        except Exception as e:  # 本地 API 偶发 reload 窗口，重试即可
            last = e
            time.sleep(1)
    raise last


def rows_for(mode, limit):
    q = {"all": None, "marked": "1", "published": "published"}.get(mode)
    if mode == "pending":
        url = f"{BASE}?publish_xhs=pending&limit={limit}"
    elif q:
        url = f"{BASE}?publish_xhs={urllib.parse.quote(q)}&limit={limit}"
    else:
        url = f"{BASE}?limit={limit}"
    return api(url).get("rows", [])


def name_candidates(db_title):
    core = GROUP_PREFIX.sub("", db_title or "")
    core = re.split(r"[，。：、！？\s]", core)[0]
    return core[:2], core[:3], core[:4]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="pending",
                    choices=["pending", "marked", "published", "all"])
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    rows = rows_for(args.mode, args.limit)
    print(f"mode={args.mode} 拉到 {len(rows)} 条\n")

    n_fail = n_warn = 0
    for r in rows:
        key = r["key"]
        try:
            j = api(f"{BASE}/{key}")
        except Exception as e:
            print(f"  !! {key[:12]} 详情读取失败: {e}")
            continue
        rt = (j.get("rewritten_title") or "").strip()
        mode = j.get("publish_mode") or ""
        pubt = rt if (rt and mode in ("rewritten", "free")) else (j.get("title") or "")
        rc = j.get("rewritten_content") or ""

        fails, warns = [], []
        tl = xhs_title_len(pubt)
        if tl > 20:
            fails.append(f"标题 {tl} 字 > 20")
        shin = sorted(set(re.findall(f"[{FAIL_SHINTAI}]", pubt)))
        if shin:
            fails.append(f"日文新字体 {shin}")
        warn_shin = sorted(set(re.findall(f"[{WARN_SHINTAI}]", pubt)))
        if warn_shin:
            warns.append(f"存疑异体字 {warn_shin}（人名可保留）")
        kn = KANA.findall(pubt)
        if kn:
            fails.append(f"假名 {len(kn)} 个")
        if PRONOUN.search(pubt):
            warns.append("疑似缺人名（她/他 当主语）")
        else:
            dbt = j.get("title") or ""
            cands = [c for c in name_candidates(dbt) if len(c) >= 2]
            if cands and not any(c in pubt for c in cands):
                warns.append(f"疑似缺人名（DB 名前缀 {'/'.join(cands)} 不在标题里）")
        if not rc and mode == "normal":
            warns.append("publish_mode=normal 且无改写正文（会发 DB 标题原文）")

        n_fail += bool(fails)
        n_warn += bool(warns)
        if fails or warns:
            flag = "FAIL" if fails else "WARN"
            print(f"{flag} {key[:12]} pub_xhs={j.get('publish_xhs')} mode={mode or '-'} | {pubt}")
            for f in fails:
                print(f"      ✗ {f}")
            for w in warns:
                print(f"      ! {w}")

    print(f"\n合计 {len(rows)} 条 | FAIL {n_fail} | WARN {n_warn}")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
