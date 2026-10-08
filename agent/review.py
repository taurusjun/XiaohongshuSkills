"""完整 review skill 运行：4 层 SKILL + 当日素材 → LLM 生成完整存档（一~六）。"""
import json
import sys

from agent import llm
from services import news as _news, paths

SKILL_FILES = [
    "skills/creative/xhs-daily-material-review/SKILL.md",
    "skills/creative/xhs-daily-material-review-layer1/SKILL.md",
    "skills/xhs-daily-material-review-layer23/SKILL.md",
    "skills/xhs-daily-material-review-layer4/SKILL.md",
]

__all__ = ["compact_rows", "prepare_package", "persist_related_mapping", "persist_related_mechanical", "clear_related_pointing_to",
           "enforce_symmetric_related", "run", "main"]


def _read(rel):
    p = paths.REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def compact_rows(date, limit=500):
    rows = _news.query_news(date_from=date, date_to=date, status="active", limit=limit)
    return [{"key": r["key"][:12], "title": r.get("title"), "fmt": r.get("format"),
             "lf": r.get("is_long_form"), "ts": r.get("title_score"), "cs": r.get("content_score"),
             "cj": len(r.get("content_ja") or ""), "src": r.get("fetch_by"),
             "pub": r.get("publish_xhs")} for r in rows]


def prepare_package(date, gap=3):
    """review 阶段1 写前准备（机械）：当日全量 + 前一天 + 近期(前2~3日,供关联) + 机械聚类线索。

    关联视野覆盖 [date-gap, date]，使 review 对该窗口的 related_keys 完整负责。
    """
    import datetime as dt
    from services import cluster as _cl
    rows = compact_rows(date)
    prev, recent = [], []
    try:
        for dd in range(1, gap + 1):
            d2 = (dt.date.fromisoformat(date) - dt.timedelta(days=dd)).isoformat()
            (prev if dd == 1 else recent).extend(compact_rows(d2))
    except Exception:  # noqa: BLE001
        pass
    pool = [{"key": r["key"], "title": r["title"]} for r in (rows + prev + recent) if r.get("title")]
    groups = _cl.cluster(pool)
    ctxt = "\n".join(f"- 组{i+1}: " + " / ".join(x["key"][:12] for x in g)
                     for i, g in enumerate(groups)) or "（无）"
    return {"rows": rows, "prev": prev, "recent": recent, "clusters": ctxt, "gap": gap}


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
    """取 LLM 输末的 ```json {"related":{...}}``` 块 → (mapping, 去掉该块后的文本)。"""
    import re
    if not text:
        return None, text
    for mm in re.finditer(r"```json\s*(\{.*?\})\s*```", text, re.S):
        try:
            d = json.loads(mm.group(1))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(d, dict) and "related" in d:
            return (d.get("related") or {}), text[:mm.start()] + text[mm.end():]
    return None, text


def _normalize_mapping(mapping):
    """{key前缀:[前缀...]} → {fullkey:[fullkey...]}（连通分量展开，保证互指）。"""
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


def persist_related_mapping(mapping, clear_keys=None):
    """review 前置（LLM 确认版）：规范化(→互指)后覆盖写入；clear_keys 内未列入的行清空（反旧误连）。

    返回 (规范化映射 dict, 写入篇数, 清空篇数)。
    """
    norm = _normalize_mapping(mapping)
    for k, v in norm.items():
        _news.update_news(k, {"related_keys": v})
    cleared = 0
    for k in (clear_keys or []):
        if k not in norm and (_news.get_by_key(k) or {}).get("related_keys"):
            _news.update_news(k, {"related_keys": ""})
            cleared += 1
    return norm, len(norm), cleared


def clear_related_pointing_to(involved, keep=()):
    """任何 related_keys 指向 involved（本次 review 涉及 key）且不在 keep 的行 → 清空其关联。

    用于清除窗口之外的旧误连（如机械写坏的跨日行）。
    """
    import sqlite3
    inv, keep = set(involved), set(keep)
    conn = sqlite3.connect(paths.sqlite_path())
    try:
        rows = conn.execute("SELECT key,related_keys FROM news "
                            "WHERE related_keys IS NOT NULL AND related_keys!=''").fetchall()
    finally:
        conn.close()
    cleared = 0
    for k, rk in rows:
        if k in keep:
            continue
        if inv & {x for x in (rk or "").split(",") if x}:
            _news.update_news(k, {"related_keys": ""})
            cleared += 1
    return cleared


def enforce_symmetric_related():
    """全局不变量：related_keys 必须互指（原 skill「关联素材回指主稿」）；去单向/悬空。"""
    import sqlite3
    conn = sqlite3.connect(paths.sqlite_path())
    try:
        rows = conn.execute("SELECT key,related_keys FROM news "
                            "WHERE related_keys IS NOT NULL AND related_keys!=''").fetchall()
    finally:
        conn.close()
    m = {k: [x for x in (rk or "").split(",") if x] for k, rk in rows}
    changed = 0
    for k, sibs in m.items():
        keep = [x for x in sibs if x in m and k in m.get(x, [])]
        if keep != sibs:
            _news.update_news(k, {"related_keys": ",".join(keep)})
            changed += 1
    return changed


def persist_related_mechanical(date, gap=3):
    """机械兜底：LLM 未给关联 JSON 时，用近期窗口同事件聚类写入。"""
    import datetime as dt
    from services import cluster as _cl
    try:
        lo = (dt.date.fromisoformat(date) - dt.timedelta(days=gap)).isoformat()
    except Exception:  # noqa: BLE001
        lo = date
    rows = _news.query_news(date_from=lo, date_to=date, status="active", limit=800)
    items = [{"key": r["key"], "title": r.get("title") or "",
              "day": (r.get("created_at") or "")[:10]} for r in rows if (r.get("title") or "").strip()]
    groups = _cl.cluster(items, date_field="day", max_gap_days=gap)
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
            f"=== 近期素材（前2~3日，供关联判断，不必分级）===\n{json.dumps(pkg.get('recent', []), ensure_ascii=False)}\n\n"
            f"=== 机械聚类候选（token 重叠，供第三节参考，可修正；含全部近期素材）===\n{pkg['clusters']}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]



def run(date, deliver=False, name=None, max_tokens=20000):
    from services.review_archive import build_archive
    pkg = prepare_package(date)
    part1 = build_archive(date, single_table=True)          # 一、单张合并表
    out = llm.chat(build_messages(date, pkg), max_tokens=max_tokens)   # 二~六 + 关联JSON
    mapping, out = _extract_related_json(out)
    try:
        if mapping:
            import datetime as dt
            lo = (dt.date.fromisoformat(date) - dt.timedelta(days=pkg.get("gap", 3))).isoformat()
            scope = [r["key"] for r in _news.query_news(date_from=lo, date_to=date, status="active", limit=1000)]
            norm, nc, nclr = persist_related_mapping(mapping, clear_keys=scope)
            involved = set(scope) | set(norm)
            for _kp, _sibs in (mapping or {}).items():
                involved.update(_resolve_keys([_kp]))
                involved.update(_resolve_keys(_sibs))
            npt = clear_related_pointing_to(involved, keep=set(norm))
            nfix = enforce_symmetric_related()
            print(f"[related] LLM 确认 {nc} 篇 → 窗口清空 {nclr} / 指向清空 {npt} / 互指校正 {nfix}")
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
