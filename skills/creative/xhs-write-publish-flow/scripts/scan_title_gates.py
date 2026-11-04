#!/usr/bin/env python3
"""标题门禁回归扫描 —— 新增/收紧标题规则前后，先量化误伤面再决定 FAIL 还是 WARN。

为什么要这个脚本（10/31 教训）：
写稿流程里的「标题门禁」很容易做成一刀切。10/31 第一版空壳门禁写成
「末分句以『表情』结尾即 FAIL」，全库回归后发现会拦掉
「一句台词，两种表情」「被镜头抓到的表情」这类好标题 —— 属于 10/23
「标题必带人名」被推翻的同一类错误。收窄成「发/晒/回了+表情类名词」
后全库 2247 条命中 0，才敢开 FAIL。

纪律：任何新增标题门禁，上线前跑一次本脚本，记下 FAIL/WARN 条数并人工
抽查命中样本。FAIL 只要有一条误伤就先降 WARN 或收窄正则，不要上线后
靠用户在成品里发现。

用法：
    python3 scripts/scan_title_gates.py                  # 扫全库
    python3 scripts/scan_title_gates.py --limit 500      # 只扫最近 500 条
    python3 scripts/scan_title_gates.py --gate shell     # 只看空壳落点
    python3 scripts/scan_title_gates.py --gate pronoun   # 只看代词开头
    python3 scripts/scan_title_gates.py --gate length    # 只看超 20 字
    python3 scripts/scan_title_gates.py --gate jargon    # 只看圈内黑话
    python3 scripts/scan_title_gates.py --show 30        # 每类最多列 30 条样本

判据来源一律是 batch_precheck.py 里的常量/正则，本脚本不写第二份实现。
"""
import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from batch_precheck import (  # noqa: E402
    GROUP_ANCHOR_RE,
    GROUP_ANCHOR_WORDS,
    JARGON_TITLE,
    JP_LOANWORD_RE,
    SHELL_BARE,
    ABSTRACT_TAIL,
    SHELL_TAIL_WARN,
    SHELL_VERB_RE,
)
from xhs_word_count import xhs_title_len  # noqa: E402

API = os.environ.get("XHS_API_BASE", "http://127.0.0.1:5000")
PRONOUN_HEAD_RE = re.compile(r"^[「『（(\s]*(他们|她们|他|她|它)")


def fetch_rows(limit):
    """分页拉取 /api/news，返回带 rewritten_title 的行。"""
    rows, offset = [], 0
    page = 500
    while True:
        q = urllib.parse.urlencode(
            {"limit": page, "offset": offset, "sort_dir": "DESC"}
        )
        with urllib.request.urlopen(f"{API}/api/news?{q}", timeout=60) as r:
            d = json.loads(r.read().decode())
        batch = d.get("rows", [])
        rows += batch
        if len(batch) < page or len(rows) >= limit:
            break
        offset += page
    return [r for r in rows if (r.get("rewritten_title") or "").strip()][:limit]


def tail_of(title):
    """末分句（按逗号/分号/冒号切），去掉尾部标点。与 batch_precheck 同口径。"""
    clauses = [c.strip() for c in re.split(r"[，,；;：:]", title) if c.strip()]
    tail = clauses[-1] if clauses else title
    return tail.strip(" 　。！？!?…~～」』）)】")


def has_anchor(title):
    """只有标题本身可用时的锚点判断（DB 回归没有 plan 的 name 字段）。"""
    return (
        "《" in title
        or bool(GROUP_ANCHOR_RE.search(title))
        or any(w in title for w in GROUP_ANCHOR_WORDS)
    )


def judge(title):
    """返回 (verdict, 原因)。verdict ∈ FAIL / WARN / PASS。"""
    tail = tail_of(title)
    if SHELL_VERB_RE.search(tail):
        return "FAIL", "空壳落点：证据动词+类别名词"
    if tail.endswith(SHELL_BARE):
        return "FAIL", "空壳落点：发文/发声无宾语"
    if PRONOUN_HEAD_RE.match(title):
        return "FAIL", "代词开头（零身份锚点）"
    if xhs_title_len(title) > 20:
        return "FAIL", f"超字数 {xhs_title_len(title)}"
    jarg = [w for w in JARGON_TITLE if w in title]
    if jarg and not has_anchor(title):
        return "FAIL", f"黑话无锚点（{'/'.join(jarg)}）"
    loan = JP_LOANWORD_RE.findall(title)
    if loan:
        return "FAIL", f"日文商品词（{'/'.join(sorted(set(loan)))}）"
    hit = next((w for w in SHELL_TAIL_WARN if tail.endswith(w)), None)
    if hit:
        return "WARN", f"泛反应词「{hit}」"
    hit_abs = next((w for w in ABSTRACT_TAIL if tail.endswith(w)), None)
    if hit_abs:
        return "WARN", f"抽象落点「{hit_abs}」"
    if len(jarg) >= 2:
        return "WARN", f"黑话≥2（{'/'.join(jarg)}）"
    return "PASS", ""


GATE_NAMES = {
    "shell": "空壳落点",
    "pronoun": "代词开头",
    "length": "超 20 字",
    "jargon": "圈内黑话",
    "loan": "日文商品词",
    "warn": "泛反应词",
    "concrete": "抽象落点",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=12000)
    ap.add_argument("--gate", choices=list(GATE_NAMES) + ["all"], default="all")
    ap.add_argument("--show", type=int, default=15, help="每类最多列几条样本")
    args = ap.parse_args()

    rows = fetch_rows(args.limit)
    buckets = {}
    for r in rows:
        verdict, why = judge(r["rewritten_title"].strip())
        if verdict == "PASS":
            continue
        key = (
            "shell" if "空壳" in why
            else "pronoun" if "代词" in why
            else "length" if "字数" in why
            else "jargon" if "黑话" in why
            else "loan" if "日文商品词" in why
            else "concrete" if "抽象" in why
            else "warn"
        )
        if args.gate != "all" and key != args.gate:
            continue
        buckets.setdefault((verdict, key, why), []).append(
            (r["key"][:12], r["rewritten_title"].strip(), r.get("publish_xhs"))
        )

    print(f"扫描 {len(rows)} 条 rewritten_title（gate={args.gate}）")
    if not buckets:
        print("✅ 零命中")
    for (verdict, key, why), items in sorted(buckets.items()):
        print(f"\n{verdict} [{GATE_NAMES.get(key, key)}] {why} → {len(items)} 条")
        for k, t, p in items[: args.show]:
            print(f"   {k}  pxhs={p}  {t}")
        if len(items) > args.show:
            print(f"   …另有 {len(items) - args.show} 条")

    fails = sum(len(v) for (verdict, _, _), v in buckets.items() if verdict == "FAIL")
    print(f"\n合计 FAIL {fails} 条。FAIL 有误伤样本 → 先降 WARN 或收窄条件再上线。")


if __name__ == "__main__":
    main()
