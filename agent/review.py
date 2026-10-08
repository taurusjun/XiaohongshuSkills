"""完整 review skill 运行：4 层 SKILL + 当日素材 → LLM 生成完整存档（一~六）+ 关联写库。

关联模型（对齐原 skill）：
- **无时间窗口**（全历史）；候选由 services.related 按相关性检索。
- LLM 判定分两类：same_event(同事件·可合并) / timeline(跨时间·续篇/时间线)。
- 只写/清「当日」行；历史行不重判（历史由各自那天的 review 负责）。
"""
import json
import sys

from agent import llm
from services import news as _news, paths, related as _rel

SKILL_FILES = [
    "skills/creative/xhs-daily-material-review/SKILL.md",
    "skills/creative/xhs-daily-material-review-layer1/SKILL.md",
    "skills/xhs-daily-material-review-layer23/SKILL.md",
    "skills/xhs-daily-material-review-layer4/SKILL.md",
]

__all__ = ["compact_rows", "prepare_package", "persist_related_mapping",
           "persist_related_mechanical", "run", "main"]


def _read(rel):
    p = paths.REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def compact_rows(date, limit=500):
    rows = _news.query_news(date_from=date, date_to=date, status="active", limit=limit)
    return [{"key": r["key"][:12], "title": r.get("title"), "fmt": r.get("format"),
             "lf": r.get("is_long_form"), "ts": r.get("title_score"), "cs": r.get("content_score"),
             "cj": len(r.get("content_ja") or ""), "src": r.get("fetch_by"),
             "pub": r.get("publish_xhs")} for r in rows]


def prepare_package(date, hist_k=6):
    """review 阶段1 写前准备（机械）：当日全量 + 前一天(日期陷阱)
    + 当日同事件机械候选 + 每个当日稿的**全历史**关联候选（无窗口，按相关性）。"""
    import datetime as dt
    from services import cluster as _cl
    rows = compact_rows(date)
    try:
        prev = compact_rows((dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat())
    except Exception:  # noqa: BLE001
        prev = []
    groups = _cl.cluster([{"key": r["key"], "title": r["title"]} for r in rows if r.get("title")])
    ctxt = "\n".join(f"- 组{i+1}: " + " / ".join(x["key"] for x in g)
                     for i, g in enumerate(groups)) or "（无）"
    hist = {}
    for r in _news.query_news(date_from=date, date_to=date, status="active", limit=500):
        if not (r.get("title") or "").strip():
            continue
        cs = _rel.find_related(r["key"], r["title"], limit=hist_k)
        if cs:
            hist[r["key"][:12]] = [{"k": c["key"][:12], "day": c["day"],
                                    "t": (c["title"] or "")[:26]} for c in cs]
    return {"rows": rows, "prev": prev, "clusters": ctxt, "hist": hist}


def _resolve_keys(prefixes):
    import sqlite3
    out = []
    conn = sqlite3.connect(paths.sqlite_path())
    try:
        for pfx in prefixes or []:
            r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (str(pfx)[:40],)).fetchone()
            if r:
                out.append(r[0])
    finally:
        conn.close()
    return out


def _extract_related_json(text):
    """取 ```json {"same_event":{...},"timeline":{...}}``` → (same_event, timeline, 去块文本)。"""
    import re
    if not text:
        return None, None, text
    for mm in re.finditer(r"```json\s*(\{.*?\})\s*```", text, re.S):
        try:
            d = json.loads(mm.group(1))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict) and ("same_event" in d or "timeline" in d or "related" in d):
            se = d.get("same_event") or d.get("related") or {}
            tl = d.get("timeline") or {}
            return se, tl, text[:mm.start()] + text[mm.end():]
    return None, None, text


def _normalize_mapping(mapping):
    """{key前缀:[前缀...]} → {fullkey:"fullkey,fullkey"}（连通分量展开，保证互指）。"""
    adj = {}
    for kp, sibs in (mapping or {}).items():
        a = _resolve_keys([kp])
        if not a:
            continue
        a = a[0]
        adj.setdefault(a, set())
        for sp in sibs or []:
            b = _resolve_keys([sp])
            if b and b[0] != a:
                adj[a].add(b[0])
                adj.setdefault(b[0], set()).add(a)
    seen, comps = set(), []
    for n in adj:
        if n in seen:
            continue
        stack, comp = [n], set()
        while stack:
            x = stack.pop()
            if x in comp:
                continue
            comp.add(x); seen.add(x)
            stack.extend(y for y in adj.get(x, ()) if y not in comp)
        if len(comp) >= 2:
            comps.append(comp)
    out = {}
    for comp in comps:
        for x in comp:
            out[x] = ",".join(sorted(y for y in comp if y != x))
    return out


def persist_related_mapping(same_event, timeline=None, day_keys=None):
    """**只写/清当日行**（历史行不碰）。

    - same_event → related_keys（规范化互指；可含历史 key，即当日稿指向前作）；
    - timeline   → timeline_keys（单向即可，不做合并）；
    - 当日行未被列入 → 清空对应字段。
    返回 (same_event 规范化 dict, related 写入篇数, related 清空篇数, timeline 写入篇数)。
    """
    norm = _normalize_mapping(same_event or {})
    dayset = set(day_keys) if day_keys is not None else None
    wrote = tlw = cleared = 0
    for k, v in norm.items():
        if dayset is not None and k not in dayset:
            continue
        _news.update_news(k, {"related_keys": v})
        wrote += 1
    tlnorm = {}
    for kp, sibs in (timeline or {}).items():
        kf = _resolve_keys([kp])
        if not kf:
            continue
        if dayset is not None and kf[0] not in dayset:
            continue
        sib = list(dict.fromkeys(x for x in _resolve_keys(sibs) if x and x != kf[0]))
        if sib:
            tlnorm[kf[0]] = ",".join(sib)
    for k, v in tlnorm.items():
        _news.update_news(k, {"timeline_keys": v})
        tlw += 1
    for k in (day_keys or []):
        cur = _news.get_by_key(k) or {}
        if k not in norm and (cur.get("related_keys") or ""):
            _news.update_news(k, {"related_keys": ""})
            cleared += 1
        if k not in tlnorm and (cur.get("timeline_keys") or ""):
            _news.update_news(k, {"timeline_keys": ""})
    return norm, wrote, cleared, tlw


def persist_related_mechanical(date):
    """机械兜底：LLM 未给关联 JSON 时，用**当日**同事件聚类写入 related_keys。"""
    from services import cluster as _cl
    rows = _news.query_news(date_from=date, date_to=date, status="active", limit=500)
    items = [{"key": r["key"], "title": r.get("title") or ""} for r in rows if (r.get("title") or "").strip()]
    groups = _cl.cluster(items)
    changed = 0
    for g in groups:
        ids = [x["key"] for x in g]
        for x in g:
            sib = [k for k in ids if k != x["key"]]
            if sib:
                _news.update_news(x["key"], {"related_keys": ",".join(sib)})
                changed += 1
    return len(groups), changed


def build_messages(date, pkg):
    system = _read("agent/prompts/review.md")
    user = (f"日期：{date}（东京时间）。\n\n"
            f"=== 当日全量素材（JSON）===\n{json.dumps(pkg['rows'], ensure_ascii=False)}\n\n"
            f"=== 前一天素材（JSON，用于日期陷阱）===\n{json.dumps(pkg['prev'], ensure_ascii=False)}\n\n"
            f"=== 当日同事件机械候选（同日；供第三节参考，可修正）===\n{pkg['clusters']}\n\n"
            f"=== 当日稿的全历史关联候选（无时间窗口，按相关性排序；供关联判定）===\n"
            f"{json.dumps(pkg['hist'], ensure_ascii=False)}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run(date, deliver=False, name=None, max_tokens=20000):
    from services.review_archive import build_archive
    pkg = prepare_package(date)
    part1 = build_archive(date, single_table=True)          # 一、单张合并表
    out = llm.chat(build_messages(date, pkg), max_tokens=max_tokens)   # 二~六 + 关联JSON
    se, tl, out = _extract_related_json(out)
    try:
        if se or tl:
            day_keys = [r["key"] for r in _news.query_news(date_from=date, date_to=date, status="active", limit=500)]
            _norm, nc, nclr, ntl = persist_related_mapping(se or {}, tl or {}, day_keys=day_keys)
            print(f"[related] 当日写入：同事件 {nc} 篇 / 时间线 {ntl} 篇 / 清空 {nclr} 篇（历史不动）")
        else:
            ng, nc = persist_related_mechanical(date)
            print(f"[related] 无 LLM 关联JSON，机械兜底 {ng} 组 → 写入 {nc} 篇")
    except Exception as e:  # noqa: BLE001
        print(f"[related] 跳过: {e}")
    rest = out
    # 裁掉 LLM 可能多输出的 H1/元信息/「一、」（已由系统生成），只保留从「## 二、」起
    idx = rest.find("## 二、")
    if idx > 0:
        rest = rest[idx:]
    md = part1.rstrip() + "\n\n---\n\n" + rest.lstrip()
    if deliver:
        from services.delivery import deliver as _d
        print("[delivery]", _d(md, name=name or f"{date}-review-full.md"))
    else:
        print(md)
    return md


def main(argv=None):
    import argparse
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--deliver", action="store_true")
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    run(a.date, a.deliver, a.name)
    return 0
