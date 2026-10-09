"""完整 review skill 运行：4 层 SKILL + 当日素材 → LLM 生成完整存档（一~六）。

忠于原 skill：
- review **只产出文档**，不做任何 DB 写入（layer23 明确「禁止写入操作」）。
- 「第五节 跨时间关联」：按**实体名**做只读检索（等价 layer23 的 search=），把候选喂给 LLM 写分析。
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

__all__ = ["compact_rows", "prepare_package", "extract_entities", "run", "main"]


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
    """review 阶段1：当日全量 + 前一天(日期陷阱) + 只读的实体名历史候选(供第五节)。"""
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
    return {"rows": rows, "prev": prev, "hist": hist}


def build_messages(date, pkg):
    system = _read("agent/prompts/review.md")
    user = (f"日期：{date}（东京时间）。\n\n"
            f"=== 当日全量素材（JSON）===\n{json.dumps(pkg['rows'], ensure_ascii=False)}\n\n"
            f"=== 前一天素材（JSON，用于日期陷阱）===\n{json.dumps(pkg['prev'], ensure_ascii=False)}\n\n"
            f"=== 第五节参考：按实体名检索的全历史候选（**只读**，供跨时间关联分析）===\n"
            f"{json.dumps(pkg['hist'], ensure_ascii=False)}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run(date, deliver=False, name=None, max_tokens=20000):
    from services.review_archive import build_archive
    pkg = prepare_package(date)
    part1 = build_archive(date, single_table=True)          # 一、单张合并表
    rest = llm.chat(build_messages(date, pkg), max_tokens=max_tokens)  # 二~六
    idx = rest.find("## 二、")
    if idx > 0:
        rest = rest[idx:]
    md = part1.rstrip() + "\n\n---\n\n" + rest.lstrip()
    # 本地存档：**无条件**（write 阶段要读它；对齐原 skill 每日存档）
    from services.delivery import web_save
    web_save(md, name=name or f"{date}-review-full.md")
    if deliver:                                          # 推送：可选
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
