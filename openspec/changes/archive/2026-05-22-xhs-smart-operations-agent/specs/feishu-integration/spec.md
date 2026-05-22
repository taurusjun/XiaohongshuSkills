## ADDED Requirements

### Requirement: 使用开放平台 Bot 发送消息并接收按钮回调
系统 SHALL 通过飞书开放平台 Bot 发送交互卡片消息，并通过注册的事件回调 URL 接收用户的按钮点击操作。服务器有公网 IP，可直接作为回调地址。

凭证存储于 `scripts/.env`：
- `FEISHU_APP_ID` / `FEISHU_APP_SECRET`：开放平台应用凭证
- `FEISHU_OPERATOR_OPEN_ID`：运营者的飞书 open_id
- `FEISHU_WEBHOOK_SECRET`：事件回调签名验证密钥

Token 管理：`tenant_access_token` 有效期 2 小时，系统 SHALL 在内存中缓存并在过期前自动刷新。

#### Scenario: 发送文本告警
- **WHEN** `feishu_bot.send_text("⚠️ CDP 连接失败")` 被调用
- **THEN** POST 到飞书消息 API，5 秒超时，失败时写入 Web UI 待审批队列并记录告警日志（不静默跳过）

#### Scenario: 飞书发送失败时 Web UI 兜底
- **WHEN** 飞书 API 调用失败（超时、Token 失效、网络异常等）
- **THEN** 将待推送内容写入 `agent_strategy` 表（key=`pending_approvals_YYYYMMDD`），Web UI 首页显示红点徽章「有 N 篇内容待审批」；运营者在 Web UI 完成审批的效果与飞书审批等价

#### Scenario: 凭证未配置时降级为 Web UI 模式
- **WHEN** `FEISHU_APP_ID` 环境变量为空
- **THEN** 所有飞书发送静默跳过，但 Web UI 待审批队列正常写入，运营者通过 Web UI 完成全部操作；系统启动时打印 WARNING「飞书未配置，使用 Web UI 纯审批模式」

### Requirement: 每日候选文章审批交互卡片，含内容预览
系统 SHALL 推送含操作按钮的交互卡片，卡片中每篇文章包含内容预览，让运营者在审批前有足够信息判断。

#### Scenario: 推送每日候选卡片
- **WHEN** `agent_runner` 完成生成+评分，有 ≥ 1 篇候选文章
- **THEN** 发送交互卡片，每篇文章展示：标题、内容前 100 字预览、话题、综合评分、收藏驱动值、内容体裁、相似度警告（如有）、来源图片域名，操作按钮：「✅ 发布（推荐时间）」「✅ 修改时间」「❌ 跳过」「🔄 重新生成」

#### Scenario: 时效性内容标注紧急发布
- **WHEN** 候选文章「热点」维度=1
- **THEN** 卡片该文章行标注「⏰ 时效性内容，建议尽快发布」，并推荐最近可用时间段（而非等到 12:00/18:00）

#### Scenario: 爆款候选标注（成长期）
- **WHEN** `growth_stage=growth` 且文章爆款潜力分 ≥ 3
- **THEN** 卡片该文章行标注「⭐ 爆款候选，建议优先发布」

#### Scenario: 运营者点击「✅ 发布（推荐时间）」
- **WHEN** 飞书回调携带 `{action: "approve", news_key: "xxx", post_time: "12:00"}`
- **THEN** `/webhook/feishu` 验证签名，将对应文章写入 `publish_xhs=1, publish_time=..., approval_status="approved"`，同时触发一次 publish 脚本检查（不等待下次轮询），更新卡片状态为「已批准 ✅」

#### Scenario: 运营者点击「🔄 重新生成」
- **WHEN** 回调携带 `{action: "regenerate", news_key: "xxx"}`
- **THEN** 触发后台重生成任务，完成后推送新卡片展示更新后的内容和评分

#### Scenario: 每日摘要包含「今日丢弃文章」折叠列表
- **WHEN** 当日有被 DISCARD 的文章
- **THEN** 飞书每日摘要末尾追加折叠区「今日丢弃 {N} 篇（点击展开）」，展开后每篇显示：标题、丢弃原因（`boring_topic` / `reached_retry_limit` 等）、Web UI 链接；运营者可从此处找到想做快速实验的低分文章并在 Web UI 标记「强制候选」

#### Scenario: 当日无候选文章
- **WHEN** 所有文章均被丢弃或抓取失败
- **THEN** 推送纯文本告警（无按钮），列出丢弃原因统计，包含进入观察期的话题列表

### Requirement: 发布前 1 小时超时提醒，追踪审批状态
系统 SHALL 追踪每篇候选文章的审批状态，在发布时间临近时推送提醒，并区分「主动跳过」和「超时未审批」。

新增字段 `approval_status`：`pending` / `approved` / `skipped` / `timeout`

#### Scenario: 发布前 1 小时提醒
- **WHEN** 某文章计划发布时间 T-60min，且 `approval_status` 仍为 `pending`
- **THEN** 飞书推送文本提醒「⏰ 距计划发布时间（HH:MM）还有 1 小时，以下 N 篇文章待审批：[标题列表]」

#### Scenario: 超时处理
- **WHEN** 计划发布时间已过，`approval_status` 仍为 `pending`
- **THEN** 将 `approval_status` 置为 `timeout`，文章不发布，下次计划生成时不自动重新排队，运营者可在 Web UI 手动重新提交

### Requirement: 每周策略审批交互卡片
系统 SHALL 推送含「一键进入调整模式」按钮的周报卡片（而非「一键采纳」），运营者逐条确认权重调整建议后再提交。

#### Scenario: 推送周报 + 权重建议卡片（含置信度标注和算法变化上报）
- **WHEN** `reflection_runner` 生成了新的权重建议
- **THEN** 卡片展示：本周发布篇数、平均收藏、内容体裁效果对比、建议权重变更列表（每个维度显示「当前 → 建议，r=X，置信度：低/中/高」），按钮：「📝 逐条确认」「🧪 实验发布配置」「🚨 上报算法变化」「📋 查看详情（Web UI 链接）」

#### Scenario: 运营者点击「📝 逐条确认」后选择性采纳
- **WHEN** 运营者在调整模式中勾选部分维度并提交
- **THEN** 系统只更新被勾选的维度权重，其余维度保持不变；`commit_dimension_version()` 写入新版本，飞书推送确认「已采纳 X 条建议，版本更新至 vY.Y.Y」

### Requirement: 发布后 T+2h 评论互动提醒
系统 SHALL 在文章发布成功后 2 小时，通过飞书向运营者推送评论互动提醒，促进发布后的及时互动，提升该内容进入「热门」频道的概率。

> **业务背景：** 小红书算法对发布后 2 小时内的评论互动密度有较高权重，这是冷启动期内容破圈的关键信号。系统可以生成「评论引导性」内容，但如果运营者不知道需要在 2 小时内回复评论，这个努力会大打折扣。

#### Scenario: T+2h 评论互动提醒推送
- **WHEN** 某文章发布成功满 2 小时（`xhs_pub_time` + 2h）
- **THEN** 飞书推送「📣 《{标题}》已发布 2 小时，当前评论数 {N}，建议登录小红书及时回复评论，促进互动热度」

#### Scenario: 已有评论时的提醒格式
- **WHEN** `xhs_comments > 0`
- **THEN** 提醒消息中列出评论数，提示运营者「有 {N} 条评论待回复」

#### Scenario: 无评论时的提醒格式
- **WHEN** `xhs_comments = 0`
- **THEN** 提醒消息中说明「暂无评论，可主动通过留言引导互动」

### Requirement: 超时未审批文章自动归档
系统 SHALL 在每日 agent_runner 运行时，检查前一日处于 `approval_status='timeout'` 状态的文章，将其归档并在飞书摘要中列出。

> **设计背景：** `timeout` 文章处于悬空状态——既不是 `published` 也不是 `discarded`，如果不处理，可能被次日 planner 误判为待处理内容。

#### Scenario: 次日归档超时文章
- **WHEN** `agent_runner` 运行，且 `agent_strategy` 表中 `pending_approvals_{昨日日期}` 含有 `approval_status='timeout'` 的文章
- **THEN** 将这些文章的 `status` 置为 `archived_timeout`，飞书每日摘要中注明「昨日有 N 篇超时未审批文章已归档，如需发布请在 Web UI 重新提交」

#### Scenario: 运营者在 Web UI 重新提交归档文章
- **WHEN** 运营者在 Web UI 点击某 `archived_timeout` 文章的「重新提交」
- **THEN** 文章 `approval_status` 重置为 `pending`，生成新的飞书审批卡片推送

### Requirement: 异常告警立即推送
系统 SHALL 在以下异常发生时立即推送飞书文本告警（飞书不可用时写入 Web UI 队列）：CDP 连接 3 次重试失败、连续 3 篇丢弃、账号近 3 日数据环比下滑 > 40%、话题进入观察期。

#### Scenario: CDP 连接 3 次重试失败
- **WHEN** CDP 重试机制耗尽
- **THEN** 推送「⚠️ CDP 连接失败（已重试 3 次），今日扫描和发布步骤跳过，内容生成流程继续」

### Requirement: agent_strategy 配置修改后静默失效检测
系统 SHALL 在 agent_runner 启动时，比较 `agent_strategy.json` 的文件修改时间与数据库中该策略最后初始化时间，如果 JSON 比数据库更新，推送飞书告警，提示运营者通过 Web UI 重新导入配置。

> **设计背景：** `agent_strategy` 以 SQLite 为单一真相来源，JSON 文件在初始化后不再被自动读取。如果运营者直接编辑 JSON（修改 `focus_topics`、`growth_stage` 等），修改不会生效，系统不会有任何提示，形成「静默失效」。

#### Scenario: 检测到 JSON 文件比数据库更新
- **WHEN** agent_runner 启动时，`agent_strategy.json` 的 `mtime` > `agent_strategy` 表最后 `updated_at`
- **THEN** 飞书推送「⚠️ agent_strategy.json 已于 {时间} 修改，但系统当前使用的是数据库配置（上次同步：{时间}）。如需应用 JSON 中的新配置，请在 **Web UI 策略设置页面** 点击「从文件重新导入」，或执行 `python scripts/agent_tools.py reload-strategy`」

#### Scenario: JSON 未被修改
- **WHEN** `agent_strategy.json` 的 `mtime` ≤ 数据库最后同步时间
- **THEN** 不推送任何告警，正常启动

### Requirement: 竞品账号配置说明
系统 SHALL 在 `agent_strategy.json` 的模板中提供竞品账号 `competitor_accounts` 字段的配置示例和 user_id 获取方式说明，确保 P1 功能上线时运营者能够顺利配置。

`agent_strategy.json` 中竞品账号配置示例：
```json
{
  "competitor_accounts": [
    {
      "user_id": "5e29b35a000000000100xxxx",
      "label": "竞品账号A（日本娱乐头部）"
    }
  ]
}
```

> **如何获取 XHS user_id：** 在 PC 端打开竞品账号主页，浏览器地址栏 URL 格式为 `https://www.xiaohongshu.com/user/profile/{user_id}`，`user_profile/` 后面的字符串即为 user_id。

#### Scenario: 竞品账号列表为空时 P1 功能跳过
- **WHEN** `competitor_accounts` 为空列表或未配置
- **THEN** reflection_runner 跳过竞品扫描，飞书周报中显示「竞品监控：未配置（请在 agent_strategy.json 中填写 competitor_accounts）」

### Requirement: 飞书事件回调签名验证与 challenge 响应
系统 SHALL 对所有 `/webhook/feishu` 收到的请求验证飞书签名，拒绝非法请求；对飞书的 URL 验证请求立即响应。

#### Scenario: 签名验证失败
- **WHEN** 请求头签名不匹配
- **THEN** 返回 HTTP 401，记录警告日志，不执行任何操作

#### Scenario: 飞书服务器验证挑战（challenge）
- **WHEN** 飞书发送 `{"type": "url_verification", "challenge": "xxx"}`
- **THEN** 立即返回 `{"challenge": "xxx"}`，HTTP 200
