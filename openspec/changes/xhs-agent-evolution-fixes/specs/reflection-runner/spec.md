## ADDED Requirements

### FR-RR-1: reflection_runner.py 存在且可独立运行
- 文件路径：`scripts/reflection_runner.py`
- 命令行：`python scripts/reflection_runner.py [--dry-run] [--min-samples N]`
- `--dry-run`：只输出报告和权重建议，不发送飞书，不更新 DB
- `--min-samples N`（默认30）：少于N篇完整数据时跳过权重建议，仅输出报告

### FR-RR-2: 执行流程
1. 调用 `dimension_analysis.load_analysis_data()` 加载数据
2. 调用 `dimension_analysis.compute_correlations()` 计算 Pearson r
3. 生成权重建议：`new_weight = clip(current * (1 + 0.3 * r), 0.1, 3.0)`；|r| < 0.1 的维度保持不变
4. 调用 `feishu_bot.build_weekly_report_card()` 构建卡片
5. 发送飞书通知（`FEISHU_OPERATOR_OPEN_ID`）
6. 输出 JSON 报告到 stdout

### FR-RR-3: 权重建议格式
```json
{
  "generated_at": "2026-05-22T23:00:00",
  "sample_count": 45,
  "correlations": {"剧情感": 0.42, "用户共鸣": 0.38, ...},
  "current_weights": {"剧情感": 1.8, ...},
  "suggested_weights": {"剧情感": 2.03, ...},
  "skip_reason": null
}
```

### FR-RR-4: cron 触发
- `agent_runner.py` 中的每周日 23:00 cron 调用 `reflection_runner.run()`（已在设计中规划）
- 独立于每日 07:00 主循环

### Test Cases
- TC-RR-1: `--dry-run` 模式下不修改 DB，不调用飞书 API
- TC-RR-2: 样本数 < 30 时输出含 `skip_reason` 的 JSON，不生成权重建议
- TC-RR-3: Pearson r = 0.42 时，建议权重 = current * 1.126，精度误差 < 0.001
- TC-RR-4: 权重建议上限3.0下限0.1的截断逻辑验证
