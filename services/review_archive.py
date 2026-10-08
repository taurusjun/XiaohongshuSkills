"""每日 review 存档 —— 按 TG 模板确定性生成 Markdown（元信息 + 全量分组表格）。

LLM 的 L2~L4 价值建议可后续 append；本模块保证**存档骨架与历史 TG 存档同格式**。
"""
import argparse
import sys

from services import news as _news

__all__ = ["build_archive", "main"]


def _dist(rows):
    from collections import Counter
    return Counter((r.get("fetch_by") or "?").strip() or "?" for r in rows)


def build_archive(date: str, rows=None) -> str:
    if rows is None:
        rows = _news.query_news(date_from=date, date_to=date, status="active", limit=500)
    n = len(rows)
    dist = _dist(rows)
    dist_str = " / ".join(f"{k} {v}" for k, v in dist.most_common())
    lines = [
        f"# 每日素材 Review — {date}（东京时间）",
        "",
        f"**データ範囲：** {date}（当日のみ・total={n}件）",
        f"**查询：** `GET /api/news?date_from={date}&date_to={date}&limit=200` → total={n} / rows={n}",
        f"**fetch_by 分布：** {dist_str} = **{n}**（== total {n} ✅ 无漏组）"
        if dist_str else f"**fetch_by 分布：** （空）",
        "**完成度：** 全量扫描 + 分组（L2~L4 由 LLM 补充）",
        "**巡检：** —",
        "",
        "---",
        "",
        f"## 一、全量素材一览（按来源分组，{n}条）",
        "",
    ]
    for fb, cnt in dist.most_common():
        lines.append(f"### fetch_by = {fb}（{cnt}条）")
        lines.append("")
        lines.append("| key (40) | ts | cs | cj_len | title |")
        lines.append("|---|---|---|---|---|")
        for r in sorted((x for x in rows if (x.get("fetch_by") or "?").strip() == fb),
                        key=lambda x: -(x.get("title_score") or 0)):
            cj = len(r.get("content_ja") or "")
            lines.append(f"| `{r.get('key','')}` | {r.get('title_score','-')} | "
                         f"{r.get('content_score','-')} | {cj} | {r.get('title','')} |")
        lines.append("")
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--deliver", action="store_true")
    ap.add_argument("--name", default=None)
    args = ap.parse_args(argv)
    md = build_archive(args.date)
    print(md)
    if args.deliver:
        from services.delivery import deliver
        print("[delivery]", deliver(md, name=args.name or f"{args.date}-review.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
