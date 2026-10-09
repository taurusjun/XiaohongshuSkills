#!/usr/bin/env python3
"""写稿 6 阶段对齐 —— 真实数据验证脚本（gap1-7）。

只用**真实 DB 数据**跑真实代码路径；仅在需要 LLM 的地方（compose/prepare_package 的
实体抽取）用桩函数，桩的返回值明确标注，不影响被验证的机械逻辑。

用法: .venv/bin/python ops/verify_write_alignment.py --date 2026-10-09
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import write as w, llm
from services import (news, references as refs, renwei, routing, split_write, format_route,
                      precheck, word_count)

# 桩：LLM 只用于「渠道判定/撰写/实体抽取」，本脚本验证的是机械逻辑
_orig = llm.chat


def _stub(msgs, **kw):
    s = msgs[-1]["content"] if isinstance(msgs, list) else ""
    if "实体抽取器" in (msgs[0]["content"] if isinstance(msgs, list) else ""):
        return "{}"
    return '{"channel":"xhs","title":"桩标题","body":"桩正文"}'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    a = ap.parse_args()
    date = a.date
    rows = news.query_news(date_from=date, date_to=date, status="active", limit=500)
    written = [r for r in rows if (r.get("rewritten_content") or "").strip()]
    print(f"# 写稿对齐真实验证 · {date} · 当日素材 {len(rows)}，已入库稿 {len(written)}")

    # ---- GAP2 拆多篇（真实 cluster 体量）----
    print("\n===== GAP2 关联素材体量过大→拆多篇（真实 cluster）=====")
    cl = [r for r in rows if (r.get("cluster_keys") or "").strip()]
    best, best_len = None, 0
    for r in cl:
        L = split_write.cluster_text_len(r)
        if L > best_len:
            best, best_len = r, L
    print(f"当日含 cluster 的行: {len(cl)}；最大 cluster 体量 = {best_len} 字（阈值 {split_write.DEFAULT_THRESHOLD}）")
    if best:
        print(f"  main={best['key'][:8]} should_split={split_write.should_split(best)}")
        print(f"  split_groups = {[[k[:8] for k in g] for g in split_write.split_groups(best)]}")
    # 展示阈值门禁在真实数据上的判定
    print(f"  → 本日>{split_write.DEFAULT_THRESHOLD}的 cluster 数: "
          f"{sum(1 for r in cl if split_write.should_split(r))}（>0 即 --split-large 会触发拆篇）")

    # ---- GAP3 机械渠道预判接入 ----
    print("\n===== GAP3 机械 gzh 预判接入 compose =====")
    for r in rows[:5]:
        print(f"  route('{ (r.get('title') or '')[:20] }') = {routing.route(r.get('title') or '', (r.get('content_ja') or '')[:500])}")
    llm.chat = _stub
    cap = {}
    def cap_chat(msgs, **kw):
        cap["msgs"] = msgs
        return _stub(msgs, **kw)
    llm.chat = cap_chat
    cand = rows[0]
    pkg = w.prepare_package(cand)
    w.compose(cand, pkg)
    umsg = cap["msgs"][1]["content"]
    gzh_hits = [(r["key"][:8], routing.route(r.get("title") or "", (r.get("content_ja") or "")[:500]), (r.get("title") or "")[:24])
                for r in rows if routing.route(r.get("title") or "", (r.get("content_ja") or "")[:500]) == "gzh"]
    print(f"  真实素材中 route=gzh 的示例: {gzh_hits[:3] or '（当日无，机制由 test_routing 覆盖）'}")
    print(f"  prepare_package.pre_channel = {pkg['pre_channel']}")
    print(f"  compose user 消息含「机械预判渠道」: {'机械预判渠道' in umsg}（值={pkg['pre_channel']}）")
    llm.chat = _orig

    # ---- GAP4 renwei 六类信号（真实正文）----
    print("\n===== GAP4 renwei 六类信号（真实正文）=====")
    for r in written:
        body = r.get("rewritten_content") or ""
        rep = renwei.review(body)
        cats = {k: len(v) for k, v in rep["hits"].items()}
        print(f"  {r['key'][:8]} exit={rep['exit']} 命中={sum(cats.values())} 类别={cats or '{}'}")
        if rep["problems"]:
            print(f"      e.g. {rep['problems'][0]}")

    # ---- GAP5 密度（分母=主素材）----
    print("\n===== GAP5 密度 = 正文/主素材content_ja（真实值）=====")
    for r in written:
        body, src = r.get("rewritten_content") or "", r.get("content_ja") or ""
        dens = word_count.content_len(body) / max(1, word_count.content_len(src)) * 100
        mech = precheck.check_text(f"## {r.get('rewritten_title') or ''}\n{body}",
                                   {"fmt": r.get("format"), "lf": r.get("is_long_form"), "ja": len(src)})
        print(f"  {r['key'][:8]} body={word_count.content_len(body)} src={word_count.content_len(src)} "
              f"density={dens:.1f}% mech_density={mech['density']:.1f}% problems={len(mech['problems'])}")

    # ---- GAP6 references 命中度 ----
    print("\n===== GAP6 references 命中（真实标题）=====")
    for r in rows[:3]:
        txt = refs.relevant(f"{r.get('title') or ''} {r.get('title_ja') or ''}")
        names = [l[1:l.index(']')] for l in txt.split("\n") if l.startswith("[")]
        print(f"  {(r.get('title') or '')[:16]} → {[n.split('/')[-1] for n in names]}")
        print(f"      ai-taste-checklist 命中={any('ai-taste' in n for n in names)}")

    # ---- GAP7 content_ja cap（export vs 普通）----
    print("\n===== GAP7 content_ja 入料上限（export 20000 / 普通 9000）=====")
    llm.chat = _stub
    big = [r for r in rows if len(r.get("content_ja") or "") > 9000]
    show = big[:2] or [r for r in rows if format_route.route(len(r.get("content_ja") or ""), r.get("format"), r.get("is_long_form"))["publish_method"] == "export"][:2]
    for r in show:
        rr = format_route.route(len(r.get("content_ja") or ""), r.get("format"), r.get("is_long_form"))
        pkg = w.prepare_package(r)
        cap = 20000 if rr["publish_method"] == "export" else 9000
        print(f"  {r['key'][:8]} fmt={r.get('format')} method={rr['publish_method']} "
              f"src={len(r.get('content_ja') or '')} cap={cap} fed={len(pkg['content_ja'])}")
    print(f"  当日 content_ja>9000 的行数: {len(big)}（无则 cap 差异不触发；逻辑见 prepare_package）")
    llm.chat = _orig
    return 0


if __name__ == "__main__":
    sys.exit(main())
