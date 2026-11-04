#!/usr/bin/env python3
"""双跑日报：对比生产库与容器库同一天的写稿产出（只读，不改任何数据）。

用法：
    python ops/dual_run_report.py                  # 昨天
    python ops/dual_run_report.py --date 2026-11-04
    python ops/dual_run_report.py --days 7         # 近 7 天趋势

输出：~/PG/xhs-storage/logs/dual_run/YYYY-MM-DD.md
"""
import argparse
import datetime as dt
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from services.word_count import content_len, title_len  # noqa: E402

PROD_DB = os.path.expanduser("~/PG/XiaohongshuSkills/data/news_dev.db")
CONT_DB = os.path.expanduser("~/PG/xhs-storage/data/news_dev.db")
OUT_DIR = os.path.expanduser("~/PG/xhs-storage/logs/dual_run")
BATCH_DIR = os.path.expanduser("~/PG/xhs-storage/data/write_batches")

KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
CONTRAST_RE = re.compile(r"却|但|反而|一边|另一边|反差|对比|争议|VS|vs")
DIMS = ("爆发点", "情绪价值", "信息增量", "内容深度", "标题质量")


def batch_keys(date):
    """容器当日实际处理的 key（write_batches manifest；没有则返回 None）。"""
    p = Path(BATCH_DIR) / f"{date}.json"
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return [x["key"] for x in (d.get("processed") or []) if x.get("ok", True)]


def connect(path):
    if not os.path.exists(path):
        return None
    return sqlite3.connect(f"file:{path}?mode=ro", uri=True)


def drafts(db, date, keys=None):
    """当日成稿。

    keys 非空：按 batch manifest 精确取（容器）；
    否则：按 updated_at 取（生产；含 related_keys 更新，仅作参考）。
    """
    if db is None:
        return {}
    cols = ("SELECT key, COALESCE(rewritten_title,''), COALESCE(rewritten_content,''), "
            "COALESCE(content_ja,''), COALESCE(format,''), COALESCE(publish_mode,'') FROM news ")
    if keys:
        out = {}
        for i in range(0, len(keys), 400):
            chunk = keys[i:i + 400]
            cur = db.execute(cols + "WHERE key IN (%s)" % ",".join("?" * len(chunk)), chunk)
            for r in cur:
                if r[2]:
                    out[r[0]] = {"title": r[1], "body": r[2], "ja": r[3], "fmt": r[4], "mode": r[5]}
        return out
    cur = db.execute(
        cols + "WHERE COALESCE(rewritten_content,'') <> '' "
        "AND date(COALESCE(updated_at, created_at)) = ?", (date,))
    return {r[0]: {"title": r[1], "body": r[2], "ja": r[3], "fmt": r[4], "mode": r[5]}
            for r in cur}


def candidates(db, date):
    if db is None:
        return 0
    return db.execute("SELECT count(*) FROM news WHERE date(created_at)=?", (date,)).fetchone()[0]


def total_scores(db, keys, column):
    """按各自的总分列取分（生产 content_score 0-3.x；容器 write_score 0-10）。"""
    if db is None or not keys:
        return {}
    out = {}
    keys = list(keys)
    for i in range(0, len(keys), 400):
        chunk = keys[i:i + 400]
        q = ("SELECT key, COALESCE(%s,0) FROM news WHERE key IN (%s)" %
             (column, ",".join("?" * len(chunk))))
        for k, v in db.execute(q, chunk):
            out[k] = float(v or 0)
    return out


def body_metrics(rows):
    vals = [r for r in rows.values() if r["body"]]
    n = len(vals)
    if not n:
        return {}
    dens = [content_len(r["body"]) / content_len(r["ja"]) * 100
            for r in vals if content_len(r["ja"])]
    return {
        "avg_body": sum(content_len(r["body"]) for r in vals) / n,
        "avg_density": (sum(dens) / len(dens)) if dens else None,
        "kana_rows": sum(1 for r in vals if KANA_RE.search(r["body"])),
        "dunhao_rows": sum(1 for r in vals
                           if any(l.count("、") >= 2 for l in r["body"].split("\n"))),
        "h2_missing": sum(1 for r in vals
                          if r["fmt"] == "story" and sum(1 for l in r["body"].split("\n")
                                                         if l.startswith("## ")) < 2),
    }


def title_metrics(rows):
    vals = [r for r in rows.values() if (r["title"] or "").strip()]
    if not vals:
        return {}
    return {
        "n": len(vals),
        "empty": sum(1 for r in rows.values() if not (r["title"] or "").strip()),
        "avg_len": sum(title_len(r["title"]) for r in vals) / len(vals),
        "over20": sum(1 for r in vals if title_len(r["title"]) > 20),
        "contrast": sum(1 for r in vals if CONTRAST_RE.search(r["title"])),
    }


def fmt_pct(a, b):
    return f"{a}/{b} ({a / b * 100:.0f}%)" if b else f"{a}/0"


def report(date):
    prod = connect(PROD_DB)
    cont = connect(CONT_DB)
    ckeys = batch_keys(date)
    pd, cd = drafts(prod, date), drafts(cont, date, ckeys)
    pset, cset = set(pd), set(cd)
    both = pset & cset

    lines = [f"# 双跑日报 {date}", ""]
    lines += ["## 覆盖", "",
              "| 指标 | 生产 | 容器 |",
              "|---|---|---|",
              f"| 当日入库 | {candidates(prod, date)} | {candidates(cont, date)} |",
              f"| 当日成稿 | {len(pd)} | {len(cd)} |",
              f"| 两边都写 | {len(both)} | — |",
              f"| 仅生产写 | {len(pset - cset)} | — |",
              f"| 仅容器写 | — | {len(cset - pset)} |",
              "",
              "> 容器口径 = `write_batches/<date>.json`（实际处理清单）；"
              "生产无 manifest，按 `updated_at` 统计，会含 related_keys 更新。",
              ""]

    # 标题
    pt, ct = title_metrics(pd), title_metrics(cd)
    lines += ["## 标题", "",
              "| 指标 | 生产 | 容器 |",
              "|---|---|---|",
              f"| 平均字数 | {pt.get('avg_len', 0):.1f} | {ct.get('avg_len', 0):.1f} |",
              f"| >20 字 | {pt.get('over20', 0)} | {ct.get('over20', 0)} |",
              f"| 空标题 | {pt.get('empty', 0)} | {ct.get('empty', 0)} |",
              f"| 对比/争议 | {fmt_pct(pt.get('contrast', 0), pt.get('n', 0))} | "
              f"{fmt_pct(ct.get('contrast', 0), ct.get('n', 0))} |",
              ""]

    # 正文
    pb, cb = body_metrics(pd), body_metrics(cd)
    pdens = f"{pb['avg_density']:.1f}%" if pb.get("avg_density") is not None else "-"
    cdens = f"{cb['avg_density']:.1f}%" if cb.get("avg_density") is not None else "-"
    lines += ["## 正文", "",
              "| 指标 | 生产 | 容器 |",
              "|---|---|---|",
              f"| 平均字数 | {pb.get('avg_body', 0):.0f} | {cb.get('avg_body', 0):.0f} |",
              f"| 平均密度 | {pdens} | {cdens} |",
              f"| 假名残留篇数 | {pb.get('kana_rows', 0)} | {cb.get('kana_rows', 0)} |",
              f"| 顿号行篇数 | {pb.get('dunhao_rows', 0)} | {cb.get('dunhao_rows', 0)} |",
              f"| story 缺 ## | {pb.get('h2_missing', 0)} | {cb.get('h2_missing', 0)} |",
              ""]

    # 评分（口径不同：生产 content_score≈0-3.x；容器 write_score=0-10，只能各自看趋势）
    ps = total_scores(prod, pset, "content_score")
    cs = total_scores(cont, cset, "write_score")
    psv = [v for v in ps.values() if v > 0]
    csv = [v for v in cs.values() if v > 0]
    lines += ["## 评分（口径不同，不可横向比较）", "",
              "| 指标 | 生产 content_score | 容器 write_score |",
              "|---|---|---|",
              f"| 有评分篇数 | {len(psv)} | {len(csv)} |",
              f"| 平均 | {(sum(psv)/len(psv) if psv else 0):.2f} | {(sum(csv)/len(csv) if csv else 0):.2f} |",
              f"| story<阈值 | - | "
              f"{sum(1 for k, v in cs.items() if cd.get(k, {}).get('fmt') == 'story' and 0 < v < 8)} (<8) |",
              "",
              "> 生产侧没有 5 维总分列；质量对比以每周盲评为准。", ""]

    # 交集对比
    if both:
        diffs = []
        for k in both:
            a, b = pd[k], cd[k]
            diffs.append((k, title_len(a["title"]), title_len(b["title"]),
                          content_len(a["body"]), content_len(b["body"])))
        lines += ["## 交集抽样（前 5）", "",
                  "| key | 生产标题字数 | 容器标题字数 | 生产正文 | 容器正文 |",
                  "|---|---|---|---|---|"]
        for k, at, bt, ab, bb in diffs[:5]:
            lines.append(f"| {k[:12]} | {at} | {bt} | {ab} | {bb} |")
        lines.append("")

    # 容器运行状况
    if cont is not None:
        fails = dict(cont.execute(
            "SELECT COALESCE(status,''), count(*) FROM write_failures "
            "WHERE date(created_at)=? GROUP BY 1", (date,)).fetchall())
        tok = cont.execute("SELECT COALESCE(SUM(total_tokens),0) FROM token_usage "
                           "WHERE date(ts)=?", (date,)).fetchone()[0]
        lines += ["## 容器运行状况", "",
                  f"- write_failures：{fails or '无'}",
                  f"- token 用量：{tok:,}",
                  ""]

    # 异常提示
    warns = []
    if ct.get("over20"):
        warns.append(f"容器有 {ct['over20']} 条标题 >20 字")
    if ct.get("empty"):
        warns.append(f"容器有 {ct['empty']} 条空标题")
    if cb.get("h2_missing"):
        warns.append(f"容器有 {cb['h2_missing']} 篇 story 缺 ##")
    if cb.get("dunhao_rows"):
        warns.append(f"容器有 {cb['dunhao_rows']} 篇顿号行")
    lines += ["## 异常", ""] + ([f"- {w}" for w in warns] if warns else ["- 无"]) + [""]

    if prod is not None:
        prod.close()
    if cont is not None:
        cont.close()
    return "\n".join(lines)


def trend(days):
    lines = ["# 双跑趋势", "",
             "| 日期 | 生产成稿 | 容器成稿 | 交集 | 容器 >20字 | 容器空标题 |",
             "|---|---|---|---|---|---|"]
    prod = connect(PROD_DB)
    cont = connect(CONT_DB)
    today = dt.date.today()
    for i in range(days - 1, -1, -1):
        d = (today - dt.timedelta(days=i)).isoformat()
        pd, cd = drafts(prod, d), drafts(cont, d)
        ct = title_metrics(cd)
        lines.append(f"| {d} | {len(pd)} | {len(cd)} | {len(set(pd) & set(cd))} | "
                     f"{ct.get('over20', 0)} | {ct.get('empty', 0)} |")
    if prod is not None:
        prod.close()
    if cont is not None:
        cont.close()
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    ap.add_argument("--days", type=int, default=0)
    ap.add_argument("--out-dir", default=OUT_DIR)
    a = ap.parse_args(argv)

    if a.days:
        text = trend(a.days)
        out = Path(a.out_dir) / f"trend-{dt.date.today().isoformat()}.md"
    else:
        date = a.date or (dt.date.today() - dt.timedelta(days=1)).isoformat()
        text = report(date)
        out = Path(a.out_dir) / f"{date}.md"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"\n写入 {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
