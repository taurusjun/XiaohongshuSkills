#!/usr/bin/env python3
"""dimension_analysis.py — 评分维度与实发数据的 Pearson 相关性分析"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime
import pandas as pd
import numpy as np
from scipy.stats import pearsonr


def load_analysis_data(min_window: str = "72h") -> pd.DataFrame:
    """JOIN news + score_dims，过滤有实发数据且有评分的文章，PIVOT 为宽表。

    权重调整使用 LLM 原始分（s.value），因为权重在运行时作用于 LLM 的打分，
    必须用同一套数据校准才有意义。
    min_window: 数据窗口下限，只纳入已达该成熟度的文章（"72h" 表示至少收集过72h数据）。
    """
    from scripts.sqlite_db import _connect
    with _connect() as db:
        rows = db.execute("""
            SELECT n.key, n.xhs_views, n.xhs_likes, n.xhs_saves, n.xhs_comments,
                   n.xhs_shares, n.xhs_fans_gained, n.xhs_impression, n.xhs_click_rate,
                   n.xhs_watch_time, n.xhs_danmaku,
                   s.dimension,
                   s.value as effective_value
            FROM news n
            JOIN score_dims s ON n.key = s.news_key
            WHERE n.xhs_views > 0
              AND n.status = 'active'
              AND (? = '' OR n.xhs_collected_at LIKE ?)
        """, (min_window, f"%{min_window}%")).fetchall()

    if not rows:
        return pd.DataFrame()

    records = [dict(r) for r in rows]
    df = pd.DataFrame(records)

    # PIVOT：每个维度变为一列
    pivot = df.pivot_table(
        index="key",
        columns="dimension",
        values="effective_value",
        aggfunc="first",
    )

    # 附加 xhs 指标（取每个 key 的第一行）
    metric_cols = ["xhs_views", "xhs_likes", "xhs_saves", "xhs_comments",
                   "xhs_shares", "xhs_fans_gained", "xhs_impression", "xhs_click_rate",
                   "xhs_watch_time", "xhs_danmaku"]
    existing_cols = [c for c in metric_cols if c in df.columns]
    metrics = df.groupby("key")[existing_cols].first()
    result = pivot.join(metrics)
    return result


def compute_correlations(df: pd.DataFrame,
                         targets: tuple = ("xhs_saves", "xhs_comments", "xhs_views")) -> pd.DataFrame:
    """对每个维度列与每个 target 计算 Pearson r 和 p 值"""
    dim_cols = [c for c in df.columns if c not in targets
                and not c.startswith("xhs_")
                and c not in ("key", "effective_value")]
    records = []
    for dim in dim_cols:
        dim_data = df[dim].dropna()
        for target in targets:
            target_data = df[target].dropna()
            common_idx = dim_data.index.intersection(target_data.index)
            if len(common_idx) < 3:
                records.append({"dimension": dim, "target": target, "r": None, "p": None, "n": len(common_idx)})
                continue
            x = dim_data.loc[common_idx].astype(float)
            y = target_data.loc[common_idx].astype(float)
            if x.std() == 0 or y.std() == 0:
                records.append({"dimension": dim, "target": target, "r": 0.0, "p": 1.0, "n": len(common_idx)})
                continue
            r, p = pearsonr(x, y)
            records.append({"dimension": dim, "target": target, "r": r, "p": p, "n": len(common_idx)})
    return pd.DataFrame(records)


def format_report(corr_df: pd.DataFrame, dim_version: str = "", min_sample: int = 10) -> str:
    """格式化相关性报告"""
    lines = []
    today_str = datetime.now().strftime("%Y-%m-%d")
    version_str = f"v{dim_version}" if dim_version else "unknown"
    lines.append(f"评分维度相关性分析报告")
    lines.append(f"基于维度定义 {version_str}，生成于 {today_str}")
    lines.append("=" * 60)
    lines.append("")

    if corr_df.empty:
        lines.append(f"⚠️ 有效样本不足（需要至少 {min_sample} 条含 72h 数据的文章），无法生成可靠的相关性分析。")
        lines.append("请等待更多文章积累实发数据后再运行。")
        return "\n".join(lines)

    for target in ["xhs_saves", "xhs_comments", "xhs_views"]:
        subset = corr_df[corr_df["target"] == target].dropna(subset=["r"])
        if subset.empty:
            continue
        subset = subset.sort_values("r", key=abs, ascending=False)
        lines.append(f"## 与 {target} 的相关性")
        lines.append("")
        lines.append(f"{'维度':<12} {'r':>7} {'p':>7} {'n':>5} 显著性")
        lines.append("-" * 50)
        for _, row in subset.iterrows():
            sig = ""
            if row["p"] is not None:
                if row["p"] < 0.01:
                    sig = "**"
                elif row["p"] < 0.05:
                    sig = "*"
            lines.append(f"{row['dimension']:<12} {row['r']:>7.3f} {row['p']:>7.3f} {int(row['n']):>5}  {sig}")
        lines.append("")

    lines.append(f"*p<0.05  **p<0.01")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="评分维度相关性分析")
    p.add_argument("--targets", default="xhs_saves,xhs_comments,xhs_views",
                   help="目标变量，逗号分隔")
    p.add_argument("--output", default="", help="输出文件路径（空则打印到 stdout）")
    p.add_argument("--min-sample", type=int, default=10,
                   help="最少有效样本数")
    p.add_argument("--min-window", default="72h",
                   help="回收时间窗口标记（默认 72h）")
    args = p.parse_args()

    targets = tuple(t.strip() for t in args.targets.split(","))
    df = load_analysis_data(min_window=args.min_window)

    if df.empty or len(df) < args.min_sample:
        corr_df = pd.DataFrame()
    else:
        corr_df = compute_correlations(df, targets=targets)

    # 获取当前版本号
    dim_version = ""
    try:
        from scripts.sqlite_db import _connect
        with _connect() as db:
            row = db.execute(
                "SELECT version FROM scoring_dimension_versions WHERE is_active=1"
            ).fetchone()
            if row:
                dim_version = row["version"]
    except Exception:
        pass

    report = format_report(corr_df, dim_version=dim_version, min_sample=args.min_sample)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"报告已写入 {args.output}")
    else:
        print(report)
