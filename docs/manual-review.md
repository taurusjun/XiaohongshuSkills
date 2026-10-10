# 待人工清单（manual_review）

## 语义
写稿内容门禁命中 **成人产业/风俗** 或 **非赛道** 的素材，**不阻断流程**，但打标 `manual_review=1` + `manual_reason`，待人工判断「写不写、怎么写」。

- `manual_review` INTEGER DEFAULT 0
- `manual_reason` TEXT DEFAULT ''（命中关键词 + 建议）

## 判定来源（config + 服务）
- `config/adult_industry.json`：成人产业/风俗/AV 关键词；`action: pending_manual`。
- `config/off_topic.json`：商务/理财/区块链等非赛道关键词。
- `services/content_gate.check(title, body)`：在 `write_one` 写稿后调用，命中→ `update_news(key, {manual_review:1, manual_reason:...})`。

> 旧 skill 对成人产业是「硬跳过」或「改道 gzh（人物弧光）」的**人判**；容器化改为**标待人工**，由人决定，避免误杀（如完整人物弧光的深访稿）。

## 人工处置入口
- 页面：`http://<host>:15000/manual-review`
- API：
  - `GET /api/manual-review` → 待人工列表
  - `POST /api/manual-review/<key>/clear` → 标记已处理（清标记）

## 关联
- 门禁命中的 key 同时进入 batch manifest 的**跳过清单**（`data/write_batches/<date>.json`）。
- 渠道改道（如成人产业人物弧光→gzh）由 **review 的 `channel_hint='gzh'`** 表达，write 会强制走 gzh。

## 测试
`tests/services/test_config_rules.py::test_content_gate_marks_manual`
