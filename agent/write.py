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
def _grade_keys_from_archive():
    """从最新 review 存档的「分级结果」节抽取 S/A 级 key（best-effort）。"""
    d = paths.REPO_ROOT / "data" / "reviews"
    cands = sorted(d.glob("*-review*.md"), key=lambda p: p.stat().st_mtime, reverse=True) if d.exists() else []
    for f in cands:
        try:
            txt = f.read_text(encoding="utf-8")
        except Exception:
            continue
        m = re.search(r"\n##\s*四、分级结果(.*?)(\n##\s*五、|\Z)", txt, re.S)
        if not m:
            continue
        keys, cur = [], ""
        for line in m.group(1).split("\n"):
            if re.match(r"^###", line):
                cur = line
            if re.search(r"S级|A级", cur):
                keys += re.findall(r"`([0-9a-f]{12,40})`", line)
        if keys:
            return list(dict.fromkeys(keys))
    return []



def pick_candidates(n=3):
    # ① DB grade(S/A) ② review 存档分级 ③ 分数兜底
    rows = _news.query_news(status="active", limit=300)
    base = [r for r in rows if (r.get("content_ja") or "")
            and r.get("format") in ("story", "news") and not (r.get("rewritten_content") or "")]
    graded = [r for r in base if (r.get("grade") or "").upper() in ("S", "A")]
    if graded:
        return sorted(graded, key=lambda r: -(r.get("title_score") or 0))[:n]
    pref = _grade_keys_from_archive()
    if pref:
        prefix = tuple(pref)
        hit = [r for r in base if any(r["key"].startswith(p) for p in prefix)]
        if hit:
            return sorted(hit, key=lambda r: -(r.get("title_score") or 0))[:n]
    base.sort(key=lambda r: -(r.get("title_score") or 0))
    return base[:n]


# ---------------- 阶段3：编写（体裁+字数路由） ----------------
def compose(cand, prev=None, retry_ctx=None, max_tokens=16000):
    """prev=(title,body,channel) 时基于上一版修改。返回 (channel,title,body,related)。"""
    msgs = [{"role": "system", "content": SYS + "\n\n" + _read("agent/prompts/write.md")}]
    cj = cand.get("content_ja") or ""
    _r = _fr.route(len(cj), cand.get("format"), cand.get("is_long_form"))
    tgt = f"{int(len(cj)*0.31)}~{int(len(cj)*0.33)}字(长文 export)" if _r["publish_method"] == "export" else _r["note"]
    user = (f"素材 key={cand['key']} title={cand.get('title')} fmt={cand.get('format')} "
            f"lf={cand.get('is_long_form')}；长度要求：{tgt}。\n"
            "判断走 xhs 还是 gzh（男团/男偶像的产业·厂牌·销量·战略·行业分析→gzh；粉丝向爆料/日常/综艺花絮→xhs）。\n"
            "全中文（假名≤5），行内「、」≤1，标题≤20字。related 放同事件其它素材 key 前缀。\n\n"
            f"=== content_ja ===\n{cj[:3500]}")
    try:
        rel = _refs.relevant(cand.get("title") or "")
        if rel:
            user += "\n\n=== 相关规范/案例（节选）===\n" + rel
    except Exception:
        pass
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
    cj = cand.get("content_ja") or ""
    pmethod = _fr.route(len(cj), cand.get("format"), cand.get("is_long_form"))["publish_method"]
    spec = {"fmt": cand.get("format"), "lf": cand.get("is_long_form"), "ja": len(cj)}

    while attempts < 3:
        attempts += 1
        ctx, prev = None, None
        if attempts > 1:
            fb = list(mech.get("problems") or [])
            if (score.get("total") or 0) < need:
                low = [k for k, v in score.items() if isinstance(v, (int, float)) and k != "total" and v < 2]
                fb.append(f"内容评分 {score.get('total')}/10 低于门槛 {need}，重点加强：{'、'.join(low) or '爆发点/情绪价值/信息增量'}")
            ctx = "基于上一版**修改**（不要整篇重写），修正下列问题后重新只输出 JSON：\n- " + "\n- ".join(fb or ["提升钩子与情绪"])
            prev = (title, body, channel)
        channel, title, body, related = compose(cand, prev=prev, retry_ctx=ctx)
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
    rk = _resolve_keys(related)
    if ok and not dry_run:
        if channel == "gzh":
            _news.update_news(cand["key"], {"wechat_title": title, "wechat_content": body,
                                            "channel": "gzh", "preselected": 0, "publish_xhs": 0, "related_keys": rk})
        else:
            _news.update_news(cand["key"], {"rewritten_title": title, "rewritten_content": body,
                                            "publish_mode": "rewritten", "publish_method": pmethod,
                                            "related_keys": rk, "preselected": 1, "publish_xhs": 0})
        got = _news.get_by_key(cand["key"]) or {}
        ok = (got.get("wechat_title") == title and bool(got.get("wechat_content"))) if channel == "gzh" \
            else (got.get("rewritten_title") == title and len(got.get("rewritten_content") or "") > 50)
    return {"key": cand["key"][:12], "full_key": cand["key"], "ok": ok, "attempts": attempts,
            "channel": channel, "title": title, "text": body, "body": mech.get("body_len"),
            "h2": mech.get("h2"), "kana": mech.get("kana"), "method": pmethod,
            "score": score.get("total"), "related": rk, "problems": mech.get("problems") or []}



def recommend_and_schedule(n=5, seed=None):
    import datetime as dt
    import random
    from services import schedule as _sch
    tomorrow = (dt.datetime.now(_sch.JST) + dt.timedelta(days=1)).date()
    pool = [r for r in _news.query_news(status="active", limit=300)
            if r.get("preselected") == 1 and (r.get("rewritten_content") or "")
            and not r.get("publish_xhs")]
    pool.sort(key=lambda r: -(r.get("title_score") or 0))
    rng = random.Random(seed)
    pick = pool[:n - 1]
    rest = pool[n - 1:]
    if rest:
        pick = pick + [rng.choice(rest)]
    plan = _sch.build_plan(tomorrow, _sch.parse_slots(",".join(_sch.DEFAULT_SLOTS)), 8, rng)
    keys = [r["key"] for r in pick][:len(plan)]
    ok, bad = _sch.apply_plan(paths.sqlite_path(), keys, plan, True)
    return pick[:len(plan)], plan, bad


def run(n=3, deliver=False, dry_run=False):
    cands = pick_candidates(n)
    results = [write_one(c, dry_run=dry_run) for c in cands]
    for r in results:
        print(f"{'PASS' if r['ok'] else 'FAIL'} {r['key']} att={r['attempts']} ch={r['channel']} m={r['method']} "
              f"score={r['score']} body={r['body']} ##={r['h2']} kana={r['kana']} "
              f"related={r['related'][:24]}{'' if r['ok'] else ' | ' + '; '.join(r['problems'])}")
    passed = sum(1 for r in results if r["ok"])
    print(f"\n写稿通过 {passed}/{len(results)}（机械门禁+5维≥8）"
          f"（{'dry-run，未入库' if dry_run else '已入库'}）")

    sched = None
    if not dry_run:
        pick, plan, bad = recommend_and_schedule(5)
        sched = [{"key": p["key"][:12], "title": p.get("title"), "time": t}
                 for p, (_, t) in zip(pick, plan)]
        print("[待发布推荐] " + "; ".join(f"{x['key']}→{x['time']}" for x in sched))

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
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--deliver", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    run(a.n, a.deliver, a.dry_run)
    return 0
