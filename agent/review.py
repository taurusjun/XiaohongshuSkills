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


def build_messages(date, rows):
    skills = "\n\n".join(f"===== {f} =====\n" + _read(f) for f in SKILL_FILES)
    system = ("你是小红书「每日素材 review」的执行者。严格按 SKILL 的四层流程执行。"
              "最终**只输出一份完整的 Markdown 存档**（一~六节），格式严格对齐历史存档；"
              "不要输出任何解释文字、不要输出 JSON、不要输出工具调用语法。\n\n" + skills)
    user = (f"日期：{date}（东京时间）。当日全量素材（JSON；判断以 title 为准）：\n"
            f"{json.dumps(rows, ensure_ascii=False)}\n\n"
            "**只输出下面 5 个章节**（不要 H1、不要元信息块、不要「一、全量素材一览」——它已由系统生成）：\n"
            "## 二、前一天日期陷阱检查\n## 三、跨来源聚类分析\n"
            "## 四、分级结果\n## 五、第2层价值建议 + 第3层跨时间关联\n## 六、第4层发布数据回顾")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run(date, deliver=False, name=None, max_tokens=20000):
    from services.review_archive import build_archive
    rows = compact_rows(date)
    part1 = build_archive(date, single_table=True)          # 一、单张合并表
    rest = llm.chat(build_messages(date, rows), max_tokens=max_tokens)  # 二~六
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
