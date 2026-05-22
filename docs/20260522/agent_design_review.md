# 智能体设计架构审查报告

**审查日期：** 2026-05-22
**整体完成度：** 5.5 / 10
**结论：** 有 3 个运营阻断级 bug，当前版本不可直接投入运营

---

## 各层完成度

| 层级 | 完成度 | 说明 |
|---|---|---|
| 感知层 | 55% | 扫描有 bug，账号快照空表 |
| 记忆层 | 60% | 表结构完整，数据写入有缺失 |
| 规划层 | 60% | cold_start 基本可用，growth/stable 未区分 |
| 执行层 | 40% | 核心 bug：hint 未传入、优先级链错误 |
| 反思层 | 65% | 工具完整，未与主循环集成 |
| Human-in-Loop | 30% | 飞书回调端点缺失，dry-run 不符合 spec |

---

## 🔴 运营阻断级 Bug（3个）

### Bug 1：重生成修正指令是死代码

`regenerate_with_hint()` 把修正提示写入 `news["_regen_hint"]`，但 `generate_content_and_comment()` 函数签名没有 `hint` 参数，hint 变量赋值后立即被丢弃，三次重试的 LLM 调用使用完全相同的 prompt，重试机制没有任何实质作用。

**修复：** `generate_content_and_comment` 增加 `hint: str = ""` 参数，在 prompt 末尾附加 hint 文本。

### Bug 2：飞书审批闭环不存在

审批卡片能发出但没有接收端（`web/app.py` 中无飞书回调路由），运营者点击「发布」无任何响应。

**修复：** 在 `web/app.py` 增加飞书回调路由，处理 approve/skip/regenerate 三种动作。

### Bug 3：`account_snapshots` 永远空，配额弹性逻辑失效

没有任何代码写入 `account_snapshots` 表，`get_recent_performance()` 永远返回 0，规划层的加量/减量逻辑永远不触发。

**修复：** agent_runner 每日执行时写入账号快照（followers、week_saves、week_views）。

---

## 🟡 中等问题

### 执行层
- `diagnose_low_score` 四分支优先级链错误：WAIT_GALLERY 在 DISCARD 前面（反了）
- DISCARD 触发条件不对：只检查了 3 个 minus 维度，没有计算 `topic_potential`（名人+热点+冲突感+猎奇感+用户共鸣之和 <= 1）
- `HUMAN_REVIEW` 分支是死代码，永远无法被返回
- `news.regeneration_history` 字段未实现
- 使用单一 `publish_threshold`，未区分标题/内容独立阈值

### 感知层
- `xhs_trend_scanner.py` 中 `is_fresh` 计算有 bug：月初 `day-1=0` 会触发 ValueError
- `is_fresh` 字段未写入 `topic_performance`，冷启动弹性配额上调永远无法触发

### 规划层
- `growth` 和 `stable` 阶段合并到同一逻辑分支，未实现三段区分
- `recommend_post_times()` 是空壳，热点内容 2 小时 SLA 未实现
- `growth_stage` 升级提醒未实现
- 话题进入观察期没有飞书通知

### 记忆层
- `upsert_topic_performance` 使用 INSERT OR REPLACE 覆写，不是滚动平均
- `discard_count` 递增逻辑缺失（DISCARD 分支没有调用更新）
- `cleanup_old_states()` 存在但没有任何地方定期触发

### 反思层
- `dimension_analysis.py` 是独立脚本，未与主循环集成
- `load_analysis_data()` 中 `min_window` 参数未在 SQL 中使用

### Human-in-Loop
- `--dry-run` 没有注入 fixture 数据，仍调用真实 CDP
- `--live-preview` 是空实现（输出计划后直接退出）
- Phase 4 通知只发文字，没有发送审批卡片

### 代码质量
- `scoring.py` 顶层导入有副作用，破坏可测试性
- `yahoo_common.py` 顶层 `sys.exit(1)` 是反模式
- `process_news_item` 超 200 行，职责混合
