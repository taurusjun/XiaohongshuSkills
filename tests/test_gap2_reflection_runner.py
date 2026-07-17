"""Gap 2 tests: reflection_runner TC-RR-1 to TC-RR-4."""
import os, sys, pytest, json
from unittest.mock import patch, MagicMock
os.environ.setdefault("SQLITE_PATH", ":memory:")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.reflection_runner import generate_weight_suggestions, run


def test_tc_rr1_dry_run_no_feishu_no_db(fresh_db):
    """TC-RR-1: --dry-run 不调用飞书，不修改 DB"""
    with patch("scripts.feishu_bot.send_text") as mock_send, \
         patch("scripts.dimension_analysis.load_analysis_data") as mock_load, \
         patch("scripts.dimension_analysis.compute_correlations") as mock_corr:

        import pandas as pd
        mock_load.return_value = pd.DataFrame()  # empty → skip_reason

        result = run(dry_run=True, min_samples=30)

        mock_send.assert_not_called()
        assert result.get("skip_reason") is not None


def test_tc_rr2_insufficient_samples(fresh_db):
    """TC-RR-2: 样本 < 30 → skip_reason 非空，无权重建议"""
    with patch("scripts.dimension_analysis.load_analysis_data") as mock_load, \
         patch("scripts.feishu_bot.send_text"):
        import pandas as pd
        mock_load.return_value = pd.DataFrame({"dummy": range(5)})  # 5 rows

        result = run(dry_run=True, min_samples=30)

    assert result["skip_reason"] is not None
    assert result["suggested_weights"] == {}


def test_tc_rr3_weight_formula():
    """TC-RR-3: r=0.42, current=1.0 → new = 1.0 * (1 + 0.3*0.42) = 1.126"""
    weights = generate_weight_suggestions(
        correlations={"剧情感": 0.42},
        current_weights={"剧情感": 1.0}
    )
    assert abs(weights["剧情感"] - 1.126) < 0.001, f"Expected 1.126, got {weights['剧情感']}"


def test_tc_rr4_weight_clipping():
    """TC-RR-4: 权重不超过 3.0 不低于 0.1"""
    # Extremely high r would push above 3.0
    weights = generate_weight_suggestions(
        correlations={"高维度": 1.0, "低维度": -1.0},
        current_weights={"高维度": 3.0, "低维度": 0.1}
    )
    assert weights["高维度"] <= 3.0
    assert weights["低维度"] >= 0.1


def test_low_r_no_change():
    """|r| < 0.10 → 权重不变"""
    weights = generate_weight_suggestions(
        correlations={"稳定维度": 0.05},
        current_weights={"稳定维度": 1.8}
    )
    assert abs(weights["稳定维度"] - 1.8) < 0.001


def test_preserve_uncorrelated_dims():
    """相关性字典中没有的维度，保留原权重"""
    weights = generate_weight_suggestions(
        correlations={"剧情感": 0.3},
        current_weights={"剧情感": 1.8, "未分析维度": 1.2}
    )
    assert "未分析维度" in weights
    assert abs(weights["未分析维度"] - 1.2) < 0.001
