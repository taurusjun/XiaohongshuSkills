## ADDED Requirements

### Requirement: 生成后立即诊断低分原因，按显式优先级链分类处置
系统 SHALL 在 `process_news_item` 完成 `evaluate_quality` 后立即进行低分诊断，按以下优先级链执行（越靠前优先级越高，避免多条件并存时的分支冲突）：

```
优先级 1：DISCARD   — 话题本身无料，后续所有操作无意义
优先级 2：WAIT_GALLERY — 有图片缺口，重生成解决不了图片问题
优先级 3：REGENERATE  — 生成质量差（话题有潜力，内容写坏了）
优先级 4：HUMAN_REVIEW — 说不清楚，交由运营者判断
```

阈值配置存储在 `agent_strategy.json`，独立配置标题和内容：`title_publish_threshold`（默认 3.0）和 `content_publish_threshold`（默认 2.5），避免用同一个阈值比较不同量纲的字段。

#### Scenario: 优先级 1 — 话题本身无聊 → DISCARD
- **WHEN** `topic_potential`（名人+热点+冲突感+猎奇感+用户共鸣之和）≤ 1，且 `title_score` < `title_publish_threshold`
- **THEN** 文章标记 `status='discarded', discard_reason='boring_topic'`；立即调用 `upsert_topic_performance(topic, discard=True)` 将 `discard_count += 1`；不触发重生成，不进人工队列

#### Scenario: 优先级 2 — 有图片缺口 → WAIT_GALLERY（即使同时有生成质量问题）
- **WHEN** `image_url` 为空，`topic_potential` ≥ 2（无论 `quality_issues` 是否为 1）
- **THEN** 文章暂存，标记 `pending_gallery=True`；图集下载完成后触发二次评分；不触发重生成（因为图片缺口无法通过重生成修复）

#### Scenario: 优先级 3 — 生成质量差 → REGENERATE
- **WHEN** `quality_issues`（啰嗦重复+离题之和）≥ 1，`topic_potential` ≥ 2，且 `image_url` 不为空
- **THEN** 触发重生成，针对 `failed_dims` 注入修正提示词，最多重试 2 次

#### Scenario: 优先级 4 — 原因不明 → HUMAN_REVIEW
- **WHEN** 不满足以上任何分支，但 `title_score` < `title_publish_threshold` 或 `content_score` < `content_publish_threshold`
- **THEN** 标记 `needs_human_review=True`，在飞书审批卡片中以「⚠️ 低分待审」标注，展示具体低分维度供运营者判断

#### Scenario: DISCARD 记录写回 topic_performance
- **WHEN** DISCARD 分支执行
- **THEN** `topic_performance.discard_count += 1`，`last_discard_reason` 更新；planner 在下次规划时，对 `discard_count >= 3` 且无任何成功发布记录的话题，降级到「观察期」（不分配配额，继续趋势监控），并在飞书摘要中通知运营者

### Requirement: 重生成时注入针对性修正提示词
系统 SHALL 根据具体失败维度，在重生成调用中附加修正指令，而非直接重跑原始 prompt。

#### Scenario: 啰嗦重复导致低分
- **WHEN** 重生成原因为 `啰嗦重复=1`
- **THEN** prompt 附加「内容需要精炼，控制在 200 字以内，每句话都要有信息量，删除重复表达」

#### Scenario: 离题导致低分
- **WHEN** 重生成原因为 `离题=1`
- **THEN** prompt 附加「内容必须紧扣标题核心事件，不得延伸到无关话题」

#### Scenario: 达到重试上限仍低分
- **WHEN** 已重试 2 次，最终 `title_score` 仍 < `title_publish_threshold`
- **THEN** 文章进入人工审核队列，重试历史（每次分数和失败维度变化）记录在 `score_dims` 的 `reason` 字段中
