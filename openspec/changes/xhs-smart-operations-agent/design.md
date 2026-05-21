## Context

项目已有完整的 CDP 浏览器自动化、SQLite 数据层、Flask Web UI、LiteLLM AI 生成、Yahoo 抓取和 XHS 发布能力。`xhs-feedback-loop` change 正在建立实发数据回收能力。本次升级在此基础上叠加「感知→记忆→规划→执行→反思」五层，将系统从手动流水线升级为有目标感知的运营智能体。核心约束：不引入 LangGraph，不增加外部服务依赖（除飞书），复用所有现有 CDP 能力。

## Goals / Non-Goals

**Goals:**
- 每日自动完成「感知趋势 → 规划内容 → 生成评分 → 飞书审批 → 定时发布 → 回收数据」完整循环
- 评分系统可基于历史数据动态调整维度权重
- 低分文章智能分类处置，减少运营者逐篇判断的工作量
- 运营者通过飞书卡片完成每日审批，每次操作 < 30 秒
- 每周自动生成策略建议，运营者一键采纳或修改

**Non-Goals:**
- 不引入 LangGraph 或多智能体框架
- 不实现封面图多模态评分（独立 change）
- 不支持 Notion 后端
- 不实现自动采纳策略建议（权重调整必须人工确认）
- 不实现实时推送或 WebSocket

## Decisions

### D1：规划层用规则引擎，不用 LLM

话题选择和配额决策是结构化问题（数值比较、排序、阈值判断），用 LLM 会引入不确定性且浪费 token。规则引擎（Python 代码）更快、更可预测、更易调试。

LLM 只用于：翻译、内容生成、质量评分、写自然语言周报。

### D2：低分诊断用维度组合判断，而非阈值规则

单一分数阈值（如 score < 2）会把「话题无聊」和「生成失败」混在一起，处置方式不同。诊断逻辑读取具体维度值：

```
topic_potential  = sum(名人, 热点, 冲突感, 猎奇感, 用户共鸣)
quality_issues   = sum(啰嗦重复, 离题)
has_image        = bool(image_url)

if topic_potential <= 1:          → DISCARD
elif quality_issues >= 1:         → REGENERATE (max 2 tries)
elif not has_image and score < threshold: → WAIT_GALLERY
else:                             → HUMAN_REVIEW
```

重生成时针对失败维度注入提示词修正，而非盲目重跑。

### D3：飞书用开放平台 Bot + 交互卡片，不用 Webhook

纯 Webhook 是单向的，无法接收用户点击回调。开放平台 Bot 支持「消息卡片 + 按钮回调」，可实现「推送候选 → 你点按钮 → 后端收到回调 → 触发发布」的闭环审批流。

回调接收：`web/app.py` 新增 `/webhook/feishu` 路由，验证签名后处理 card action 事件。

### D4：评分权重热读取，不硬编码

`agent_strategy.json` 每次调用 `evaluate_quality` 时从磁盘读取（加 1 秒内存缓存），修改文件后下次调用立即生效，无需重启服务。这让运营者可以直接编辑 JSON 进行快速实验。

### D5：agent_runner 用 cron + 幂等设计，不用常驻进程

常驻进程复杂、崩溃恢复麻烦。cron 每小时触发 `agent_runner.py`，脚本通过 SQLite 状态表判断「今日计划是否已生成」「哪些文章待抓取」「哪些审批已收到」，实现幂等——多次运行不会重复操作。

### D6：飞书卡片 ID 与 news.key 映射，用 SQLite 持久化

飞书回调只携带 card action 的 `value` 字段（自定义 JSON），将 `{feishu_msg_id: news_key, action: "approve/skip/regenerate"}` 存入 `agent_strategy` 表，解决进程重启后状态丢失问题。

## Risks / Trade-offs

- **飞书回调需要公网可达 URL** → 本地开发用 ngrok/frp 做内网穿透；生产环境确保 Flask 服务有固定 IP 或域名。如果无法暴露公网，可降级为「飞书推送 + 用户回复关键字」模式。
- **CDP 连接在 agent_runner 运行时可能未就绪** → 启动前检查 Chrome 是否在运行，失败时飞书告警而非静默跳过。
- **每日计划依赖 topic_performance 数据，初期数据稀疏** → 前 4 周 topic_performance 为空时，planner 降级为按默认关键词平均分配配额，不影响流程运行。
- **重生成增加 LiteLLM 调用成本** → 每篇最多 2 次重试，且只在 `quality_issues >= 1`（生成失败）时触发，不对话题无聊的文章浪费 token。
- **飞书 API 限流** → 每日推送量极小（< 10 条卡片），不会触发限流。

## Migration Plan

1. `xhs-feedback-loop` change 部署完成，确认 `xhs_saves` 字段有数据
2. 在飞书开放平台创建应用，获取凭证，写入 `.env`
3. 部署 `agent_strategy.json` 默认配置（等权重，默认阈值）
4. 运行 SQLite schema migration（新增 3 张表）
5. 手动运行 `agent_runner.py --dry-run` 验证感知+规划流程
6. 配置 cron，先观察 1 周日志确认稳定后开启自动发布
7. 4 周后首次运行 `reflection_runner.py` 生成维度建议

**回滚策略**：`agent_runner.py` 仅新增行为（感知+规划+通知），发布仍依赖人工点飞书按钮，即使 runner 出错也不会误发布。可随时从 cron 移除 `agent_runner.py`，系统回退到原手动模式。

## Open Questions

- 飞书 Bot 回调 URL 是否可以是局域网地址（视网络环境而定），还是必须配置公网穿透？
- `account_snapshots` 的粉丝数从哪里抓取？`get_content_data()` 返回的是内容统计，粉丝数需要 `get_profile_snapshot()` 访问自己的主页——需确认该接口是否稳定。
- 每日配额的默认值是多少？建议先定 2 篇/天，根据实际运营经验再调整。
