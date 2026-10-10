# 写稿失败队列（write_failures）

## 目的
写稿阶段**任何原因失败**（LLM 抖动/网络超时、解析失败、机械门禁不过、评分不达标、正文为空、异常）的文章，不再静默丢弃，而是落入 `write_failures` 表：
- **1 小时后自动重试**（`cli write-retry`，cron 每 15 分钟扫描到期项）；
- **重试再失败 → 转「待人工」**（`status='needs_manual'`）；
- **重试成功 → 标记 `resolved`（保留记录，不删行）**。
- 该表用于**分析写稿失败的主要原因**（`category` 归一化 + `stats()` 聚合）。

## 表结构（`write_failures`）
| 列 | 说明 |
|---|---|
| `key` | 素材 key（主键） |
| `reason` | 失败原因（门禁问题串 / 异常摘要） |
| `category` | **归一化主因类别**（见下，供聚合） |
| `stage` | 失败阶段：`exception` / `gate` |
| `attempts` | 本篇改稿尝试次数 |
| `channel` | 失败时渠道 |
| `title` | 标题（便于识别） |
| `retry_count` | 已重试次数 |
| `status` | `pending`｜`needs_manual`｜`resolved` |
| `next_retry_at` | 下次重试时间（= 失败时间 + 60min） |
| `created_at` / `updated_at` | 时间戳 |
| `traceback` | **报错堆栈**（异常时 `traceback.format_exc()`） |

## 主因类别（`CATEGORY_LABELS`）
`llm_network`(LLM 网络/超时) · `llm_parse`(输出解析失败) · `empty_body`(正文为空) ·
`gate_density`(密度不足) · `gate_length`(字数不足) · `gate_structure`(结构) · `gate_title`(标题超长) ·
`gate_kana`(残留假名) · `gate_dunhao`(顿号) · `gate_shintai`(日文新字体) ·
`renwei`(renwei 拒稿) · `gzh_review`(公众号审读) · `score_low`(评分不达标) ·
`exception_other` · `gate_other`。

## 生命周期
```
写稿失败 ──record()──▶ pending (next_retry_at=now+60m)
                          │  cron write-retry 扫描到期
                          ├─ 成功/已写/素材不存在 ─▶ resolved（保留行）
                          └─ 再失败/异常 ─────────▶ needs_manual（等人工）
```

## 触发点（`agent/write.py`）
- `run()` 对每篇 `write_one` 包 `try/except`：**单篇异常不再中断整批**，异常写 `record(stage='exception', tb=format_exc())`。
- `write_one` 返回 `ok=False`（非 skip/bullet）→ `record(stage='gate', reason=problems)`。
- `pick_candidates` 排除 `list_open()`（pending/needs_manual）中的 key，避免批量与重试重复处理；`resolved` 不阻塞。

## 重试
- `agent/write.py::retry_failures()` / `python -m cli write-retry [--limit N]`。
- cron：`*/15 * * * * … cli write-retry`（见 `ops/crontab`）。

## 分析入口
- CLI：`python -m cli write-failures [--days 30]` → 打印主因分布 + 未结列表。
- API：`GET /api/write-failures`（全量）、`GET /api/write-failures/stats?days=30`（主因聚合）。
- 后台：`/write-failures`（主因条形图 + 列表 + 报错堆栈展开 + 立即重试/忽略）；素材页「❌ 失败稿」按钮。

## 测试
`tests/services/test_write_failures.py`（落表/到期/标记成功不删/转待人工/堆栈/主因聚合）。
## 门禁/评分豁免（一律通过 + warn）
- **3 轮改稿后仍过不了门禁/评分** → **一律通过（不阻断）**，把剩余问题写入本表 `level='warn'`（status=resolved，不重试），供后续调整依据。
- 例外：**空正文**（LLM 未产出）→ 仍算失败（无法入库）。
- 因此本表现在同时承载：**error**（异常/空正文，待重试或人工）与 **warn**（门禁/评分豁免留痕）。
- 列的 `level`：`error`（默认）｜`warn`。
