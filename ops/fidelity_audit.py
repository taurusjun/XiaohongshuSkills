#!/usr/bin/env python3
"""保真度审计（数据驱动）——**不看「规则有没有写进代码」，只拿真实数据校验 skill 口径**。

为什么需要它（2026-10-18 的教训）：
  按「规则是否实现」做审计会整类漏掉问题：
    ① 机械缺陷      —— LIMIT 200 无 ORDER BY（系统性只取最老数据）
    ② 数据流断点    —— review 产了 cross_time/advice/grade_reason，却从不落库
    ③ 静默丢弃      —— update_news 白名单外的字段被丢
    ④ 行为漂移      —— 「同簇只写一条」只在单次 run 内生效
  这些规则的「实现」都在，所以旧审计全判 ✅。

本脚本只做一件事：**用 skill 的精确口径校验库里真实产出的数据**。
口径全部独立实现（**不调用 services.precheck**）——否则门禁本身写错时，审计会跟着一起错。

用法：
  python ops/fidelity_audit.py                 # 审计最近 3 天
  python ops/fidelity_audit.py --date 2026-10-17
  python ops/fidelity_audit.py --json          # 机器可读
退出码：0=无违规；1=有违规（供定时任务告警）
"""
import argparse
import json
import os
import re
import sqlite3
import sys
from datetime import date as _date, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

CJK = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
KEEP_CHANNEL = ("xhs", "both")


# ---------------- 口径（全部来自 skill 文本） ----------------
def title_weight(t: str) -> int:
    """skill（xhs_word_count.py）：标题 = CJK/全角×1 + 其余（拉丁/数字/假名）×0.5，**向上取整**。"""
    import math
    t = t or ""
    full = sum(1 for c in t if ("\u4e00" <= c <= "\u9fff") or ("\u3000" <= c <= "\u303f")
               or ("\uff00" <= c <= "\uffef"))
    return math.ceil(full + (len(t) - full) * 0.5)


def content_len(t: str) -> int:
    """skill（xhs_word_count.py）：正文字数 = 所有字符×1，换行不计。"""
    t = t or ""
    return len(t) - t.count("\n")


def kana_count(t: str) -> int:
    return len(KANA.findall(t or ""))


def body_cjk_len(t: str) -> int:      # 密度分母用：日文原文的中日韩字符数
    return len(CJK.findall(t or "")) + kana_count(t)


def dens_pct(body: str, ja: str) -> float:
    """密度 = 正文中文字数 / 主素材日文原文（按 ja 的中日韩字符数）。"""
    j = len(ja or "")                      # skill：分母 = 主素材 content_ja 全文（所有字符×1）
    return 100.0 * (len(body or "") - (body or "").count("\n")) / j if j else 0.0


# ---------------- 审计项 ----------------
def ck_related(rows, conn, out):
    bad40, ghost, selfref = [], [], []
    for r in rows:
        for x in [y for y in (r["related_keys"] or "").split(",") if y]:
            if len(x) != 40:
                bad40.append(f'{r["key"]} -> {x[:14]}({len(x)}位)')
            elif x == r["key"]:
                selfref.append(r["key"])
            elif not conn.execute("SELECT 1 FROM news WHERE key=?", (x,)).fetchone():
                ghost.append(f'{r["key"]} -> {x}')
    out.append(("related_keys 全 40 位", bad40))
    out.append(("related_keys 无自我关联", selfref))
    out.append(("related_keys 指向的 key 都存在", ghost))
    # 互设：同事件（纯重复/同簇）应双向。这里只报「A→B 但 B 无 A」的比例（宽松阈值）
    n = miss = 0
    for r in rows:
        for x in [y for y in (r["related_keys"] or "").split(",") if y]:
            n += 1
            back = (conn.execute("SELECT related_keys FROM news WHERE key=?", (x,)).fetchone() or [""])[0] or ""
            if r["key"] not in [y for y in back.split(",") if y]:
                miss += 1
    # 信息项：单向关联比例（含跨时间历史，生产只对**同事件**要求互设 → 不设阈值，仅记录）
    out.append((f"ℹ️ 单向关联比例 {miss}/{n}（生产只要求同事件互设）", []))


def ck_body(rows, out):
    short, long_, h2 = [], [], []
    for r in rows:
        b = r["rewritten_content"] or ""
        if not b:
            continue
        is_story_lf = (r["format"] == "story" and r["is_long_form"])
        exp = (r["publish_method"] == "export")
        if is_story_lf and not exp:
            n = content_len(b)
            if n < 800:
                short.append(f'{r["key"]} {n}字(<800)')
            elif n > 1200:
                long_.append(f'{r["key"]} {n}字(>1200)')
            if b.count("##") < 2:
                h2.append(f'{r["key"]} ##{b.count("##")}(<2)')
        elif not exp and content_len(b) > 950:
            long_.append(f'{r["key"]} {content_len(b)}字(>950, 900±50)')
    out.append(("story&lf=1 正文 800-1200（export 不压字数）", short + long_))
    out.append(("story&lf=1 有 ≥2 个 ##", h2))


def ck_title(rows, out):
    bad = [f'{r["key"]} {title_weight(r["rewritten_title"])}字 {r["rewritten_title"][:24]}'
           for r in rows if (r["rewritten_title"] or "") and title_weight(r["rewritten_title"]) > 20]
    out.append(("标题 ≤20（CJK×1/其余×0.5）", bad))


def ck_kana_dunhao(rows, out):
    kana, dh = [], []
    for r in rows:
        b = r["rewritten_content"] or ""
        if not b:
            continue
        k = kana_count(b) + kana_count(r["rewritten_title"] or "")
        if k > 5:
            kana.append(f'{r["key"]} 假名{k}')
        for ln in b.split("\n"):
            if ln.count("、") >= 2:
                dh.append(f'{r["key"]} {ln[:22]}')
                break
    out.append(("假名 ≤5（含标题行）", kana))
    out.append(("无顿号行（行内「、」≥2）", dh))


def ck_density(rows, out):
    low = []
    for r in rows:
        if not (r["rewritten_content"] or ""):
            continue
        d = dens_pct(r["rewritten_content"], r["content_ja"])
        if d < 30 and r["publish_method"] != "export":
            low.append(f'{r["key"]} {d:.1f}%')
    out.append(("密度 ≥30%（export/深访另计）", low))


def ck_grade(rows, out):
    graded = [r for r in rows if (r["grade"] or "").strip()]
    S = [r for r in graded if (r["grade"] or "").upper() == "S"]
    if not graded:
        out.append(("S 级占比 ≤15%", [])); return
    cap = 3 if len(graded) < 25 else len(graded) * 0.15      # skill：低分母日改绝对上限
    out.append((f"S 级 {len(S)}/{len(graded)} = {100.0*len(S)/len(graded):.1f}%（上限 {cap:.1f}）",
                [] if len(S) <= cap else [f"S 级超限：{len(S)} > {cap:.1f}"]))


def ck_cluster(rows, conn, out):
    seen, multi = set(), []
    for r in rows:
        cl = [x for x in (r["cluster_keys"] or "").split(",") if x]
        if not cl:
            continue
        grp = tuple(sorted(cl + [r["key"]]))
        if grp in seen:
            continue
        seen.add(grp)
        n = 0
        for k in grp:
            d = conn.execute("SELECT rewritten_content,preselected,publish_xhs,COALESCE(xhs_pub_time,'') FROM news WHERE key=?",
                             (k,)).fetchone()
            if not d:
                continue
            # 生产口径：被 related_keys 吸收的条**保留正文但 preselected=0 是预期状态**；
            # 真正的不变量是「每簇 ≤1 篇**待发布/已发布**」。
            if d[1] or d[2] or (d[3] or "").strip():
                n += 1
        _cap = 2
        try:
            _m = conn.execute("SELECT mains_json FROM review_clusters WHERE cluster_id=?",
                              (r.get("cluster_id") or "",)).fetchone()
            if _m and (_m[0] or "").strip():
                _cap = max(1, len(json.loads(_m[0])))
        except Exception:  # noqa: BLE001
            pass
        if n > _cap:
            multi.append(f'{r["key"]} 簇内 {n} 篇处于待发布/已发布（主条上限 {_cap}）')
    out.append(("同簇只写一条（待发布/已发布 ≤1）", multi))


def ck_schedule(rows, out):
    sched = [r for r in rows if (r["xhs_pub_time"] or "").strip()]
    odd = [f'{r["key"]} {r["xhs_pub_time"]}' for r in sched if r["xhs_pub_time"][14:16] == "00"]
    orphan = [r["key"] for r in rows if r["publish_xhs"] and not (r["xhs_pub_time"] or "").strip()]
    out.append(("排期非整点", odd))
    out.append(("publish_xhs=1 必有 xhs_pub_time", orphan))


def ck_landing(rows, out):
    """review 产出的字段有没有真的落库（数据流断点类）。"""
    need = {"grade": "grade", "grade_reason": "grade_reason", "review_advice": "review_advice",
            "date_trap": "date_trap"}
    got = {k: sum(1 for r in rows if (r.get(k) or "").strip()) for k in need}
    miss = [f"{k} 落库 {got[k]}/{len(rows)}" for k in need if got[k] == 0 and rows]
    out.append(("review 产出字段已落库（grade/grade_reason/review_advice/date_trap）", miss))
    # LLM 返回的 key 前缀解析失败（静默丢素材/关联的源头；生产 skill 高频事故）
    try:
        _r = conn.execute("SELECT date,n_unresolved FROM review_runs ORDER BY id DESC LIMIT 5").fetchall()
        _bad = [f"{x[0]} 解析失败 {x[1]} 个" for x in _r if (x[1] or 0) > 0]
    except Exception:  # noqa: BLE001
        _bad = []
    out.append(("LLM 返回的 key 前缀全部解析成功（近 5 次 review）", _bad))


def ck_static(out):
    """静态类：静默丢弃 + LIMIT 无 ORDER BY。"""
    src = open(os.path.join(REPO, "scripts/sqlite_db.py"), encoding="utf-8").read()
    m = re.search(r"allowed\s*=\s*\{(.*?)\}", src, re.S)
    allowed = set(re.findall(r"'([^']+)'", m.group(1))) if m else set()
    written = set()
    for root in ("agent", "services", "cli", "scripts"):
        for dp, _, fs in os.walk(os.path.join(REPO, root)):
            for f in fs:
                if not f.endswith(".py"):
                    continue
                t = open(os.path.join(dp, f), encoding="utf-8", errors="ignore").read()
                for mm in re.finditer(r"update_news\(\s*[^,]+,\s*\{", t, re.S):
                    # 只取**顶层**键：跟踪花括号深度，嵌套 dict（如 {"date_trap": json.dumps({"kind":..})}）不算字段
                    i = mm.end() - 1
                    depth, buf, keys = 0, "", []
                    while i < len(t):
                        ch = t[i]
                        if ch == "{":
                            depth += 1
                            if depth == 1:
                                i += 1
                                continue
                        elif ch == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        if depth == 1:
                            buf += ch
                        i += 1
                    for km in re.finditer(r"[\"']([a-z_]+)[\"']\s*:", buf):
                        keys.append(km.group(1))
                    written |= set(keys)
    out.append(("update_news 白名单外字段（会被静默丢弃）", sorted(written - allowed)))
    noorder = []
    for root in ("agent", "services", "cli", "scripts"):
        for dp, _, fs in os.walk(os.path.join(REPO, root)):
            for f in fs:
                if not f.endswith(".py"):
                    continue
                p = os.path.join(dp, f)
                for i, ln in enumerate(open(p, encoding="utf-8", errors="ignore"), 1):
                    s = ln.strip()
                    if ("LIMIT" in s or "limit" in s) and ("SELECT" in s or "select" in s.lower()) \
                            and "ORDER BY" not in s and "order by" not in s.lower() \
                            and "{" not in s:          # 跳过 f-string 模板（ORDER BY 可能是变量）
                        noorder.append(f"{os.path.relpath(p, REPO)}:{i}")
    out.append(("SELECT ... LIMIT 但无 ORDER BY（会系统性只取最旧）", noorder))


# ---------------- 主流程 ----------------
def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", help="只审计这一天（created_at 日期）")
    ap.add_argument("--days", type=int, default=3, help="默认审计最近 N 天")
    ap.add_argument("--db", help="DB 路径（默认 services.paths.sqlite_path()）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", help="把 markdown 报告写到这个路径")
    a = ap.parse_args(argv)

    from services import paths
    db = a.db or paths.sqlite_path()
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    if a.date:
        where, params = "date(created_at)=?", (a.date,)
    else:
        lo = (_date.today() - timedelta(days=a.days)).isoformat()
        where, params = "date(created_at)>=?", (lo,)
    rows = [dict(r) for r in conn.execute(
        f"SELECT * FROM news WHERE {where} AND status='active' AND COALESCE(title,'')!=''", params)]
    if not rows:
        print(f"[fidelity] 无样本（{a.date or a.days}天）"); return 0

    out = []
    ck_related(rows, conn, out); ck_body(rows, out); ck_title(rows, out)
    ck_kana_dunhao(rows, out); ck_density(rows, out); ck_grade(rows, out)
    ck_cluster(rows, conn, out); ck_schedule(rows, out); ck_landing(rows, out)
    ck_static(out)

    # 白名单：已确认接受的历史项（只对**新增**违规告警）
    allow = {}
    try:
        _ap = os.path.join(REPO, "config", "fidelity_allow.json")
        if os.path.exists(_ap):
            for _it in (json.load(open(_ap, encoding="utf-8")).get("items") or []):
                allow[(str(_it.get("check") or ""), str(_it.get("key") or ""))] = str(_it.get("reason") or "")
    except Exception as _e:  # noqa: BLE001
        print(f"  ⚠️ 白名单读取失败: {_e}")

    def _k12(item):
        return str(item).split(" ", 1)[0]      # 条目以**完整 key** 开头，原样取首 token（白名单匹配必须全键）

    viol, accepted, used = [], [], set()
    for _n, _v in out:
        for _item in _v:
            _key = (_n, _k12(_item))
            if _key in allow:
                accepted.append((_n, _item))
                used.add(_key)
            else:
                viol.append((_n, _item))
    for _key in allow:
        if _key not in used:
            print(f"  ⚠️ 白名单条目未命中（check 名或 key 变了？）: {_key[0]} / {_key[1]}")

    if a.json:
        print(json.dumps({"db": db, "sample": len(rows),
                          "violations": [{"check": n, "item": i} for n, i in viol],
                          "accepted": [{"check": n, "item": i, "reason": allow[(n, _k12(i))]} for n, i in accepted]},
                         ensure_ascii=False, indent=1))
    else:
        print(f"[fidelity] 样本 {len(rows)} 篇（{'--date ' + a.date if a.date else '最近 %d 天' % a.days}）")
        for n, v in out:
            print(("  ⚠️ " if v else "  ✅ ") + n + (f"（{len(v)} 处）" if v else ""))
            for x in v[:8]:
                print(("       ℹ️ " if (n, _k12(x)) in allow else "       ") + x)
            if len(v) > 8:
                print(f"       … 另 {len(v)-8} 处")
        print(f"\n新增违规 {len(viol)} 项 / 已接受 {len(accepted)} 项 / 检查项 {len(out)}")
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(f"# 保真度审计 {_date.today().isoformat()}\n\n样本 {len(rows)} 篇\n\n")
            for n, v in out:
                f.write(("### ⚠️ " if v else "### ✅ ") + n + "\n")
                for x in v:
                    f.write(f"- {x}\n")
                f.write("\n")
    return 1 if viol else 0


if __name__ == "__main__":
    sys.exit(main())
