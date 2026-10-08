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


def prepare_package(date):
    """review 阶段1 写前准备（机械）：当日全量 + 前一天 + 机械聚类线索。"""
    import datetime as dt
    from services import cluster as _cl
    rows = compact_rows(date)
    try:
        prev_date = (dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat()
        prev = compact_rows(prev_date)
    except Exception:
        prev = []
    groups = _cl.cluster([{"key": r["key"], "title": r["title"]} for r in rows if r.get("title")])
    ctxt = "\n".join(f"- 组{i+1}: " + " / ".join(x["key"][:12] for x in g)
                     for i, g in enumerate(groups)) or "（无）"
    return {"rows": rows, "prev": prev, "clusters": ctxt}


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


def persist_related_mapping(mapping, clear_keys=None):
    """review 前置（LLM 确认版）：写入 {key前缀:[同事件key前缀,...]}（覆盖、去自指/去重）；
    clear_keys 里「当日」且未被 LLM 列入的行 → 清空 related_keys（纠正旧机械误连）。"""
    changed = seen_cleared = 0
    seen = set()
    for kp, sibs in (mapping or {}).items():
        kfull = _resolve_keys([kp])
        if not kfull:
            continue
        seen.add(kfull[0])
        sibfull = [x for x in _resolve_keys(sibs) if x and x != kfull[0]]
        sibfull = list(dict.fromkeys(sibfull))
        _news.update_news(kfull[0], {"related_keys": ",".join(sibfull)})
        changed += 1
    for k in (clear_keys or []):
        if k not in seen and (_news.get_by_key(k) or {}).get("related_keys"):
            _news.update_news(k, {"related_keys": ""})
            seen_cleared += 1
    return len(mapping or {}), changed, seen_cleared


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
            f"=== 机械聚类候选（token 重叠，供第三节参考，可修正）===\n{pkg['clusters']}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]



def run(date, deliver=False, name=None, max_tokens=20000):
    from services.review_archive import build_archive
    pkg = prepare_package(date)
    part1 = build_archive(date, single_table=True)          # 一、单张合并表
    out = llm.chat(build_messages(date, pkg), max_tokens=max_tokens)   # 二~六 + 关联JSON
    mapping, out = _extract_related_json(out)
    try:
        if mapping:
            scope = [r["key"] for r in _news.query_news(date_from=date, date_to=date, status="active", limit=500)]
            nm, nc, nclr = persist_related_mapping(mapping, clear_keys=scope)
            print(f"[related] LLM 确认 {nm} 组 → 写入 {nc} 篇 / 清空旧关联 {nclr} 篇")
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
