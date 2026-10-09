"""完整写稿 skill 运行（对齐 xhs-write-publish-flow 6 阶段）。

阶段1 写前准备(分级/关联) → 2 渠道路由(xhs/gzh) → 3 编写(体裁+字数路由/publish_method)
→ 4 入库+验证 → 5 xhs-content-review 5维评分+改稿 → 6 待发布推荐。
用法: python -m cli write-full [--n 3] [--deliver] [--dry-run]
"""
import json
import os
import re
import sys

from agent import llm
from services import (news as _news, paths, precheck as _pc, dunhao as _dh, kana as _kana,
                      format_route as _fr, routing as _route, renwei as _rw, gzh_review as _gz,
                      references as _refs)
from services.word_count import content_len  # noqa: E402

SKILL_FILE = "skills/creative/xhs-write-publish-flow/SKILL.md"
REVIEW_PROMPT = "skills/creative/xhs-write-publish-flow/reviews/chinese-review-prompt.md"
SYS = ('你是小红书日娱写稿助手。只输出严格 JSON 对象：'
       '{"key": "<40位key>", "channel": "xhs|gzh", "title": "...", "body": "...", '
       '"related": ["<关联素材key前缀12位>", ...]}。不要任何多余文字。')

__all__ = ["pick_candidates", "compose", "score_content", "write_one",
           "recommend_and_schedule", "run", "main"]


def _read(rel):
    p = paths.REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""




# ---------------- 阶段1：写前准备（分级 + 关联） ----------------


def pick_candidates(n=0):
    # ① DB grade(S/A) ② review 存档分级 ③ 分数兜底
    rows = _news.query_news(status="active", limit=300)
    base = [r for r in rows if (r.get("content_ja") or "")
            and r.get("format") in ("story", "news") and not (r.get("rewritten_content") or "")]
    # 选材：review 的 grade（S/A/AKB大TOP）；**同 cluster 只取一条**（其余合并/跳过）；无分级才分数兜底
    graded = [r for r in base if (r.get("grade") or "").upper() in ("S", "A", "AKB", "AKB大TOP")]
    pool = graded or base
    picks, used = [], set()
    for r in sorted(pool, key=lambda x: -(x.get("title_score") or 0)):
        cl = {x for x in (r.get("cluster_keys") or "").split(",") if x}
        if (cl | {r["key"]}) & used:
            continue
        picks.append(r)
        used |= cl | {r["key"]}
        if n and len(picks) >= n:
            break
    return picks


# ---------------- 阶段3：编写（体裁+字数路由） ----------------
def prepare_package(cand):
    """阶段1 写前准备（机械）：组装「写作包」— 全文原文 + 同事件关联 + 相关知识 + 体裁/字数路由。"""
    cj = cand.get("content_ja") or ""
    r = _fr.route(len(cj), cand.get("format"), cand.get("is_long_form"))
    from services.word_count import content_len
    j = content_len(cj) or 1
    if r["publish_method"] == "export":
        tmin, tmax = int(j * 0.31), int(j * 0.33)
    elif cand.get("format") == "story" and cand.get("is_long_form"):
        tmin, tmax = max(800, int(j * 0.30)), max(900, int(j * 0.34))
    else:
        tmin, tmax = int(j * 0.30), 900
    # 小红书图文正文硬上限 900（正文+标签等总限 1000，留足余量）；export 走 md 不受限
    if r["publish_method"] != "export":
        tmax = min(tmax, 900)
        tmin = min(tmin, tmax)
    target = f"{tmin}~{tmax}字（密度=正文字数/日文原文×100，须 ≥30%，低于 {tmin} 会被打回重写）"
    refs = related_text = ""
    try:
        refs = _refs.relevant(cand.get("title") or "")
    except Exception:
        pass
    # 关联（write 阶段决定并写 related_keys）：
    #   同事件 = review 的聚类计划 cluster_keys（可合并）
    #   同人物历史 = 写稿时按实体名检索
    merge_text = hist_text = ""
    related_keys = []
    try:
        ck = [k.strip() for k in (cand.get("cluster_keys") or "").split(",") if k.strip()]
        sibs = [x for x in (_news.get_by_key(k) for k in ck) if x]
        merge_text = "\n".join(
            f"[{s['key'][:12]}] {s.get('title')}｜原文节选：{(s.get('content_ja') or '')[:1200]}" for s in sibs)
        related_keys += ck
    except Exception:
        pass
    try:
        from agent import review as _rev
        from services import related as _rel
        ents = (_rev.extract_entities([{"key": cand["key"][:12], "title": cand.get("title") or "",
                                        "ja": (cand.get("title_ja") or "")[:60]}])
                .get(cand["key"][:12], []))
        hist = [h["key"] for h in _rel.find_related(cand["key"], ents, limit=4)
                if h["key"] not in related_keys]
        if hist:
            hs = [x for x in (_news.get_by_key(k) for k in hist) if x]
            hist_text = "\n".join(f"[{s['key'][:12]}] {s.get('title')}" for s in hs)
            related_keys += hist
    except Exception:
        pass
    related_keys = list(dict.fromkeys(related_keys))
    return {"content_ja": cj[:9000], "target": target, "tmin": tmin, "tmax": tmax, "refs": refs,
            "merge_text": merge_text, "hist_text": hist_text, "related_keys": related_keys,
            "cluster_keys": [k.strip() for k in (cand.get("cluster_keys") or "").split(",") if k.strip()],
            "method": r["publish_method"],
            "spec": {"fmt": cand.get("format"), "lf": cand.get("is_long_form"), "ja": len(cj)}}


def compose(cand, pkg, prev=None, retry_ctx=None, max_tokens=16000):
    """阶段3 撰写（LLM）：只吃写作包 pkg。prev=(title,body,channel) 时基于上一版修改。"""
    msgs = [{"role": "system", "content": SYS + "\n\n" + _read("agent/prompts/write.md")}]
    user = (f"素材 key={cand['key']} title={cand.get('title')} fmt={cand.get('format')} "
            f"lf={cand.get('is_long_form')}；长度要求：{pkg['target']}。\n"
            "判断走 xhs 还是 gzh（男团/男偶像的产业·厂牌·销量·战略·行业分析→gzh；粉丝向爆料/日常/综艺花絮→xhs）。\n"
            "全中文（假名≤5），行内「、」≤1，标题≤20字。（同事件素材可合并；同人物历史不并入。）\n\n"
            f"=== content_ja（原文全文）===\n{pkg['content_ja']}")
    if pkg["refs"]:
        user += "\n\n=== 相关规范/案例（节选）===\n" + pkg["refs"]
    if pkg.get("merge_text"):
        user += "\n\n=== 同事件关联（可合并：把这些素材的角度并入正文）===\n" + pkg["merge_text"]
    if pkg.get("hist_text"):
        user += "\n\n=== 同人物历史（仅供前情/避免重复，**不合并**）===\n" + pkg["hist_text"]
    msgs.append({"role": "user", "content": user})
    if prev and prev[0]:
        msgs.append({"role": "assistant", "content": json.dumps(
            {"key": cand["key"], "channel": prev[2], "title": prev[0], "body": prev[1]}, ensure_ascii=False)})
    if retry_ctx:
        msgs.append({"role": "user", "content": retry_ctx})
    raw = llm.chat(msgs, max_tokens=max_tokens)
    d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    return d.get("channel", "xhs"), d.get("title", ""), d.get("body", ""), d.get("related") or []



def score_content(title, body, max_tokens=4000):
    sysd = "你是中文内容评审。只输出严格 JSON：{\"爆发点\":n,\"情绪价值\":n,\"信息增量\":n,\"内容深度\":n,\"标题质量\":n,\"total\":n}（各0-2）。"
    usr = _read(REVIEW_PROMPT)[:3000] + f"\n\n=== 稿件 ===\n标题：{title}\n\n{body[:4000]}"
    try:
        raw = llm.chat([{"role": "system", "content": sysd}, {"role": "user", "content": usr}],
                       max_tokens=max_tokens)
        return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "total": 0}


# ---------------- 工具 ----------------
def score_gzh(title, body, max_tokens=4000):
    sysd = '你是公众号内容评审。只输出 JSON：{"标题吸引力":n,"叙事质量":n,"公众号适配度":n,"total":n}（各1-10，合格线7）。'
    usr = ("先做去魅测试：去掉所有日本专名后，文章是否仍有独立传播价值？\n\n"
           f"标题：{title}\n\n{body[:4000]}")
    try:
        raw = llm.chat([{"role": "system", "content": sysd}, {"role": "user", "content": usr}], max_tokens=max_tokens)
        return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "total": 0}


def _resolve_keys(prefixes):
    import sqlite3
    out = []
    conn = sqlite3.connect(paths.sqlite_path())
    try:
        for p in prefixes or []:
            r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (str(p)[:40],)).fetchone()
            if r:
                out.append(r[0])
    finally:
        conn.close()
    return ",".join(dict.fromkeys(out))


def _trim_title(title, limit=20):
    """标题按 xhs_title_len 机械改短到 <=limit。"""
    while title and _pc.title_len(title) > limit:
        title = title[:-1].rstrip()
    return title


def write_one(cand, dry_run=True):
    """阶段3~5：编写→门禁(precheck+renwei/gzh)→评分(按渠道)→改稿(最多3轮,基于上一版)。"""
    title = body = ""
    channel = "xhs"
    related = []
    mech = {"problems": ["未开始"]}
    score = {"total": 0}
    attempts = 0
    need = 8
    pkg = prepare_package(cand)                 # 阶段1：写前准备
    pmethod, spec = pkg["method"], pkg["spec"]

    while attempts < 3:
        attempts += 1
        ctx, prev = None, None
        if attempts > 1:
            fb = list(mech.get("problems") or [])
            if (score.get("total") or 0) < need:
                low = [k for k, v in score.items() if isinstance(v, (int, float)) and k != "total" and v < 2]
                fb.append(f"内容评分 {score.get('total')}/10 低于门槛 {need}，重点加强：{'、'.join(low) or '爆发点/情绪价值/信息增量'}")
            try:                                  # 残留假名 → 显式列出，要求替换
                res = _kana.new_terms(body)
                if res:
                    fb.append("残留假名必须替换为中文/罗马字（标题+正文都要）：" + "、".join(res[:8]))
            except Exception:
                pass
            fb = list(dict.fromkeys(fb))
            if body and len(body) < pkg["tmin"]:   # 字数不足 → **置顶**并要求扩写
                fb.insert(0, f"字数严重不足：正文仅 {len(body)} 字，**必须扩写到 ≥{pkg['tmin']} 字**"
                              f"（密度须 ≥30%，低于会被打回；补原文细节/背景，不要灌水）")
            ctx = ("基于上一版修改。**若字数不足，务必扩写到位**（其余问题只做局部修改）；"
                   "修正下列问题后重新只输出 JSON：\n- " + "\n- ".join(fb or ["提升钩子与情绪"]))
            prev = (title, body, channel)
        channel, title, body, related = compose(cand, pkg, prev=prev, retry_ctx=ctx)
        body, _ = _dh.fix_text(body)
        body = _kana.replace(body)
        title = _kana.replace(_trim_title(title))
        mech = _pc.check_text(f"## {title}\n{body}", spec)
        gate = list(mech["problems"]) + (_gz.check(title, body) if channel == "gzh" else _rw.check(body))
        mech["problems"] = gate
        need = 7 if channel == "gzh" else (8 if cand.get("format") == "story" else 6)
        if gate:
            continue
        score = score_gzh(title, body) if channel == "gzh" else score_content(title, body)
        if (score.get("total") or 0) >= need:
            break

    try:
        _kana.log_pending(body, note=cand["key"][:12])
    except Exception:
        pass
    ok = not mech["problems"] and (score.get("total") or 0) >= need
    rk = ",".join(pkg["related_keys"])         # write 阶段写 related_keys（同事件+同人物历史）
    if ok and not dry_run:
        if channel == "gzh":
            _news.update_news(cand["key"], {"wechat_title": title, "wechat_content": body,
                                            "channel": "gzh", "preselected": 0, "publish_xhs": 0,
                                            "related_keys": rk})
        else:
            _news.update_news(cand["key"], {"rewritten_title": title, "rewritten_content": body,
                                            "publish_mode": "rewritten", "publish_method": pmethod,
                                            "related_keys": rk, "preselected": 1, "publish_xhs": 0})
        # 关联素材回指主稿（先读当前 state 再合并，防止覆盖已有 related_keys）
        for sib in pkg.get("cluster_keys", []):
            if sib == cand["key"]:
                continue
            cur = _news.get_by_key(sib) or {}
            lst = [x for x in (cur.get("related_keys") or "").split(",") if x]
            if cand["key"] not in lst:
                lst.append(cand["key"])
            _news.update_news(sib, {"related_keys": ",".join(lst)})
        got = _news.get_by_key(cand["key"]) or {}
        ok = (got.get("wechat_title") == title and bool(got.get("wechat_content"))) if channel == "gzh" \
            else (got.get("rewritten_title") == title and len(got.get("rewritten_content") or "") > 50)
    return {"key": cand["key"][:12], "full_key": cand["key"], "ok": ok, "attempts": attempts,
            "channel": channel, "title": title, "text": body, "body": mech.get("body_len"),
            "h2": mech.get("h2"), "kana": mech.get("kana"), "method": pmethod,
            "score": score.get("total"), "related": rk, "problems": mech.get("problems") or []}



def recommend_and_schedule(n=5, seed=None):
    """阶段6：4 篇数据驱动（时效+人物历史+分数）+ 1 篇随机 + 2 备选；§0 总览；只排期不发布。"""
    import datetime as dt
    import random
    from services import schedule as _sch, recommend as _rec
    from agent import review as _rev
    today = dt.datetime.now(_sch.JST).date()
    tomorrow = today + dt.timedelta(days=1)
    pool = _rec.candidates(days=3)
    busy = _rec.tomorrow_busy(tomorrow)
    items = [{"key": r["key"][:12], "title": r.get("title") or "", "ja": (r.get("title_ja") or "")[:60]}
             for r in (pool + busy)]
    ents = _rev.extract_entities(items)                     # LLM 一次：候选+明天已排的实体
    busy_ents = [e for r in busy for e in (ents.get(r["key"][:12]) or [])]
    picks, backups, stat_by_key = _rec.stage6(pool, ents, busy_entities=busy_ents,
                                              n=n, seed=seed, today=today)
    rng = random.Random(seed)
    plan = _sch.build_plan(tomorrow, _sch.parse_slots(",".join(_sch.DEFAULT_SLOTS)), 8, rng)
    keys = [r["key"] for r in picks][:len(plan)]
    ok, bad = _sch.apply_plan(paths.sqlite_path(), keys, plan, True)
    reasons = _rec.reasons(picks, stat_by_key, today)
    overview = _rec.data_overview(today, pool)
    return picks, plan, bad, reasons, backups, overview


def run(n=0, deliver=False, dry_run=False):
    cands = pick_candidates(n)
    results = [write_one(c, dry_run=dry_run) for c in cands]
    for r in results:
        print(f"{'PASS' if r['ok'] else 'FAIL'} {r['key']} att={r['attempts']} ch={r['channel']} m={r['method']} "
              f"score={r['score']} body={r['body']} ##={r['h2']} kana={r['kana']} "
              f"related={r['related'][:24]}{'' if r['ok'] else ' | ' + '; '.join(r['problems'])}")
    passed = sum(1 for r in results if r["ok"])
    print(f"\n写稿通过 {passed}/{len(results)}（机械门禁+5维≥8）"
          f"（{'dry-run，未入库' if dry_run else '已入库'}）")

    if not dry_run:
        picks, plan, bad, reasons, backups, ov = recommend_and_schedule(5)
        gap = "无记录" if ov["gap_days"] is None else f"{ov['gap_days']}天"
        print(f"\n[数据总览] 断更 {gap} | 本月发布 " +
              " ".join(f"{d[-5:]}:{c}" for d, c in ov["perday"][:7]) +
              f" | 候选池 {ov['pool']} | 待发队列 {ov['pending']}")
        print("[待发布推荐]")
        for r, (slot, t), why in zip(picks, plan, reasons):
            print(f"  {r['key'][:12]} → {t}  《{r.get('rewritten_title') or r.get('title')}》  （{why}）")
        print("[备选补位] " + "; ".join(f"{b['key'][:12]} {b.get('title','')[:18]}" for b in backups))
        if bad:
            print("[!] 落库失败:", bad)
        # 待发布必跑：重新获取图集（原 skill：发布前 gallery-download）
        try:
            from services import gallery as _gal
            gres = _gal.sync(keys=[r["key"] for r in picks], timeout=180)
            print("[图集] " + "; ".join(f"{k[:12]}={st}" for k, st in gres))
        except Exception as e:  # noqa: BLE001
            print(f"[图集] 跳过: {e}")

    if deliver:
        from services.delivery import deliver as _d
        okr = [r for r in results if r["ok"]]          # 只交付达标稿
        if not okr:
            print("[delivery] 本次 0 篇达标，跳过飞书推送（无内容可审核）")
            return results
        lines = [f"# 写稿结果（达标入库 {len(okr)} 篇）", ""]
        for r in okr:
            lines += [f"## {r['title']}",
                      f"`{r['key']}` {r['channel']}（{r['method']}，{r['body']}字，评分{r['score']}/10）",
                      "", r["text"], "", "---", ""]
        if sched:
            lines += ["# 待发布推荐（明天 09/12/15/18/20）", ""]
            lines += [f"- `{x['key']}` **{x['time']}**  {x['title']}" for x in sched]
        if len(results) - len(okr):
            lines += ["", f"> 另有 {len(results)-len(okr)} 篇未达标（未入库，不列出）。"]
        print("[delivery]", _d("\n".join(lines), name="write-result.md"))
    return results


def main(argv=None):
    import argparse
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=0, help="最多写几篇；0=全部 S/A/AKB（默认）")
    ap.add_argument("--deliver", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    run(a.n, a.deliver, a.dry_run)
    return 0
