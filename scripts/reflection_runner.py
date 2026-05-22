#!/usr/bin/env python3
"""
reflection_runner.py — 每周自动反思编排器

流程：
  1. 加载有完整72h数据的文章分析集
  2. 计算各评分维度与实发指标的 Pearson 相关性
  3. 生成 dim_weights 调整建议（幅度上限30%，范围0.1-3.0）
  4. 通过飞书推送周报卡片，等待运营者一键采纳或修改
  5. 输出 JSON 报告到 stdout

用法:
  python scripts/reflection_runner.py [--dry-run] [--min-samples 30]
"""

import sys
import os
import json
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [reflection] %(message)s")
logger = logging.getLogger("reflection_runner")

MIN_ABS_R = 0.10     # |r| 低于此值的维度不调整
MAX_ADJUST = 0.30    # 单次权重调整幅度上限 30%
W_MIN, W_MAX = 0.1, 3.0


def generate_weight_suggestions(correlations: dict, current_weights: dict) -> dict:
    """
    根据 Pearson r 计算建议权重。
    new_weight = clip(w * (1 + 0.3 * r), W_MIN, W_MAX)
    |r| < MIN_ABS_R 的维度保持不变。
    """
    suggestions = {}
    for dim, r in correlations.items():
        cur = current_weights.get(dim, 1.0)
        if abs(r) < MIN_ABS_R:
            suggestions[dim] = round(cur, 3)
        else:
            adjusted = cur * (1 + MAX_ADJUST * r)
            suggestions[dim] = round(max(W_MIN, min(W_MAX, adjusted)), 3)
    # Preserve weights for dims not in correlations
    for dim, w in current_weights.items():
        if dim not in suggestions:
            suggestions[dim] = round(w, 3)
    return suggestions


def run(dry_run: bool = False, min_samples: int = 30) -> dict:
    """执行完整反思流程，返回 JSON 报告 dict。"""
    from scripts.sqlite_db import get_config
    from scripts.dimension_analysis import load_analysis_data, compute_correlations, format_report

    logger.info(f"Starting reflection run (dry_run={dry_run}, min_samples={min_samples})")

    # 1. 加载数据
    df = load_analysis_data(min_window="72h")
    sample_count = len(df) if df is not None else 0
    logger.info(f"Loaded {sample_count} samples with 72h data")

    report = {
        "generated_at": datetime.now().isoformat(),
        "sample_count": sample_count,
        "correlations": {},
        "current_weights": {},
        "suggested_weights": {},
        "skip_reason": None,
    }

    if sample_count < min_samples:
        skip_msg = f"样本数不足（{sample_count} < {min_samples}），跳过权重建议"
        logger.warning(skip_msg)
        report["skip_reason"] = skip_msg
        if not dry_run:
            _send_feishu_report(report, weight_suggestions={})
        return report

    # 2. 计算相关性
    corr_df = compute_correlations(df)
    correlations = {}
    if corr_df is not None and not corr_df.empty:
        # corr_df columns: dimension, target, r, p, n
        # Use xhs_saves correlations (primary engagement signal)
        saves_corr = corr_df[corr_df["target"] == "xhs_saves"] if "target" in corr_df.columns else corr_df
        for _, row in saves_corr.iterrows():
            dim = str(row.get("dimension") or row.name)
            r_val = float(row.get("r", 0) or 0)
            correlations[dim] = round(r_val, 4)
    report["correlations"] = correlations
    logger.info(f"Correlations computed for {len(correlations)} dimensions")

    # 3. 获取当前权重 + 生成建议
    current_weights = get_config("dim_weights", default={})
    report["current_weights"] = current_weights
    suggestions = generate_weight_suggestions(correlations, current_weights)
    report["suggested_weights"] = suggestions

    changed = {k: v for k, v in suggestions.items() if abs(v - current_weights.get(k, 1.0)) > 0.001}
    logger.info(f"Weight changes proposed: {len(changed)} dimensions")
    for dim, new_w in changed.items():
        logger.info(f"  {dim}: {current_weights.get(dim, 1.0):.2f} → {new_w:.3f} (r={correlations.get(dim, 0):.3f})")

    # 4. LLM 误判分析（独立于权重建议，使用人工校正数据）
    from scripts.dimension_analysis import analyze_llm_calibration, format_calibration_report
    cal = analyze_llm_calibration(min_corrections=3)
    report["llm_calibration"] = cal
    if cal["sample_count"] > 0:
        cal_report = format_calibration_report(cal)
        logger.info(f"LLM 误判分析：{cal['sample_count']} 条校正记录，{len(cal['dims'])} 个维度有统计意义")
        for d in cal["dims"][:3]:
            logger.info(f"  {d['dimension']}: 均值偏差={d['mean_error']:+.3f} {d['suggestion'][:40]}")
    else:
        logger.info("LLM 误判分析：暂无人工校正记录")

    # 5. 发送飞书（非dry-run）
    if not dry_run:
        _send_feishu_report(report, weight_suggestions=suggestions)

    return report


def _send_feishu_report(report: dict, weight_suggestions: dict) -> None:
    """发送飞书周报卡片（fire-and-forget）。"""
    try:
        from scripts.feishu_bot import send_text, FEISHU_OPERATOR_OPEN_ID
        if not FEISHU_OPERATOR_OPEN_ID:
            logger.info("FEISHU_OPERATOR_OPEN_ID 未配置，跳过飞书通知")
            return

        sample_count = report.get("sample_count", 0)
        skip_reason = report.get("skip_reason")
        correlations = report.get("correlations", {})
        current_weights = report.get("current_weights", {})

        if skip_reason:
            msg = f"📊 周报反思\n⚠️ {skip_reason}\n样本数：{sample_count}"
        else:
            top_dims = sorted(correlations.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
            corr_lines = "\n".join(
                f"  • {d}：r={r:+.3f}  {current_weights.get(d, 1.0):.2f}→{weight_suggestions.get(d, 1.0):.3f}"
                for d, r in top_dims
            )
            changed_count = sum(
                1 for d, w in weight_suggestions.items()
                if abs(w - current_weights.get(d, 1.0)) > 0.001
            )
            # LLM 误判摘要
            cal = report.get("llm_calibration", {})
            cal_lines = ""
            if cal.get("dims"):
                worst = cal["dims"][:2]
                cal_lines = "\n\nLLM 误判（Top 2）：\n" + "\n".join(
                    f"  • {d['dimension']}：均值偏差{d['mean_error']:+.2f}  {d['suggestion'][:35]}"
                    for d in worst
                )
            msg = (
                f"📊 周报反思 {report['generated_at'][:10]}\n"
                f"样本：{sample_count} 篇  权重调整：{changed_count} 个维度\n\n"
                f"Top 相关维度：\n{corr_lines}"
                f"{cal_lines}\n\n"
                f"运营者可在 Web UI → 配置 中手动采纳权重建议"
            )

        send_text(FEISHU_OPERATOR_OPEN_ID, msg)
        logger.info("飞书周报已发送")
    except Exception as e:
        logger.warning(f"飞书发送失败（不影响反思流程）: {e}")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="XHS 智能体反思层 — 周报 + 权重建议")
    p.add_argument("--dry-run", action="store_true", help="只输出报告，不发送飞书不更新DB")
    p.add_argument("--min-samples", type=int, default=30, help="最少样本数（默认30）")
    args = p.parse_args()

    os.chdir(Path(__file__).parent.parent)
    result = run(dry_run=args.dry_run, min_samples=args.min_samples)
    print(json.dumps(result, ensure_ascii=False, indent=2))
