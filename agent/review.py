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

__all__ = ["compact_rows", "run", "main"]


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
    rest = llm.chat(build_messages(date, pkg), max_tokens=max_tokens)  # 二~六
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
