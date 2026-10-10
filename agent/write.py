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
                      references as _refs, content_gate as _gate, name_variants as _nv,
                      titles as _titles, batches as _batches, rules as _rules)
from services.word_count import content_len  # noqa: E402

SKILL_FILE = "skills/creative/xhs-write-publish-flow/SKILL.md"
REVIEW_PROMPT = "skills/creative/xhs-write-publish-flow/reviews/chinese-review-prompt.md"
SYS = ('你是小红书日娱写稿助手。只输出严格 JSON 对象：'
       '{"key": "<40位key>", "channel": "xhs|gzh|both", "title": "...", "body": "...", '
       '"gzh_title": "...", "gzh_body": "..."}。'
       'channel=both 时 title/body 为 xhs 版、gzh_title/gzh_body 为公众号版；'
       'channel 非 both 时 gzh_title/gzh_body 留空。不要任何多余文字。')

__all__ = ["pick_candidates", "prepare_package", "compose", "score_content", "write_one",
           "recommend_and_schedule", "run", "main"]


def _read(rel):
    p = paths.REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _log(msg):
    """单条处理明细日志（带时间戳，直接进日志文件）。"""
    import datetime as _dt
    print(f"[{_dt.datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


# ---------------- 阶段1：写前准备（分级 + 关联） ----------------


def pick_candidates(n=0):
    # ① DB grade(S/A) ② review 存档分级 ③ 分数兜底
    rows = _news.query_news(status="active", limit=300)
    base = [r for r in rows if (r.get("content_ja") or "")
            and r.get("format") in ("story", "news", "ranking", "comparison")
            and not (r.get("rewritten_content") or "")
            and not (r.get("wechat_content") or "")        # gzh-only 已写稿 → 不再重跑
            # AKB 晒照型 bullet 已处理（preselected=1 且无正文）→ 不再重跑
            and not ((r.get("akb_type") or "") == "bullet" and r.get("preselected"))]
    try:                                             # 消费跳过清单：纯重复不再重写
        _sk = _batches.skipped_map(_batches.recent())
        if _sk:
            base = [r for r in base if not str(_sk.get(r["key"], "")).startswith("纯重复")]
    except Exception:  # noqa: BLE001
        pass
    try:                                             # 失败队列中的稿不重复入选（由 write-retry 处理）
        from services import write_failures as _wf0
        _blocked = {r["key"] for r in _wf0.list_open()}
        if _blocked:
            base = [r for r in base if r["key"] not in _blocked]
    except Exception:  # noqa: BLE001
        pass
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
    _rh, _conf = _route.route_detail(cand.get("title") or "", cj[:500])
    pre_channel = "gzh" if hint == "gzh" else _rh
    if pre_channel == "ambiguous":
        pre_channel = "xhs"
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
    hist_excerpts = {}          # key -> 节选行（供 classify 后按合格类型决定是否注入）
    related_keys = []
    rel_candidates = []
    try:
        ck = [k.strip() for k in (cand.get("cluster_keys") or "").split(",") if k.strip()]
        sibs = [x for x in (_news.get_by_key(k) for k in ck) if x]
        merge_text = "\n".join(
            f"[{s['key'][:12]}] {s.get('title')}｜原文节选：{(s.get('content_ja') or '')[:1200]}" for s in sibs)
        rel_candidates += [{"k12": s["key"][:12], "title": s.get("title") or "",
                             "excerpt": (s.get("content_ja") or "")[:300]} for s in sibs]
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
            for s in hs:        # 只登记节选，**暂不注入**（classify 后再按合格类型注入）
                hist_excerpts[s["key"]] = (f"[{s['key'][:12]}] {s.get('title')}｜旧文节选："
                                           f"{((s.get('rewritten_content') or s.get('content_ja') or ''))[:400]}")
            rel_candidates += [{"k12": s["key"][:12], "title": s.get("title") or "",
                                 "excerpt": ((s.get("rewritten_content") or s.get("content_ja") or ""))[:300]} for s in hs]
            related_keys += hist
    except Exception:  # noqa: BLE001
        pass
    related_keys = list(dict.fromkeys(related_keys))
    return {"content_ja": cj[:cap], "target": target, "tmin": tmin, "tmax": tmax, "refs": refs, "patterns": patterns,
            "merge_text": merge_text, "hist_text": hist_text, "hist_excerpts": hist_excerpts,
            "related_keys": related_keys,
            "cluster_keys": [k.strip() for k in (cand.get("cluster_keys") or "").split(",") if k.strip()],
            "rel_candidates": rel_candidates,
            "method": r["publish_method"], "pre_channel": pre_channel,
            "channel_hint": hint, "route_conf": _conf,
            "spec": {"fmt": cand.get("format"), "lf": cand.get("is_long_form"), "ja": len(cj)}}


def compose(cand, pkg, force_channel=None, prev=None, retry_ctx=None, max_tokens=16000):
    """阶段3 撰写（LLM）：只吃写作包 pkg。返回 dict（channel/title/body/gzh_*/related）。"""
    msgs = [{"role": "system", "content": SYS + "\n\n" + _read("agent/prompts/write.md")}]
    if force_channel:
        chan_note = f"渠道已指定：channel=\"{force_channel}\"（不要更改）。"
    else:
        _conf = pkg.get("route_conf")
        _lead = ("渠道机械判据不明确（低置信度），请先根据内容性质自行判断渠道，再决定写法。"
                 if _conf == "low" else "")
        chan_note = (_lead + f"机械预判渠道：{pkg.get('pre_channel', 'xhs')}（可覆盖；拿不准沿用它）。"
                     "若同一素材 xhs 与 gzh 都合适，可选 channel=\"both\" 并同时给 gzh_title/gzh_body。")
    user = (f"素材 key={cand['key']} title={cand.get('title')} fmt={cand.get('format')} "
            f"lf={cand.get('is_long_form')}；长度要求：{pkg['target']}。\n{chan_note}\n"
            "全中文（假名≤5），行内「、」≤1，标题≤20字。（同事件素材可合并；同人物历史不合并正文。）\n\n"
            f"=== content_ja（原文全文）===\n{pkg['content_ja']}")
    if pkg["refs"]:
        user += "\n\n=== 相关规范/案例（节选）===\n" + pkg["refs"]
    if pkg.get("merge_text"):
        user += "\n\n=== 同事件关联（可合并：把这些素材的角度并入正文）===\n" + pkg["merge_text"]
    if pkg.get("hist_text"):
        user += "\n\n=== 同人物历史（前情参考，不要照抄）===\n" + pkg["hist_text"]
    if pkg.get("relations"):
        _lab = {"时间线补充": "开头交代旧事件，主体写新进展（合并写新版）", "人物呼应": "以续篇口吻，一句带过前情", "新角度": "从新角度切入，不要重复旧文"}
        user += ("\n\n=== 历史关联（已判定类型，按类型写）===\n"
                 + "\n".join(f"- [{r['key'][:12]}] {r['type']}：{_lab.get(r['type'], '')}" for r in pkg["relations"]))
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


def _extract_json(raw):
    """从 LLM 文本里稳健提取第一个**括号配平**的 JSON 对象（跳过字符串内的括号，应对多余文字/多个对象）。"""
    raw = raw or ""
    for m in re.finditer(r"\{", raw):
        depth, start, in_str, esc = 0, m.start(), False, False
        for i in range(start, len(raw)):
            ch = raw[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i + 1])
                    except Exception:  # noqa: BLE001
                        break
    return None


def _score_call(sysd, usr, max_tokens, dims, agg="sum", tries=2):
    """LLM 评分：解析容错 + 重试。缺 total 时按维度合成（sum/avg）；全失败才返回 total=0。"""
    last = None
    for _ in range(tries):
        try:
            raw = llm.chat([{"role": "system", "content": sysd},
                            {"role": "user", "content": usr}], max_tokens=max_tokens)
        except Exception as e:  # noqa: BLE001
            last = {"error": str(e)}
            continue
        d = _extract_json(raw)
        if isinstance(d, dict):
            if not isinstance(d.get("total"), (int, float)):
                nums = [d[k] for k in dims if isinstance(d.get(k), (int, float))]
                if nums:
                    d["total"] = sum(nums) if agg == "sum" else round(sum(nums) / len(nums))
            if isinstance(d.get("total"), (int, float)):
                return d
            last = {"error": "no total", "raw": (raw or "")[:200]}
        else:
            last = {"error": "json parse fail", "raw": (raw or "")[:200]}
    return {**(last or {}), "total": 0}


def score_content(title, body, max_tokens=4000):
    """5 维评分（各 0-2）+ 每维评语 + 整体修改建议（供改稿参考，对齐原 skill）。"""
    sysd = ("你是严格的中文内容评审。对 5 个维度各 0-2 打分并**给一句理由**，再给**整体修改建议**。"
            '只输出严格 JSON：{"爆发点":{"score":n,"reason":".."},"情绪价值":{...},"信息增量":{...},'
            '"内容深度":{...},"标题质量":{...},"total":n,"建议":".."}（各0-2，total=五维之和）。')
    usr = _read(REVIEW_PROMPT)[:3000] + f"\n\n=== 稿件 ===\n标题：{title}\n\n{body[:4000]}"
    _dims = ("爆发点", "情绪价值", "信息增量", "内容深度", "标题质量")
    last = {"total": 0, "error": "parse"}
    for _ in range(2):
        try:
            raw = llm.chat([{"role": "system", "content": sysd},
                            {"role": "user", "content": usr}], max_tokens=max_tokens)
            d = _extract_json(raw)
        except Exception as e:  # noqa: BLE001
            last = {"total": 0, "error": str(e)}
            continue
        if not isinstance(d, dict):
            continue
        if not isinstance(d.get("total"), (int, float)):
            nums = []
            for n in _dims:
                v = d.get(n)
                v = v.get("score") if isinstance(v, dict) else v
                if isinstance(v, (int, float)):
                    nums.append(v)
            if len(nums) == 5:
                d["total"] = sum(nums)
        if isinstance(d.get("total"), (int, float)):
            return d
    return last


def score_gzh(title, body, max_tokens=4000):
    sysd = '你是公众号内容评审。只输出 JSON：{"标题吸引力":n,"叙事质量":n,"公众号适配度":n,"total":n}（各1-10，合格线7）。'
    usr = ("先做去魅测试：去掉所有日本专名后，文章是否仍有独立传播价值？再做背景锚定：开头第一段专有名词是否过多/路人看不懂？\n\n"
           f"标题：{title}\n\n{body[:4000]}")
    return _score_call(sysd, usr, max_tokens,
                       ["标题吸引力", "叙事质量", "公众号适配度"], agg="avg")


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
    """标题按 xhs_title_len 机械改短到 <=limit（保底）。"""
    while title and _pc.title_len(title) > limit:
        title = title[:-1].rstrip()
    return title


def _fit_title(title, channel="xhs"):
    """边界优先截断；无边界 → LLM 重写一次（保钩子，不砍词义）。"""
    limit = _titles.limit_for(channel)
    t, need_llm = _titles.fit(title, limit)
    if need_llm and title:
        try:
            raw = llm.chat([{"role": "system", "content":
                             f"把给定标题改写到 {limit} 字以内，保留钩子/数字/矛盾点，不做标题党，"
                             "只输出标题本身（不要引号、不要解释）。"},
                            {"role": "user", "content": title}], max_tokens=300)
            cand_t = (raw or "").strip().strip('"').splitlines()[0].strip() if raw else ""
            if cand_t and _pc.title_len(cand_t) <= limit:
                t = cand_t
        except Exception:  # noqa: BLE001
            pass
    return t


def _need(cand, channel, body_len=None):
    """story 正文 > soft_max(900) → 门槛提到 min_score_long(9)（长必须有长的价值）。"""
    _t = _rules.thresholds()
    if channel == "gzh":
        return _t.get("gzh", {}).get("min_score", 7)
    _x = _t.get("xhs", {})
    if cand.get("format") == "story":
        base = _x.get("story_min_score", 8)
        if body_len and body_len > _x.get("story_soft_max", 900):
            base = max(base, _x.get("story_min_score_long", 9))
        return base
    return _x.get("news_min_score", 7)


REL_SYS = ('你是"跨时间关联"判断器（对齐旧 skill 第3层）。给【本篇】与各【候选】(同事件或同人物历史的旧文)，'
           '判断每个候选与本篇的关联类型，只输出严格 JSON：{"relations":[{"key":"<key前12位>","type":"<类型>"}]}。'
           '类型：时间线补充=旧事件有新进展（本轮合并写新版）｜人物呼应=旧文写过该人物、本篇是新事件（写续篇）｜'
           '新角度=旧文写了X面、本篇给Y面（写不同角度）｜纯重复=同一事件标题类似（本篇应跳过不写）｜无关联｜同名不同人。'
           '前置：命中必须先核对是否同一人；同名/近似名不同人→"同名不同人"。')


MAT_SYS = ('判断日文素材类型，只输出严格 JSON：{"type":"catalog|multi_artist|commentary|deep_interview|ranking|normal"}。'
           'catalog=片单/作品一览(目录占原文70%+)；multi_artist=多艺人综合报道/时间表(目标人物≤1/3)；'
           'commentary=评论/分析(单视角论点为主)；deep_interview=深访/自述/采访(引语丰富)；'
           'ranking=榜单/排行榜；normal=单人物单事件普通报道。')


def classify_material(cand, max_tokens=200):
    """1 次 LLM 通读判定素材类型（供密度豁免判定）。"""
    _set_mat = (cand.get("title") or "") + "\n" + (cand.get("content_ja") or "")[:3000]
    try:
        raw = llm.chat([{"role": "system", "content": MAT_SYS},
                        {"role": "user", "content": _set_mat}], max_tokens=max_tokens)
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        return (d.get("type") or "normal").strip()
    except Exception:  # noqa: BLE001
        return "normal"


def classify_relations(cand, pkg, max_tokens=2000):
    """第3层：对候选(同事件∪同人物历史)判关联类型；返回 [{"key":完整key,"type":...}]。"""
    cands = pkg.get("rel_candidates") or []
    if not cands:
        return []
    user = (f"【本篇】{cand.get('title')}｜{(cand.get('content_ja') or '')[:800]}\n\n【候选】\n"
            + "\n".join(f"[{c['k12']}] {c['title']}｜{c['excerpt']}" for c in cands))
    try:
        raw = llm.chat([{"role": "system", "content": REL_SYS}, {"role": "user", "content": user}],
                       max_tokens=max_tokens)
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}
        rels = d.get("relations") or []
    except Exception:  # noqa: BLE001
        return []
    out, seen = [], set()
    for it in rels:
        if not isinstance(it, dict):
            continue
        for k in [x for x in _resolve_keys([it.get("key")]).split(",") if x]:
            if k not in seen:
                seen.add(k)
                out.append({"key": k, "type": (it.get("type") or "").strip()})
    return out


def _produce(cand, pkg, channel, first=None):
    """单渠道：编写→门禁(precheck+renwei exit1/gzh)→评分→改稿(最多3轮,基于上一版)。"""
    need = _need(cand, channel)
    _log(f"  [{channel}] 改稿循环（门槛 {need}/10，最多 3 轮）")
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
                    _dims = ("爆发点", "情绪价值", "信息增量", "内容深度", "标题质量")
                    _low, _rs = [], []
                    for _k in _dims:
                        v = score.get(_k)
                        _sv = v.get("score") if isinstance(v, dict) else v
                        if isinstance(_sv, (int, float)) and _sv < 2:
                            _low.append(_k)
                        if isinstance(v, dict) and v.get("reason"):
                            _rs.append(f"{_k}：{v['reason']}")
                    _msg = f"内容评分 {score.get('total')}/10 低于门槛 {need}，重点加强：{'、'.join(_low) or '各维度'}"
                    if _rs:
                        _msg += "\n评审理由：" + "；".join(_rs)
                    if score.get("建议"):
                        _msg += "\n修改建议：" + str(score.get("建议"))
                    fb.append(_msg)
                try:
                    res = _kana.new_terms(body)
                    if res:
                        _ctxs = []
                        for _ln in (title + "\n" + body).split("\n"):
                            if any(t in _ln for t in res):
                                _sn = _ln.strip()
                                if _sn and _sn not in _ctxs:
                                    _ctxs.append(_sn[:90])
                        fb.append("残留假名：把**下列整句**里含假名的词/作品名/节目名翻成中文或罗马字"
                                  "（标题+正文都要，不要留下任何假名）：\n"
                                  + "\n".join(f"    · {c}" for c in _ctxs[:6]))
                except Exception:  # noqa: BLE001
                    pass
                if any("格言公式" in str(p) for p in fb):     # renwei 误报高发：给具体改写指引
                    fb.append("「格言公式」多为误报：把『X的(路/语言/镜子/货币/篇章/缩影/写照)』"
                              "改成『X的方向/说法/…』或平铺陈述句，标题行也要改。")
                if any("标志性动词" in str(p) for p in fb):
                    fb.append("去掉『标志着/见证了/体现/彰显/折射出』这类拔高动词，直接陈述事实。")
                _x = _rules.xhs_th()
                _blen = mech.get("body_len") or len(body)
                if (score.get("total") or 0) < need and _blen > _x.get("story_soft_max", 900):
                    fb.append(f"正文 {_blen} 字偏长但评分 {score.get('total')}/{need}：要么精简到 ≤"
                              f"{_x.get('story_soft_max',900)} 字，要么把内容深度/信息增量补到 ≥{need}"
                              f"（长必须有长的价值）。")
                if _blen > _x.get("story_hard_max", 1300):
                    fb.append(f"正文 {_blen} 字超过 {_x.get('story_hard_max',1300)}：必须精简到 ≤"
                              f"{_x.get('story_hard_max',1300)}（仅留最有价值段落）。")
                fb = list(dict.fromkeys(fb))
                if body and len(body) < pkg["tmin"]:
                    fb.insert(0, f"字数严重不足：正文仅 {len(body)} 字，**必须扩写到 ≥{pkg['tmin']} 字**"
                                  f"（密度须 ≥30%，低于会被打回；补原文细节/背景，不要灌水）")
                ctx = ("基于上一版修改。**若字数不足，务必扩写到位**（其余问题只做局部修改）；"
                       "修正下列问题后重新只输出 JSON：\n- " + "\n- ".join(fb or ["提升钩子与情绪"]))
                prev = (title, body, channel)
            import time as _t
            _c0 = _t.time()
            _log(f"  撰写 尝试 {attempts}/3 [{channel}] 请求 LLM…")
            d = compose(cand, pkg, force_channel=channel, prev=prev, retry_ctx=ctx)
            _bt = (d.get("body") or d.get("gzh_body") or "")
            _log(f"  撰写完成 {int(_t.time() - _c0)}s | title={(d.get('title') or d.get('gzh_title') or '')[:18]} "
                 f"body={len(_bt)}字")
            if d.get("related"):
                llm_related = d.get("related")
            title, body = d.get("title") or "", d.get("body") or ""
            # gzh：LLM 可能把正文放进 gzh_body（而非 body）→ 回退读取
            if not (body or "").strip() and (d.get("gzh_body") or "").strip():
                body = d.get("gzh_body")
                title = title or (d.get("gzh_title") or "")
        body, _ = _dh.fix_text(body)
        body = _kana.replace(body)
        body, _ = _nv.replace(body)
        title = _kana.replace(title)
        title, _ = _nv.replace(title)
        title = _fit_title(title, channel)
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
        if pkg.get("material_type") in ("catalog", "multi_artist", "commentary"):
            _drop = [p for p in mech["problems"] if "密度" in p]     # 原 skill：只免密度
            if _drop:
                mech["problems"] = [p for p in mech["problems"] if "密度" not in p]
                mech.setdefault("den_exempt", []).extend(_drop)
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
            _log(f"  ✗ 门禁未过: {'; '.join(gate)[:120]}")
            continue
        score = score_gzh(title, body) if channel == "gzh" else score_content(title, body)
        need = _need(cand, channel, mech.get("body_len") or len(body))    # 按长度分层
        _log(f"  评分 {score.get('total')}/{need}"
             f"{'（通过）' if (score.get('total') or 0) >= need else '（偏低，继续改）'}")
        if (score.get("total") or 0) >= need:
            break
    if not mech["problems"] and (score.get("total") or 0) < need:
        mech["problems"] = list(mech.get("problems") or []) + [
            f"内容评分 {score.get('total')}/10 < 门槛 {need}（未达标）"]
    ok = not mech["problems"] and (score.get("total") or 0) >= need
    if mech.get("den_exempt"):
        _log(f"  密度豁免（{pkg.get('material_type')}）：{'; '.join(mech['den_exempt'])[:60]}")
    try:
        _kana.log_pending(body, note=cand["key"][:12])
    except Exception:  # noqa: BLE001
        pass
    try:
        _nw = _nv.check(title, body, cand.get("content_ja") or "")
    except Exception:  # noqa: BLE001
        _nw = []
    return {"channel": channel, "ok": ok, "attempts": attempts, "title": title, "text": body,
            "body": mech.get("body_len"), "h2": mech.get("h2"), "kana": mech.get("kana"),
            "score": score.get("total"), "problems": mech.get("problems") or [],
            "related": llm_related, "warns": _nw, "exempt": mech.get("exempt") or [],
            "score_obj": score}


def write_one(cand, dry_run=True, force_channel=None):
    """阶段3~5：编写(可 both 双版本)→门禁→评分→改稿→入库+验证。force_channel 用于指定渠道。

    AKB大TOP 晒照型（review 标 akb_type=bullet）→ **不写正文**，只设 preselected=1/publish_xhs=0。
    """
    if (cand.get("akb_type") or "").strip().lower() == "bullet":
        if not dry_run:
            _news.update_news(cand["key"], {"preselected": 1, "publish_xhs": 0, "publish_mode": "normal",
                                            "score_dims": "akb-top-bullet"})
        return {"key": cand["key"][:12], "full_key": cand["key"], "ok": True, "bullet": True,
                "attempts": 0, "channel": "xhs", "title": "", "text": "", "body": 0, "h2": 0,
                "kana": 0, "method": "bullet", "score": 0, "related": "", "problems": [], "versions": []}
    _log(f"▶ {cand['key'][:12]} 《{(cand.get('title') or '')[:20]}》 fmt={cand.get('format')} lf={cand.get('is_long_form')}")
    pkg = prepare_package(cand)
    method = pkg["method"]
    _log(f"  准备: method={method} pre={pkg.get('pre_channel')}({pkg.get('route_conf')}) "
         f"关联候选={len(pkg.get('rel_candidates') or [])}")
    pkg["material_type"] = classify_material(cand)          # 1 次 LLM 通读判类型
    _log(f"  素材类型: {pkg['material_type']}")
    if not dry_run:
        try:
            _news.update_news(cand["key"], {"material_type": pkg["material_type"]})   # 落库
        except Exception:  # noqa: BLE001
            pass
    # 第3层：先判关联类型（旧 skill 3b），再按类型写
    _rels = classify_relations(cand, pkg)
    _log(f"  关联判定: {[(r['key'][:8], r['type']) for r in _rels] or '无'}")
    _KEEP = {"时间线补充", "人物呼应", "新角度"}
    if any(r.get("type") == "纯重复" for r in _rels):
        if not dry_run:
            _news.update_news(cand["key"], {"grade": "C", "grade_reason": "纯重复(历史已发)"})
        return {"key": cand["key"][:12], "full_key": cand["key"], "ok": False, "skipped": True,
                "attempts": 0, "channel": pkg.get("pre_channel") or "xhs", "title": "", "text": "",
                "body": 0, "h2": 0, "kana": 0, "method": method, "score": 0, "related": "",
                "problems": ["纯重复(历史已发)→跳过"], "versions": []}
    pkg["relations"] = [r for r in _rels if r.get("type") in _KEEP and r["key"] != cand["key"]]
    _kept = {r["key"] for r in pkg["relations"]}                 # 只注入「合格类型」的同人物历史
    _he = pkg.get("hist_excerpts") or {}
    pkg["hist_text"] = "\n".join(_he[k] for k in _kept if k in _he)
    rk_s = ",".join(dict.fromkeys(r["key"] for r in pkg["relations"]))
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
    _mr = None
    try:
        _mr = _gate.check(cand.get("title") or "", (versions[0]["text"] if versions else ""))
    except Exception:  # noqa: BLE001
        _mr = None
    if not dry_run and _mr:
        _news.update_news(cand["key"], _mr)
    prim = versions[0]
    if not dry_run and prim.get("score_obj"):                 # 5 维评分落表（不动原 title_score/content_score）
        _so = prim["score_obj"]

        def _sv(k):
            v = _so.get(k)
            v = v.get("score") if isinstance(v, dict) else v
            return v if isinstance(v, (int, float)) else 0
        try:
            _news.update_news(cand["key"], {
                "write_score": _so.get("total") or 0,
                "write_title_score": _sv("标题质量"),
                "write_content_score": sum(_sv(k) for k in
                                           ("爆发点", "情绪价值", "信息增量", "内容深度")),
                "write_dims": json.dumps(_so, ensure_ascii=False)[:4000]})
        except Exception:  # noqa: BLE001
            pass
    return {"key": cand["key"][:12], "full_key": cand["key"], "ok": any(v["ok"] for v in versions),
            "manual": _mr,
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


def retry_failures(limit=10):
    """重试「失败队列」里到期的稿：成功/已写/素材不存在 → 移除；再失败 → 待人工。"""
    from services import write_failures as _wf
    rows = _wf.due()[:limit]
    if not rows:
        print("[write-retry] 无到期失败稿")
        return []
    print(f"[write-retry] 到期 {len(rows)} 篇")
    out = []
    for r in rows:
        key = r["key"]
        cand = _news.get_by_key(key)
        if not cand:
            _wf.mark_resolved(key, "素材不存在（已结）"); out.append((key, "gone"))
            print(f"  - {key[:12]} 素材不存在→标记成功"); continue
        if (cand.get("rewritten_content") or "") or (cand.get("wechat_content") or ""):
            _wf.mark_resolved(key, "已有正文（已结）"); out.append((key, "already-written"))
            print(f"  - {key[:12]} 已有正文→标记成功"); continue
        try:
            res = write_one(cand, dry_run=False)
            if res.get("ok") or res.get("skipped") or res.get("bullet"):
                _wf.mark_resolved(key, "重试成功"); out.append((key, "ok"))
                print(f"  ✅ {key[:12]} 重试成功（标记 resolved）")
            else:
                why = "；".join(res.get("problems") or ["未达标"])
                _wf.mark_manual(key, why); out.append((key, "needs_manual"))
                print(f"  ❌ {key[:12]} 再次失败→待人工：{why[:60]}")
        except Exception as e:                        # noqa: BLE001
            import traceback as _tb
            _wf.mark_manual(key, f"{type(e).__name__}: {e}", tb=_tb.format_exc())
            out.append((key, "needs_manual"))
            print(f"  ❌ {key[:12]} 异常→待人工：{type(e).__name__}: {e}")
    return out


def main_failures(argv=None):
    """打印写稿失败主因统计 + 未结列表（分析用）。"""
    import argparse
    from services import write_failures as _wf
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args(argv)
    st = _wf.stats(a.days)
    print(f"[写稿失败·近 {st['days']} 天] 共 {st['total']} 篇")
    print("  主因分布：")
    for x in st["by_category"]:
        print(f"    {x['n']:>4}  {x['label']}")
    print("  状态：" + " ".join(f"{s['status']}={s['n']}" for s in st["by_status"]))
    op = _wf.list_open()
    if op:
        print(f"  未结 {len(op)} 篇：")
        for r in op[:20]:
            print(f"    [{r['status']}] {r['key'][:12]} {r.get('reason','')[:50]}")
    return 0


def main_retry(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="重试写稿失败队列（到期项）")
    ap.add_argument("--limit", type=int, default=10)
    a = ap.parse_args(argv)
    retry_failures(a.limit)
    return 0


def pick_rewrite_today():
    """今天入库的候选（grade S/A/AKB，story/news，有原文），**忽略已写** → 供强制重写。"""
    import datetime as _dt
    today = _dt.datetime.now().strftime("%Y-%m-%d")
    rows = _news.query_news(status="active", limit=500)
    base = [r for r in rows if (r.get("created_at") or "").startswith(today)
            and (r.get("content_ja") or "")
            and r.get("format") in ("story", "news", "ranking", "comparison")
            and not ((r.get("akb_type") or "") == "bullet" and r.get("preselected"))]
    graded = [r for r in base if (r.get("grade") or "").upper() in ("S", "A", "AKB", "AKB大TOP")]
    picks, used = [], set()
    for r in sorted(graded, key=lambda x: -(x.get("title_score") or 0)):
        cl = {x for x in (r.get("cluster_keys") or "").split(",") if x}
        if (cl | {r["key"]}) & used:
            continue
        picks.append(r)
        used |= cl | {r["key"]}
    return picks


def _print_split_preview(cands):
    """拆篇预览（只读）：打印 cluster 体量与分组，不写库。"""
    from services import split_write as _sp
    for c in cands:
        L = _sp.cluster_text_len(c)
        hit = _sp.should_split(c)
        print(f"{'SPLIT' if hit else 'keep '} {c['key'][:12]} cluster体量={L} "
              f"主={(c.get('title') or '')[:24]}")
        if hit:
            for i, g in enumerate(_sp.split_groups(c), 1):
                print(f"      组{i}: {[k[:8] for k in g]}")


def _split_candidates(cands, threshold=None):
    """关联素材体量过大 → 拆多篇（密度极限 fallback，默认关闭）。"""
    from services import split_write as _sp
    _th = threshold or _sp.DEFAULT_THRESHOLD
    out = []
    for c in cands:
        if _sp.should_split(c, _th):
            for g in _sp.split_groups(c, _th):
                main = _news.get_by_key(g[0])
                if main:
                    out.append({**main, "cluster_keys": ",".join(g[1:])})
        else:
            out.append(c)
    return out


def _enqueue_failure(cand, reason, stage, attempts=0, channel="", title="", tb=""):
    """**确认放弃/跳过时**才把文章落入失败表（不在中间态写）。

    - 基础设施：LLM 瞬时错误由 `agent.llm._post` 先做指数退避重试，**退避全部失败**（LiteLLMError）
      或其它不可恢复异常才会走到这里。
    - 质量：门禁/评分经 3 轮改稿循环后仍不达标（`write_one` 返回 ok=False）。
    """
    from services import write_failures as _wf
    _wf.record(cand["key"], reason, stage=stage, attempts=attempts, channel=channel,
               title=title or cand.get("title") or "", tb=tb)
    try:
        from services import error_log as _elog              # 同步写 error.log
        _elog.log(f"写稿失败 {cand['key'][:12]} [{stage}] {reason}", tb=tb)
    except Exception:  # noqa: BLE001
        pass


def run(n=0, deliver=False, dry_run=False, split_large=False, key=None, force_channel=None,
        verify_gallery=False, split_threshold=None, split_preview=False, rewrite_today=False):
    if key:
        import sqlite3
        conn = sqlite3.connect(paths.sqlite_path())
        try:
            r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (key[:40],)).fetchone()
        finally:
            conn.close()
        cands = [_news.get_by_key(r[0])] if r else []
        cands = [c for c in cands if c]
    elif rewrite_today:
        cands = pick_rewrite_today()
    else:
        cands = pick_candidates(n)
    if not key and cands:
        try:
            _cov = _batches.covered_keys(_batches.recent())
            _backlog = [c["key"][:12] for c in cands if c["key"] not in _cov]
            if _backlog:
                _more = "..." if len(_backlog) > 8 else ""
                print(f"[跨日] {len(_backlog)} 篇为新/漏写候选：{_backlog[:8]}{_more}")
        except Exception:  # noqa: BLE001
            pass
    if split_preview:
        _print_split_preview(cands)
        return []
    if split_large:
        cands = _split_candidates(cands, split_threshold)
    def _emit(r):
        """逐篇即时打印（边写边打印）。"""
        if r.get("bullet"):
            print(f"BULLET {r['key']}（AKB 晒照型 → 仅入库不写正文）", flush=True)
        elif r.get("skipped"):
            print(f"SKIP {r['key']}（{'; '.join(r.get('problems') or ['纯重复'])}）", flush=True)
        elif r.get("error"):
            print(f"ERROR {r['key']}（{r['error']}）", flush=True)
        else:
            for v in r.get("versions") or []:
                tail = "" if v["ok"] else " | " + "; ".join(v["problems"])
                if v.get("exempt"):
                    tail = " | 豁免(" + "; ".join(v["exempt"])[:60] + ")"
                print(f"{'PASS' if v['ok'] else 'FAIL'} {r['key']} [{v['channel']}] att={v['attempts']} "
                      f"m={r.get('method', '')} score={v['score']} body={v['body']} ##={v['h2']} "
                      f"kana={v['kana']} related={(r.get('related') or '')[:24]}{tail}", flush=True)

    results = []
    for idx, c in enumerate(cands, 1):
        print(f"[{idx}/{len(cands)}] 处理 {c['key'][:12]} 《{(c.get('title') or '')[:24]}》...", flush=True)
        try:
            r = write_one(c, dry_run=dry_run, force_channel=force_channel)
        except Exception as e:                        # noqa: BLE001 单篇异常不中断整批
            print(f"❌ {c['key'][:12]} 异常: {type(e).__name__}: {e}", flush=True)
            if not dry_run:                           # 确认放弃（异常已含退避耗尽）→ 落表
                import traceback as _tb
                _enqueue_failure(c, f"{type(e).__name__}: {e}", "exception", tb=_tb.format_exc())
            r = {"key": c["key"][:12], "full_key": c["key"], "ok": False, "error": str(e),
                 "versions": [], "related": "", "channel": "", "problems": [f"{type(e).__name__}: {e}"]}
            results.append(r); _emit(r); continue
        if not dry_run and not r.get("ok") and not r.get("skipped") and not r.get("bullet"):
            _enqueue_failure(c, "；".join(r.get("problems") or ["未达标"]), "gate",   # 质量确认放弃
                             attempts=r.get("attempts", 0), channel=r.get("channel") or "",
                             title=r.get("title") or "")
        if not dry_run and r.get("ok"):                    # 写稿成功 → 清掉该 key 旧失败记录
            try:
                from services import write_failures as _wf
                _wf.mark_resolved(c["key"], "写稿成功")
            except Exception:  # noqa: BLE001
                pass
        results.append(r); _emit(r)
    passed = sum(1 for r in results if r["ok"])
    print(f"\n写稿通过 {passed}/{len(results)}（机械门禁+renwei+评分）"
          f"（{'dry-run，未入库' if dry_run else '已入库'}）")
    if not dry_run and not key:                       # 批次 manifest（跨日漏写检测 + 跳过清单）
        import datetime as _dt
        processed = [{"key": r["full_key"], "ok": r["ok"], "channel": r.get("channel"),
                      "manual": bool(r.get("manual"))} for r in results if r.get("full_key")]
        skipped = [{"key": r["full_key"], "reason": "；".join(r.get("problems") or ["跳过"])}
                   for r in results if r.get("full_key") and r.get("skipped")]
        _batches.save(str(_dt.datetime.now().date()), {"processed": processed, "skipped": skipped})
        try:                                          # 假名新词自动入字典（先自动，人工后审）
            _auto = _kana.auto_promote()
            if _auto:
                print(f"[假名] 自动入字典 {len(_auto)} 条（人工可后审 data/kana_autopromoted.jsonl）", flush=True)
        except Exception:  # noqa: BLE001
            pass

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
    ap.add_argument("--split-threshold", type=int, default=None,
                    help="拆篇体量阈值（默认读 config，3000）")
    ap.add_argument("--split-preview", action="store_true",
                    help="只打印拆篇分组预览，不写稿（只读）")
    ap.add_argument("--rewrite-today", action="store_true",
                    help="强制重写今天入库的候选（忽略已写标记）")
    a = ap.parse_args(argv)
    run(a.n, a.deliver, a.dry_run, a.split_large, a.key, a.force_channel, a.verify_gallery,
        a.split_threshold, a.split_preview, a.rewrite_today)
    return 0
