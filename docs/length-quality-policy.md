# 篇幅 × 质量策略（长度上限用质量分仲裁）

> 问题：story 只卡下限（≥800），无上限 → 改稿反馈只逼「≥tmin」，LLM 一扩就过头（如 958→1192），无人拦。
> 硬卡 900 又会砍掉「内容确实充足」的优质长文。折中：**不做死上限，用 LLM 质量分当长度仲裁**。

## 规则（config `review_thresholds.json`）
| 正文长度（机械字数） | 门槛 | 含义 |
|---|---|---|
| ≤ `story_soft_max`(900) | `story_min_score`(8) | 目标区间，正常门槛 |
| `901 ~ story_hard_max`(1300) | `story_min_score_long`(9) | **长必须明显更好**，更高门槛换更长篇幅 |
| > `story_hard_max`(1300) | ≥9 且**提示精简** | 软顶，防失控（不硬 FAIL） |

- `gzh` / `news`：维持原门槛（gzh 7；news 7），不套此分档。
- `story_body_min`(800) 下限不变。

## 改稿反馈（超长且质量不达标时）
- 超 `soft_max`(900) 但评分不足：`「正文 N 字偏长但评分 X/Y：要么精简到 ≤900（删重复/铺陈），要么把信息增量/内容深度补到 ≥Y（长必须有长的价值）。」`
- 超 `hard_max`(1300)：`「正文 N 字超过 1300：必须精简到 ≤1300（仅留最有价值段落）。」`

## 实现点
- `config/review_thresholds.json`：`story_soft_max` / `story_hard_max` / `story_min_score_long`。
- `agent/write.py::_need(cand, channel, body_len=None)`：story 按 `body_len` 分档。
- `_produce`：每轮评分后按当前 `body_len` 重算 `need`；反馈按上表生成。
- 不新增硬 FAIL（>1300 仅提示），保留优质长文。

## 与豁免的关系
- 密度豁免（`catalog/multi_artist/commentary`）仍只免密度；**篇幅门槛（本策略）独立**，不放水。
- 「字数/评分不放水」仍成立：只是**长文要求更高分**，不是免检。

## 测试
`tests/services/test_config_rules.py::test_need_tiers`（≤900→8；901–1300→9）。
