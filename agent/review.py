"""完整 review skill 运行：4 层 SKILL + 当日素材 → LLM 生成完整存档（一~六）+ 关联写库。

关联（对齐原 skill）：
- 先按**实体名**（人物/团体/作品/系列）检索**全历史**候选（无时间窗口，非 n-gram）；
- LLM 判定 → 写入**当日行**的 `related_keys`（历史行不碰）。
"""
import json
import re
import sys

from agent import llm
from services import news as _news, paths, related as _rel

SKILL_FILES = [
    "skills/creative/xhs-daily-material-review/SKILL.md",
    "skills/creative/xhs-daily-material-review-layer1/SKILL.md",
    "skills/xhs-daily-material-review-layer23/SKILL.md",
    "skills/xhs-daily-material-review-layer4/SKILL.md",
]

ENT_SYS = ("你是日娱实体抽取器。给每条素材(title=中文标题, title_ja=日文原标题)，抽取**用于检索的实体**："
           "人物名/团体名/作品名/系列名，用**日文原始写法**（title_ja 中的），以避开简繁差异；"
           "不要把“古民家/剪去长发/唱的是/移籍/写真/演唱会/评论区/自拍”等描述性词当实体。"
           '只输出严格 JSON：{"<key前12位>": ["实体1","实体2", ...]}。')

__all__ = ["compact_rows", "prepare_package", "extract_entities", "persist_related", "run", "main"]


def _read(rel):
    p = paths.REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def compact_rows(date, limit=500):
    rows = _news.query_news(date_from=date, date_to=date, status="active", limit=limit)
    return [{"key": r["key"][:12], "title": r.get("title"), "ja": (r.get("title_ja") or "")[:40],
             "fmt": r.get("format"), "lf": r.get("is_long_form"), "ts": r.get("title_score"),
             "cs": r.get("content_score"), "cj": len(r.get("content_ja") or ""),
             "src": r.get("fetch_by"), "pub": r.get("publish_xhs")} for r in rows]


def extract_entities(items, max_tokens=4000):
    """items:[{key,title,ja}] → {key12: [实体,...]}（LLM 抽取，日文原始写法优先）。"""
    if not items:
        return {}
    try:
        raw = llm.chat([{"role": "system", "content": ENT_SYS},
                        {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
                       max_tokens=max_tokens)
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001
        return {}


def prepare_package(date, hist_k=6):
    """review 阶段1：当日全量 + 前一天(日期陷阱) + 每个当日稿的**全历史实体候选**。"""
    import datetime as dt
    rows = compact_rows(date)
    try:
        prev = compact_rows((dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat())
    except Exception:  # noqa: BLE001
        prev = []
    full = _news.query_news(date_from=date, date_to=date, status="active", limit=500)
    ent = extract_entities([{"key": r["key"][:12], "title": r.get("title") or "",
                             "ja": (r.get("title_ja") or "")[:60]}
                            for r in full if (r.get("title") or "").strip()])
    hist = {}
    for r in full:
        if not (r.get("title") or "").strip():
            continue
        cs = _rel.find_related(r["key"], ent.get(r["key"][:12]) or [], limit=hist_k)
        if cs:
            hist[r["key"][:12]] = [{"k": c["key"][:12], "day": c["day"], "t": (c["title"] or "")[:26]}
                                   for c in cs]
    return {"rows": rows, "prev": prev, "hist": hist, "ent": ent}


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
    """取输末 ```json {"related":{...}}``` → (mapping, 去块文本)。兼容 same_event 旧格式。"""
    if not text:
        return None, text
    for mm in re.finditer(r"```json\s*(\{.*?\})\s*```", text, re.S):
        try:
            d = json.loads(mm.group(1))
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(d, dict):
            continue
        mp = d.get("related")
        if mp is None and ("same_event" in d or "timeline" in d):     # 兼容旧格式
            mp = {}
            for k, v in (d.get("same_event") or {}).items():
                mp.setdefault(k, [])
                mp[k] += v
        if isinstance(mp, dict):
            return mp, text[:mm.start()] + text[mm.end():]
    return None, text


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


def persist_related(mapping, day_keys=None):
    """**只写/清当日行**（历史行不碰）。返回 (规范化映射, 写入篇数, 清空篇数)。"""
    norm = _normalize_mapping(mapping or {})
    dayset = set(day_keys) if day_keys is not None else None
    wrote = cleared = 0
    for k, v in norm.items():
        if dayset is not None and k not in dayset:
            continue
        _news.update_news(k, {"related_keys": v})
        wrote += 1
    for k in (day_keys or []):
        if k not in norm and (_news.get_by_key(k) or {}).get("related_keys"):
            _news.update_news(k, {"related_keys": ""})
            cleared += 1
    return norm, wrote, cleared


def build_messages(date, pkg):
    system = _read("agent/prompts/review.md")
    user = (f"日期：{date}（东京时间）。\n\n"
            f"=== 当日全量素材（JSON）===\n{json.dumps(pkg['rows'], ensure_ascii=False)}\n\n"
            f"=== 前一天素材（JSON，用于日期陷阱）===\n{json.dumps(pkg['prev'], ensure_ascii=False)}\n\n"
            f"=== 当日稿的关联候选（按**实体名**检索的全历史，无时间窗口）===\n"
            f"{json.dumps(pkg['hist'], ensure_ascii=False)}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run(date, deliver=False, name=None, max_tokens=20000):
    from services.review_archive import build_archive
    pkg = prepare_package(date)
    part1 = build_archive(date, single_table=True)          # 一、单张合并表
    out = llm.chat(build_messages(date, pkg), max_tokens=max_tokens)   # 二~六 + 关联JSON
    mapping, out = _extract_related_json(out)
    try:
        if mapping:
            day_keys = [r["key"] for r in _news.query_news(date_from=date, date_to=date, status="active", limit=500)]
            _norm, nc, nclr = persist_related(mapping, day_keys=day_keys)
            print(f"[related] 当日写入 {nc} 篇 / 清空 {nclr} 篇（历史不动；实体检索候选）")
        else:
            print("[related] 无 LLM 关联 JSON，跳过写库")
    except Exception as e:  # noqa: BLE001
        print(f"[related] 跳过: {e}")
    rest = out
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
