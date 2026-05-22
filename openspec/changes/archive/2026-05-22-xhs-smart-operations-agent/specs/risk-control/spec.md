## ADDED Requirements

### Requirement: CDP 操作行为随机化，规避平台自动化检测
系统 SHALL 在所有 CDP 自动化操作中引入随机延迟抖动，包括步骤间延迟和启动时间偏移。

#### Scenario: 发布操作步骤间加随机延迟
- **WHEN** CDP 执行内容发布流程（填写标题 → 填写正文 → 上传图片 → 点击发布）
- **THEN** 每个步骤之间的等待时间在基础等待时间的 ±30% 范围内随机浮动

#### Scenario: 每日发布时间加随机偏移（扩展窗口）
- **WHEN** `agent_runner` 安排 12:00 发布
- **THEN** 实际发布时间在 `-15 min ~ +30 min` 非对称随机范围内选取（稍晚发布对时效性影响较小），整体窗口约 45 分钟，避免跨天规律性特征

#### Scenario: agent_runner 启动时加随机延迟
- **WHEN** cron 在 07:00 触发 `agent_runner.py`
- **THEN** 脚本内部先 `sleep(random.randint(0, 180))`（0-3 分钟随机延迟）后再开始执行，将 cron 精确触发时间的规律性打散

### Requirement: 补发数量上限控制，避免间歇性批量发布异常
系统 SHALL 在 agent_runner 检测到前一天未能发布时，限制当日最多额外补发 1 篇。

#### Scenario: 前日未发布时的补发限制
- **WHEN** `account_snapshots` 显示昨日发布数为 0，今日正常配额为 2 篇
- **THEN** 今日最大发布上限为 3 篇（正常配额 + 1 篇补发），多余积压文章保留到后续日期，飞书摘要中注明「今日包含 1 篇昨日补发内容」

### Requirement: AI 生成内容文本识别风险防御
系统 SHALL 在内容生成 prompt 中加入明确的「去 AI 味」指令，并在 reflection 层监控 AI 惯用语频率，防止账号被平台标记为 AI 内容农场。

> **背景：** 小红书 2025-2026 年已收紧 AI 批量内容账号管控，识别维度包括：高频 AI 惯用语（「不容错过」「干货满满」「强烈推荐」等）、句式模板（三段式）、标点分布异常均匀。账号一旦被标记，解除需要很长冷却期。

**内容生成 prompt 必须包含以下限制指令（在 `yahoo_common.py` 的 generate_content prompt 中加入）：**
```
禁止使用以下词组：不容错过、干货满满、满满干货、精心整理、强烈推荐、值得关注、绝对值得、不得不看、超级好用
禁止使用三段式结构（首先...其次...最后...）
句式长短需有变化，不要每句都是相似长度
适当加入口语化表达，使内容读起来像真人写的
```

#### Scenario: 生成 prompt 包含去 AI 味指令
- **WHEN** `generate_content_and_comment()` 被调用
- **THEN** 发送给 LLM 的 prompt 包含上述禁用词组和句式约束，LLM 生成的内容不出现禁用词组

#### Scenario: reflection_runner 周报包含 AI 特征词频监控
- **WHEN** `reflection_runner` 运行，且近 30 篇已发布内容可查
- **THEN** 统计这 30 篇内容中 AI 惯用语（可配置的词组列表）出现次数，超过 `ai_phrase_threshold`（默认 15 次/30篇，即平均每篇 0.5 个）时在飞书周报中标注「⚠️ 近期内容 AI 特征词频较高，建议检查生成 prompt」

#### Scenario: 运营者更新禁用词组列表
- **WHEN** 运营者在 `agent_strategy.json` 中修改 `ai_banned_phrases` 列表
- **THEN** 下次内容生成时自动注入新的禁用词组，无需修改代码

### Requirement: CDP 连接失败时的重试与降级
系统 SHALL 在 CDP 连接失败时执行有限次数重试，重试耗尽后进入降级模式。

#### Scenario: CDP 首次连接失败，触发重试
- **WHEN** CDP 连接抛出异常
- **THEN** 等待 30 秒后重试，最多重试 3 次

#### Scenario: 3 次重试均失败，进入降级模式
- **WHEN** 3 次重试后 CDP 仍不可用
- **THEN** 跳过趋势扫描和发布步骤，继续执行「使用缓存数据规划 → LLM 生成内容 → 推送飞书摘要（标注 CDP 不可用）」

#### Scenario: 飞书发送失败时 Web UI 兜底
- **WHEN** 飞书消息发送失败
- **THEN** 将待审批内容列表写入 `agent_strategy` 表（key=`pending_approvals_YYYYMMDD`），Web UI 首页显示红点徽章「有 N 篇内容待审批」（详见 feishu-integration/spec.md）

### Requirement: 账号限流/降权状态检测与响应
系统 SHALL 通过 `account_snapshots` 数据检测账号是否处于疑似限流状态，并自动降低发布频率以助于账号恢复。

限流特征：新发内容浏览均值骤降（正常新账号 200-500，限流期可能降至个位数）。

#### Scenario: 检测到疑似限流
- **WHEN** `account_snapshots` 连续 3 天显示「当周发布内容」的平均浏览量 < `low_view_threshold`（默认 50），且账号已发布 ≥ 5 篇内容（排除冷启动极早期）
- **THEN** 飞书推送「⚠️ 账号疑似处于限流状态，建议暂停 1-2 天发布观察恢复」，同时今日规划中将发布配额自动降至 1 篇（而非完全停止，以维持账号活跃信号）

#### Scenario: 限流期间避免加量
- **WHEN** `growth_stage != cold_start` 但账号处于疑似限流状态
- **THEN** 即使 `engagement_score > 历史均值 120%`，也不触发加量逻辑（疑似限流期间的数据不可信）

### Requirement: 图片版权合规标注
系统 SHALL 在图集下载时记录图片来源域名，在飞书审批卡片和 Web UI 详情页中展示，辅助运营者判断版权合规性。

#### Scenario: 展示图片来源域名
- **WHEN** 飞书审批卡片展示候选文章
- **THEN** 每篇文章的封面图下方显示来源域名（如 `oricon.co.jp`），供运营者判断
