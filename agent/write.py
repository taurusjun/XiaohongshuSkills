"""完整写稿 skill 运行（对齐 xhs-write-publish-flow 6 阶段）。

阶段1 写前准备(分级/关联/体裁路由) → 2 渠道路由(xhs/gzh/both)
→ 3 编写 → 4 入库+验证 → 5 renwei(六类信号) + xhs 5维 / gzh 3维 + 改稿 → 6 待发布推荐。
用法: python -m cli write-full [--n 3] [--deliver] [--dry-run] [--split-large]
"""
import json
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
       '{"key": "<40位key>", "channel": "xhs|gzh|both", "title": "...", "body": "...", '
       '"gzh_title": "...", "gzh_body": "...", "related": ["<关联素材key前缀12位>", ...]}。'
       'channel=both 时 title/body 为 xhs 版、gzh_title/gzh_body 为公众号版；'
       'channel 非 both 时 gzh_title/gzh_body 留空。不要任何多余文字。')

__all__ = ["pick_candidates", "prepare_package", "compose", "score_content", "write_one",
           "recommend_and_schedule", "run", "main"]


def _read(rel):
    p = paths.REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


# ---------------- 阶段1：写前准备（分级 + 关联） ----------------


def pick_candidates(n=0):
    # ① DB grade(S/A) ② review 存档分级 ③ 分数兜底
    rows = _news.query_news(status="active", limit=300)
    base = [r for r in rows if (r.get("content_ja") or "")
            and r.get("format") in ("story", "news") and not (r.get("rewritten_content") or "")
            # AKB 晒照型 bullet 已处理（preselected=1 且无正文）→ 不再重跑
            and not ((r.get("akb_type") or "") == "bullet" and r.get("preselected"))]
    # 选材：review 的 grade（S/A/AKB大TOP）；**同 cluster 只取一条**（其余合并/跳过）；无分级才分数兜底
    graded = [r for r in base if (r.get("grade") or "").upper() in ("S", "A", "AKB", "AKB大TOP")]
    gzh_hint = [r for r in base if (r.get("channel_hint") or "").strip().lower() == "gzh"]
    pool = graded or base
    if gzh_hint:                      # review 标了 gzh 方向 → 也必须处理（旧 skill 阶段2）
        seen = {r["key"] for r in pool}
        pool = pool + [r for r in gzh_hint if r["key"] not in seen]
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
    """阶段1 写前准备（机械）：写作包 — 全文原文 + 同事件关联 + 相关知识 + 体裁/字数路由 + 机械渠道预判。"""
    cj = cand.get("content_ja") or ""
    r = _fr.route(len(cj), cand.get("format"), cand.get("is_long_form"))
    j = content_len(cj) or 1
    cap = 9000
    if r["publish_method"] == "export":                 # 超长文：喂更多原文（旧 dump_ja 全文口径）
        tmin, tmax = int(j * 0.31), int(j * 0.33)
        cap = 20000
    elif cand.get("format") == "story" and cand.get("is_long_form"):
        tmin, tmax = max(800, int(j * 0.30)), max(900, int(j * 0.34))
    else:
        tmin, tmax = int(j * 0.30), 900
    # 小红书图文正文硬上限 900（正文+标签等总限 1000，留足余量）；export 走 md 不受限
    if r["publish_method"] != "export":
        tmax = min(tmax, 900)
        tmin = min(tmin, tmax)
    target = f"{tmin}~{tmax}字（密度=正文字数/主素材日文原文×100，须 ≥30%，低于 {tmin} 会被打回重写）"
    # 渠道：review 标注的 gzh 方向 > 机械预判（旧 skill 阶段2「review 标了 gzh 方向的必须先处理」）
    hint = (cand.get("channel_hint") or "").strip().lower()
    pre_channel = "gzh" if hint == "gzh" else _route.route(cand.get("title") or "", cj[:500])
    refs = related_text = patterns = ""
    try:
        refs = _refs.relevant(f"{cand.get('title') or ''} {cand.get('title_ja') or ''}")
    except Exception:  # noqa: BLE001
        pass
    try:
        from services import feedback_patterns as _fbpat
        patterns = _fbpat.relevant(f"{cand.get('title') or ''} {cand.get('title_ja') or ''}")
    except Exception:  # noqa: BLE001
        pass
    # 关联（write 阶段决定并写 related_keys）：
    #   同事件 = review 的聚类计划 cluster_keys（可合并）
    #   同人物历史 = 写稿时按实体名(中日双形)检索
    merge_text = hist_text = ""
    related_keys = []
    try:
        ck = [k.strip() for k in (cand.get("cluster_keys") or "").split(",") if k.strip()]
        sibs = [x for x in (_news.get_by_key(k) for k in ck) if x]
        merge_text = "\n".join(
            f"[{s['key'][:12]}] {s.get('title')}｜原文节选：{(s.get('content_ja') or '')[:1200]}" for s in sibs)
        related_keys += ck
    except Exception:  # noqa: BLE001
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
    except Exception:  # noqa: BLE001
        pass
    related_keys = list(dict.fromkeys(related_keys))
    return {"content_ja": cj[:cap], "target": target, "tmin": tmin, "tmax": tmax, "refs": refs, "patterns": patterns,
            "merge_text": merge_text, "hist_text": hist_text, "related_keys": related_keys,
            "cluster_keys": [k.strip() for k in (cand.get("cluster_keys") or "").split(",") if k.strip()],
            "method": r["publish_method"], "pre_channel": pre_channel,
            "channel_hint": hint,
            "spec": {"fmt": cand.get("format"), "lf": cand.get("is_long_form"), "ja": len(cj)}}


def compose(cand, pkg, force_channel=None, prev=None, retry_ctx=None, max_tokens=16000):
    """阶段3 撰写（LLM）：只吃写作包 pkg。返回 dict（channel/title/body/gzh_*/related）。"""
    msgs = [{"role": "system", "content": SYS + "\n\n" + _read("agent/prompts/write.md")}]
    if force_channel:
        chan_note = f"渠道已指定：channel=\"{force_channel}\"（不要更改）。"
    else:
        chan_note = (f"机械预判渠道：{pkg.get('pre_channel', 'xhs')}（可覆盖；拿不准沿用它）。"
                     "若同一素材 xhs 与 gzh 都合适，可选 channel=\"both\" 并同时给 gzh_title/gzh_body。")
    user = (f"素材 key={cand['key']} title={cand.get('title')} fmt={cand.get('format')} "
            f"lf={cand.get('is_long_form')}；长度要求：{pkg['target']}。\n{chan_note}\n"
            "全中文（假名≤5），行内「、」≤1，标题≤20字。（同事件素材可合并；同人物历史不并入。related **只能**放「同事件关联」里的 key——「同人物历史」里的、以及只是同团/同人但事件无关的，**都不要**放。）\n\n"
            f"=== content_ja（原文全文）===\n{pkg['content_ja']}")
    if pkg["refs"]:
        user += "\n\n=== 相关规范/案例（节选）===\n" + pkg["refs"]
    if pkg.get("merge_text"):
        user += "\n\n=== 同事件关联（可合并：把这些素材的角度并入正文）===\n" + pkg["merge_text"]
    if pkg.get("hist_text"):
        user += "\n\n=== 同人物历史（仅供前情/避免重复，**不合并**）===\n" + pkg["hist_text"]
    if pkg.get("patterns"):
        user += ("\n\n=== 历史发布规律（往期已发布数据的复盘结论，供选题/标题/写法避坑；"
                 "来自 feedback_patterns 表）===\n" + pkg["patterns"])
    msgs.append({"role": "user", "content": user})
    if prev and prev[0]:
        msgs.append({"role": "assistant", "content": json.dumps(
            {"key": cand["key"], "channel": prev[2], "title": prev[0], "body": prev[1]}, ensure_ascii=False)})
    if retry_ctx:
        msgs.append({"role": "user", "content": retry_ctx})
    raw = llm.chat(msgs, max_tokens=max_tokens)
    try:
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:  # noqa: BLE001
        d = {}
    return d if isinstance(d, dict) else {}


def score_content(title, body, max_tokens=4000):
    sysd = "你是中文内容评审。只输出严格 JSON：{\"爆发点\":n,\"情绪价值\":n,\"信息增量\":n,\"内容深度\":n,\"标题质量\":n,\"total\":n}（各0-2）。"
    usr = _read(REVIEW_PROMPT)[:3000] + f"\n\n=== 稿件 ===\n标题：{title}\n\n{body[:4000]}"
    try:
        raw = llm.chat([{"role": "system", "content": sysd}, {"role": "user", "content": usr}],
                       max_tokens=max_tokens)
        return json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception as e:  # noqa: BLE001
        return {"error": str(e), "total": 0}


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


def _need(cand, channel):
    if channel == "gzh":
        return 7
    return 8 if cand.get("format") == "story" else 6


def _produce(cand, pkg, channel, first=None):
    """单渠道：编写→门禁(precheck+renwei exit1/gzh)→评分→改稿(最多3轮,基于上一版)。"""
    need = _need(cand, channel)
    title = body = ""
    mech = {"problems": ["未开始"], "body_len": 0, "h2": 0, "kana": 0}
    score = {"total": 0}
    attempts = 0
    prev = None
    llm_related = []
    while attempts < 3:
        attempts += 1
        ctx = None
        if attempts == 1 and first and first[0] is not None:
            title, body = first
        else:
            if attempts > 1:
                fb = list(mech.get("problems") or [])
                if (score.get("total") or 0) < need:
                    low = [k for k, v in score.items() if isinstance(v, (int, float)) and k != "total" and v < 2]
                    fb.append(f"内容评分 {score.get('total')}/10 低于门槛 {need}，重点加强：{'、'.join(low) or '爆发点/情绪价值/信息增量'}")
                try:
                    res = _kana.new_terms(body)
                    if res:
                        fb.append("残留假名必须替换为中文/罗马字（标题+正文都要）：" + "、".join(res[:8]))
                except Exception:  # noqa: BLE001
                    pass
                fb = list(dict.fromkeys(fb))
                if body and len(body) < pkg["tmin"]:
                    fb.insert(0, f"字数严重不足：正文仅 {len(body)} 字，**必须扩写到 ≥{pkg['tmin']} 字**"
                                  f"（密度须 ≥30%，低于会被打回；补原文细节/背景，不要灌水）")
                ctx = ("基于上一版修改。**若字数不足，务必扩写到位**（其余问题只做局部修改）；"
                       "修正下列问题后重新只输出 JSON：\n- " + "\n- ".join(fb or ["提升钩子与情绪"]))
                prev = (title, body, channel)
            d = compose(cand, pkg, force_channel=channel, prev=prev, retry_ctx=ctx)
            if d.get("related"):
                llm_related = d.get("related")
            title, body = d.get("title") or "", d.get("body") or ""
            # gzh：LLM 可能把正文放进 gzh_body（而非 body）→ 回退读取
            if not (body or "").strip() and (d.get("gzh_body") or "").strip():
                body = d.get("gzh_body")
                title = title or (d.get("gzh_title") or "")
        body, _ = _dh.fix_text(body)
        body = _kana.replace(body)
        title = _kana.replace(_trim_title(title))
        mech = _pc.check_text(f"## {title}\n{body}", pkg["spec"], channel)
        if not (body or "").strip():          # 正文为空 → block + 报错（写 error log）
            _msg = (f"write 正文为空（LLM 未产出正文）key={cand['key'][:12]} "
                    f"channel={channel} attempt={attempts}")
            print(f"    ❌ {_msg}")
            try:
                import sys as _sys
                _sys.path.insert(0, str(paths.REPO_ROOT / "scripts"))
                from sqlite_db import _log_db_error
                _log_db_error(_msg)
            except Exception:  # noqa: BLE001
                pass
            mech["problems"] = list(mech.get("problems") or []) + ["正文为空（LLM 未产出正文）"]
        gate = list(mech["problems"])
        try:
            rw = _rw.review(body)
            if rw["exit"] == 1:                        # exit1=拒绝(聚集信号)；exit2=警告(接受)
                gate += [f"renwei {p}" for p in rw["problems"]]
        except Exception:  # noqa: BLE001
            pass
        if channel == "gzh":
            gate += _gz.check(title, body)
        mech["problems"] = gate
        if gate:
            continue
        score = score_gzh(title, body) if channel == "gzh" else score_content(title, body)
        if (score.get("total") or 0) >= need:
            break
    ok = not mech["problems"] and (score.get("total") or 0) >= need
    try:
        _kana.log_pending(body, note=cand["key"][:12])
    except Exception:  # noqa: BLE001
        pass
    return {"channel": channel, "ok": ok, "attempts": attempts, "title": title, "text": body,
            "body": mech.get("body_len"), "h2": mech.get("h2"), "kana": mech.get("kana"),
            "score": score.get("total"), "problems": mech.get("problems") or [],
            "related": llm_related}


def write_one(cand, dry_run=True, force_channel=None):
    """阶段3~5：编写(可 both 双版本)→门禁→评分→改稿→入库+验证。force_channel 用于指定渠道。

    AKB大TOP 晒照型（review 标 akb_type=bullet）→ **不写正文**，只设 preselected=1/publish_xhs=0。
    """
    if (cand.get("akb_type") or "").strip().lower() == "bullet":
        if not dry_run:
            _news.update_news(cand["key"], {"preselected": 1, "publish_xhs": 0, "publish_mode": "normal"})
        return {"key": cand["key"][:12], "full_key": cand["key"], "ok": True, "bullet": True,
                "attempts": 0, "channel": "xhs", "title": "", "text": "", "body": 0, "h2": 0,
                "kana": 0, "method": "bullet", "score": 0, "related": "", "problems": [], "versions": []}
    pkg = prepare_package(cand)
    method = pkg["method"]
    eff = force_channel or ("gzh" if pkg.get("channel_hint") == "gzh" else None)  # 显式 > review 标注
    if eff:
        d0 = compose(cand, pkg, force_channel=eff)
        ch = eff
    else:
        d0 = compose(cand, pkg)
        ch = d0.get("channel") or pkg["pre_channel"]
        if ch not in ("xhs", "gzh", "both"):
            ch = pkg["pre_channel"]
    t0, b0 = d0.get("title") or "", d0.get("body") or ""
    versions = []
    if ch == "both":
        versions.append(_produce(cand, pkg, "xhs", first=(t0, b0)))
        gt, gb = d0.get("gzh_title") or "", d0.get("gzh_body") or ""
        versions.append(_produce(cand, pkg, "gzh", first=(gt, gb) if (gt and gb) else None))
    else:
        versions.append(_produce(cand, pkg, ch, first=(t0, b0)))
    # 关联：**只认「同事件」**（review 的 cluster_keys）；LLM 在其中复判，实体历史不进 related_keys
    cand_set = {k for k in pkg.get("cluster_keys", []) if k and k != cand["key"]}
    llm_pick = []
    for _v in versions:
        for _pfx in (_v.get("related") or []):
            for _fk in [x for x in _resolve_keys([_pfx]).split(",") if x]:
                if _fk in cand_set and _fk not in llm_pick:
                    llm_pick.append(_fk)
    rk_list = llm_pick if llm_pick else list(cand_set)
    rk_s = ",".join(rk_list)
    for v in versions:
        if not (v["ok"] and not dry_run):
            continue
        if v["channel"] == "gzh":
            _news.update_news(cand["key"], {"wechat_title": v["title"], "wechat_content": v["text"],
                                            "preselected": 0, "publish_xhs": 0,
                                            "related_keys": rk_s})
        else:
            _news.update_news(cand["key"], {"rewritten_title": v["title"], "rewritten_content": v["text"],
                                            "publish_mode": "rewritten", "publish_method": method,
                                            "related_keys": rk_s, "preselected": 1, "publish_xhs": 0})
        for sib in pkg.get("cluster_keys", []):        # 关联素材回指主稿
            if sib == cand["key"]:
                continue
            cur = _news.get_by_key(sib) or {}
            lst = [x for x in (cur.get("related_keys") or "").split(",") if x]
            if cand["key"] not in lst:
                lst.append(cand["key"])
            _news.update_news(sib, {"related_keys": ",".join(lst)})
        got = _news.get_by_key(cand["key"]) or {}
        v["ok"] = (got.get("wechat_title") == v["title"] and bool(got.get("wechat_content"))) \
            if v["channel"] == "gzh" else (got.get("rewritten_title") == v["title"]
                                           and len(got.get("rewritten_content") or "") > 50)
    # 写后按字段实际存在情况判定 channel（xhs=空 / gzh=gzh / 两版共存=both）——避免残留旧标记
    if not dry_run:
        got = _news.get_by_key(cand["key"]) or {}
        has_x = bool((got.get("rewritten_content") or "").strip())
        has_w = bool((got.get("wechat_content") or "").strip())
        chan = "both" if (has_x and has_w) else ("gzh" if has_w else "")
        upd = {}
        if (got.get("channel") or "") != chan:
            upd["channel"] = chan
        want_ps = 1 if has_x else 0          # xhs 版本存在 → 待发(1)；仅 gzh → 0
        if got.get("preselected") != want_ps:
            upd["preselected"] = want_ps
        if upd:
            _news.update_news(cand["key"], upd)
    prim = versions[0]
    return {"key": cand["key"][:12], "full_key": cand["key"], "ok": any(v["ok"] for v in versions),
            "attempts": max(v["attempts"] for v in versions), "channel": ch,
            "title": prim["title"], "text": prim["text"], "body": prim["body"], "h2": prim["h2"],
            "kana": prim["kana"], "method": method, "score": prim["score"], "related": rk_s,
            "problems": [p for v in versions for p in v["problems"]], "versions": versions}


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
    try:
        from services import feedback_patterns as _fbpat
        for i, r in enumerate(picks):
            pat = _fbpat.relevant(f"{r.get('rewritten_title') or ''} {r.get('title') or ''} {r.get('title_ja') or ''}", k=1)
            if pat:
                reasons[i] = f"{reasons[i]}｜历史规律 {pat.splitlines()[0][:50]}"
    except Exception:  # noqa: BLE001
        pass
    overview = _rec.data_overview(today, pool)
    return picks, plan, bad, reasons, backups, overview


def _split_candidates(cands):
    """关联素材体量过大 → 拆多篇（密度极限 fallback，默认关闭）。"""
    from services import split_write as _sp
    out = []
    for c in cands:
        if _sp.should_split(c):
            for g in _sp.split_groups(c):
                main = _news.get_by_key(g[0])
                if main:
                    out.append({**main, "cluster_keys": ",".join(g[1:])})
        else:
            out.append(c)
    return out


def run(n=0, deliver=False, dry_run=False, split_large=False, key=None, force_channel=None,
        verify_gallery=False):
    if key:
        import sqlite3
        conn = sqlite3.connect(paths.sqlite_path())
        try:
            r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (key[:40],)).fetchone()
        finally:
            conn.close()
        cands = [_news.get_by_key(r[0])] if r else []
        cands = [c for c in cands if c]
    else:
        cands = pick_candidates(n)
        if split_large:
            cands = _split_candidates(cands)
    results = [write_one(c, dry_run=dry_run, force_channel=force_channel) for c in cands]
    for r in results:
        if r.get("bullet"):
            print(f"BULLET {r['key']}（AKB 晒照型 → 仅入库不写正文）")
            continue
        for v in r["versions"]:
            print(f"{'PASS' if v['ok'] else 'FAIL'} {r['key']} [{v['channel']}] att={v['attempts']} "
                  f"m={r['method']} score={v['score']} body={v['body']} ##={v['h2']} kana={v['kana']} "
                  f"related={r['related'][:24]}{'' if v['ok'] else ' | ' + '; '.join(v['problems'])}")
    passed = sum(1 for r in results if r["ok"])
    print(f"\n写稿通过 {passed}/{len(results)}（机械门禁+renwei+评分）"
          f"（{'dry-run，未入库' if dry_run else '已入库'}）")

    if not dry_run and not key:          # 指定 key 重写时不重跑阶段6
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
        # 待发布必跑：对**整个待发布队列** one-way 触发图集下载（fire-and-forget，后读状态/日志确认）
        try:
            from services import gallery as _gal
            ks = _gal.pending_keys()
            gres = _gal.sync(keys=ks, wait=False)
            started = sum(1 for _, st in gres if st == "started")
            cached = sum(1 for _, st in gres if str(st).startswith("skip"))
            print(f"[图集] 待发布队列 {len(ks)} 篇：触发 {started} / 已有缓存 {cached}"
                  f"（one-way，稍后查 /api/gallery-status）")
            if verify_gallery:
                import time as _t
                _t.sleep(5)
                vres = _gal.sync(keys=ks, wait=True, timeout=120)
                print("[图集·verify] " + "; ".join(f"{k[:12]}={st}" for k, st in vres))
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
            for v in r["versions"]:
                if not v["ok"]:
                    continue
                lines += [f"## [{v['channel']}] {v['title']}",
                          f"`{r['key']}` （{r['method']}，{v['body']}字，评分{v['score']}/10）",
                          "", v["text"], "", "---", ""]
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
    ap.add_argument("--split-large", action="store_true", help="关联素材体量过大时拆多篇（默认关）")
    ap.add_argument("--key", default=None, help="只处理指定 key（前缀即可；用于重写/验证）")
    ap.add_argument("--force-channel", default=None, choices=["xhs", "gzh", "both"],
                    help="强制渠道（默认由 LLM 判断）")
    ap.add_argument("--verify-gallery", action="store_true",
                    help="图集 one-way 触发后再轮询一次确认（默认只触发不等）")
    a = ap.parse_args(argv)
    run(a.n, a.deliver, a.dry_run, a.split_large, a.key, a.force_channel, a.verify_gallery)
    return 0
