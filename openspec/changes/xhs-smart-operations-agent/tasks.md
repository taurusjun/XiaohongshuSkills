## 任务组依赖关系

```
Phase 0（数据字段 + scoring_dimensions.json）
    │
    ├─→ A. 记忆层建表（agent_config/agent_state/topic_performance/account_snapshots）
    │        │
    │        ├─→ B. 评分加权化（依赖 agent_config）
    │        │        └─→ C. 低分诊断（依赖 B）
    │        │
    │        ├─→ D. 趋势扫描（依赖 topic_performance 表）
    │        │        └─→ E. 每日规划器（依赖 A + D）
    │        │
    │        └─→ MCP xhs-operations（依赖所有 DB 表）
    │
    ├─→ F. 飞书 Bot（独立，可并行）
    │
    └─→ G. 智能体主循环（依赖 A/B/C/D/E/F 全部完成）

任务组 1（维度注册表 DB 版本管理）→ P2（推迟）
任务组 8（reflection_runner 统计分析）→ P2（需要 40 篇有效样本）
```

## Phase 0: 数据基础（先做，尽早启动数据积累）

> 这是原 `xhs-feedback-loop` change 的全部内容，合并至此作为最优先的任务组。
> **完成 Phase 0 后即可上线开始积累数据，无需等待后续任务完成。**

- [ ] P0.1 在 `sqlite_db.py` 的 `_ensure_columns` 中新增 `xhs_views / xhs_likes / xhs_saves / xhs_comments / xhs_collected_at` 五个字段（ALTER TABLE 兼容模式）
- [ ] P0.2 新建 `config/scoring_dimensions.json` 初始文件：包含「收藏驱动」维度的完整条目（`name/category/direction/default_weight/definition/example_1/example_0/edge_case`，内容见 `save-drive-dimension/spec.md`）；其余 18 个现有维度填基础字段（`name/category/direction/default_weight`），`definition` 等留空（触发降级为裸名，由后续任务补全）；文件头含 `"version": "1.0.0"`
- [ ] P0.3 修改 `yahoo_common.py:evaluate_quality`：实现 `build_scoring_prompt(dims)` 从 `scoring_dimensions.json` 动态读取定义构造 prompt；「收藏驱动」以等权 +1 计入 `content_score`（不引入 `agent_strategy.json`，加权化由后续任务实现）；`score_dims.value` 写入时使用 `float()` 转换（不强制 `int()`）
- [ ] P0.4 在 `sqlite_db.py:_DIM_DEFS` 中注册 `'收藏驱动': ('内容', '加分')`
- [ ] P0.5 新建 `scripts/metrics_collector.py`：实现 `collect_pending_articles()`，按 4h/24h/72h 时间点判断需要回收的文章，调用 CDP `fetch_note_stats()` 抓取数据写回 SQLite，`xhs_collected_at` 按格式约定追加标记
- [ ] P0.6 在 `cdp_publish.py` 中新增 `fetch_note_stats(note_url) -> dict`，通过 CDP 访问笔记页面提取浏览/点赞/收藏/评论数字
- [ ] P0.7 配置 crontab：`0 * * * * cd /path && python scripts/metrics_collector.py >> logs/metrics.log 2>&1`
- [ ] P0.8 新建 `scripts/dimension_analysis.py`：JOIN `news` 和 `score_dims`，对有效样本（含 72h 数据）分别计算各维度与 `xhs_saves` / `xhs_comments` / `xhs_views` 的 Pearson 相关性；支持 `--targets` 参数选择目标变量，`--output` 写入文件
- [ ] P0.9 Web UI 列表页：新增「收藏率」列（`xhs_saves/xhs_views`），支持排序，无数据显示「—」
- [ ] P0.10 Web UI 文章详情页：新增「实发数据」面板（展示浏览/点赞/收藏/评论 + 回收时间点）；新增「立即回收」按钮调用 `/api/collect-metrics/<key>` 端点
- [ ] P0.11 验证：手动对一篇已发布文章运行 `python scripts/metrics_collector.py --key <key>`，确认数据回填正确；运行 `dimension_analysis.py`，确认三个目标变量均有输出

## 0. 前置确认（不写代码，人工检查 + 运营决策）

> 这里有两类任务：技术前置检查和**上线前必须完成的运营决策**。
> 运营决策不需要写代码，但直接影响系统上线后的内容质量方向。

- [ ] 0.1 在飞书开放平台创建应用，获取 App ID / App Secret，配置事件回调 URL
- [ ] 0.2 确认飞书应用已申请权限：`im:message:send_as_bot`、`im:message.group_at_msg`、消息卡片权限
- [ ] 0.3 将飞书凭证写入 `scripts/.env`：`FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_OPERATOR_OPEN_ID`
- [ ] 0.4 **【运营决策】初始维度权重垂类校准**：在 `scoring_dimensions.json` 中为娱乐垂类设置初始权重——「有用信息」降至 0.4，「收藏驱动」写真/女星话题降至 0.5，「原创度」的 `definition` 改为「视角和组织方式是否有独特性」（而非素材原创性）。**不要等 reflection 来建议，这是运营判断。**
- [ ] 0.5 **【运营决策】确认账号核心锚定话题**：在 `agent_strategy.json` 中设置 `focus_topics`（如 `["写真", "日本女星"]`）和 `growth_stage`（冷启动期设为 `cold_start`）
- [ ] 0.6 **【运营决策】确认每日配额和体裁轮换序列**：在 `agent_strategy.json` 中设置 `cold_start_quota`（建议 3）和 `content_format_rotation`（建议 `["news", "story", "news", "ranking", "news", "story", "comparison"]`）

## 1. 维度注册表与版本管理

- [ ] 1.1 在 `sqlite_db.py` 新增 `scoring_dimension_versions` 表（`id / version / dimensions_json / created_at / created_by / change_note / is_active`）
- [ ] 1.2 创建 `config/scoring_dimensions.json`，包含全部 19 个维度的完整条目（`name / category / direction / default_weight / definition / example_1 / example_0 / edge_case`）作为初始化种子文件
- [ ] 1.3 实现 `sqlite_db.init_dimension_versions()`：若表为空则从 `scoring_dimensions.json` 导入，写入版本 `"1.0.0"` 并设 `is_active=1`
- [ ] 1.4 实现 `sqlite_db.load_active_dimensions() -> list[dict]`：查询 `is_active=1` 行，解析 `dimensions_json`，内存缓存 5 分钟 TTL
- [ ] 1.5 实现 `sqlite_db.commit_dimension_version(dims, change_note, created_by)`：将旧 `is_active` 置 0，插入新版本行并置 `is_active=1`，语义版本号自动递增（patch → minor 视修改范围）
- [ ] 1.6 实现 `sqlite_db.rollback_dimension_version(version)`：切换 `is_active` 到指定历史版本，清空缓存
- [ ] 1.7 修改 `yahoo_common.py:evaluate_quality`，实现 `build_scoring_prompt(dims)` 函数：从 `load_active_dimensions()` 读取定义，动态构造含四段结构的 prompt；定义缺失时降级为裸名
- [ ] 1.8 修改 LLM prompt 输出说明：value 支持 0 / 0.5 / 1，下游解析代码改为浮点
- [ ] 1.9 在 `score_dims` 表新增字段（ALTER TABLE 兼容）：`human_override INTEGER DEFAULT 0` / `human_value REAL` / `override_note TEXT` / `llm_value REAL` / `dim_version TEXT`
- [ ] 1.10 评分时写入 `dim_version`：在 `upsert_score_dims` 中自动附加当前生效版本号
- [ ] 1.11 在 `web/app.py` 新增 `PUT /api/score-dim/<key>/<dimension>` 接口：接收 `{human_value, override_note}`，将原 `value` 移入 `llm_value`，写入纠正值，重算综合分
- [ ] 1.12 Web UI 评分面板：维度支持点击设置 0 / 0.5 / 1，`human_override=1` 时双行显示（灰色 LLM + 金色人工），标注 `dim_version`
- [ ] 1.13 Web UI 新增「评分维度」管理页：展示所有历史版本列表（版本号 / 时间 / 来源 / 变更说明），当前版本高亮，支持「查看详情」和「回滚」操作
- [ ] 1.14 在 `dimension_analysis.py` 查询时优先使用 `human_value`（`human_override=1`），报告头部标注定义版本号
- [ ] 1.15 在 `reflection_runner.py` 新增纠正聚合逻辑：同维度近 4 周纠正 ≥ 3 条时，用 LiteLLM 提炼 `override_note` 共同模式，生成 `edge_case` 修订建议，写入飞书周报
- [ ] 1.16 飞书「采纳定义建议」回调 / Web UI「发布新版本」按钮：调用 `commit_dimension_version()`，清空缓存
- [ ] 1.17 验证：修改「原创度」为 0.5 + 填写理由，确认综合分更新；在版本管理页提交新版本，确认版本号递增；执行回滚，确认 prompt 使用旧定义

## A. 记忆层 — 数据库扩展

> **依赖：** Phase 0 完成（news 表字段已有）。其他任务组均依赖本组完成。

- [ ] A.1 在 `sqlite_db.py` 新增 `topic_performance` 表（`topic / avg_saves / avg_comments / engagement_score / avg_views / post_count / trend_signal / trend_updated_at / discard_count / last_discard_reason / window_days / topic_baseline_saves / competition_count / last_updated`）
- [ ] A.2 在 `sqlite_db.py` 新增 `account_snapshots` 表（`snapshot_date / followers / week_views / week_saves / week_likes / top_note_key / data_completeness`）
- [ ] A.3 在 `sqlite_db.py` 新增 `agent_config` 表（`key TEXT PK / value TEXT / updated_at TEXT`）和 `agent_state` 表（`key TEXT PK / value TEXT / date TEXT / updated_at TEXT`）代替原 `agent_strategy` 单表（配置和运行时状态分离）
- [ ] A.4 实现 `get_top_topics(n)` / `get_recent_performance(days)` / `upsert_topic_performance()` / `get_config(key)` / `set_config(key, value)` / `get_state(key)` / `set_state(key, value, date)` / `cleanup_old_states(days=7)` CRUD 函数
- [ ] A.5 创建 `config/agent_strategy.json` 默认配置（`dim_weights: {}` / `publish_threshold: 3.0` / `retry_threshold: 2.0` / `daily_quota: 2` / `max_daily_quota: 4` / `default_post_times: ["09:30", "12:00", "18:00"]`）
- [ ] A.6 实现 `load_dim_weights()` 从 `agent_config` 表读取，带 1 分钟内存缓存（比较 DB `updated_at` 时间戳，不用 TTL）
- [ ] A.7 验证：启动服务，确认 4 张新表创建成功；`set_config/get_config` 读写正常；`cleanup_old_states` 清理 8 天前的 state 记录

## B. 评分加权化

> **依赖：** Phase 0（scoring_dimensions.json 已创建），任务组 A 完成（agent_config 可读）

- [ ] 2.1 修改 `yahoo_common.py:evaluate_quality` — 用 `load_dim_weights()` 读取权重，计算 `content_score` / `title_score` 改为加权求和后 clamp 到 [0, 5]
- [ ] 2.2 确保权重缺失时默认 1.0，LLM 未返回维度时不报错（`scores.get(dim, 0)`）
- [ ] 2.3 验证：修改 `agent_strategy.json` 设置 `"收藏驱动": 2.0`，对一篇文章 regenerate，确认 `content_score` 有变化且符合预期

## C. 低分诊断与重试逻辑

> **依赖：** Phase 0，任务组 B

- [ ] C.1 新建 `scripts/scoring.py`，实现纯函数 `diagnose_low_score(article, scores) -> Action`（DISCARD / REGENERATE / WAIT_GALLERY / HUMAN_REVIEW），无副作用，方便单元测试
- [ ] C.2 新建 `scripts/agent_tools.py`，实现
- [ ] 3.2 实现 `regenerate_with_hint(article, failed_dims) -> updated_article`，根据 `failed_dims` 选择对应修正提示词注入 generate 调用
- [ ] 3.3 在 `yahoo_common.py:process_news_item` 中，评分完成后调用 `diagnose_low_score`，实现最多 2 次重试循环
- [ ] 3.4 DISCARD 的文章写 `status='discarded', discard_reason=...`；WAIT_GALLERY 写 `pending_gallery=True`；HUMAN_REVIEW 写 `needs_human_review=True`
- [ ] 3.5 `agent_tools.py` 同时封装现有工具：`fetch_by_keywords` / `fetch_recommendations` / `run_gallery_download` / `run_publish_pipeline`（统一接口）
- [ ] 3.6 验证：构造一篇 `啰嗦重复=1` 的测试文章，确认触发重生成；构造 `topic_potential=0` 的文章，确认直接 DISCARD

## D. XHS 趋势扫描

> **依赖：** 任务组 A（topic_performance 表已建）

- [ ] 4.1 新建 `scripts/xhs_trend_scanner.py`，实现 `scan_topic_trends(keywords: list[str]) -> list[dict]`，内部调用 `search_feeds(sort="最多收藏", limit=10)`
- [ ] 4.2 提取 TOP 10 笔记的：标题列表、`recommended_keywords`、图文/视频比例、平均标题长度
- [ ] 4.3 实现结果写入 `topic_performance.trend_signal`（JSON 字符串）
- [ ] 4.4 CDP 未就绪时捕获异常，记录日志并返回空结果（不抛出）
- [ ] 4.5 验证：手动运行 `python scripts/xhs_trend_scanner.py --keywords "写真集,美人"`，确认数据写入 DB

## E. 每日规划器

> **依赖：** 任务组 A（数据层）、任务组 D（趋势数据）

- [ ] 5.1 新建 `scripts/agent_planner.py`，实现 `plan_today() -> DailyPlan` 数据类（`topics: list[TopicQuota], post_times: list[str], quota_total: int, mode: str`）
- [ ] 5.2 实现话题分配逻辑：70% 高表现（`avg_saves DESC`）/ 20% 探索（`trend_signal` 有新话题）/ 10% 保底（默认关键词）
- [ ] 5.3 实现配额动态调整：近7天 avg_saves 与历史均值对比，±1 篇调整，不超过 `max_daily_quota`
- [ ] 5.4 实现历史最优发布时间推荐（不足10篇时降级到 `default_post_times`）
- [ ] 5.5 实现今日计划幂等存储：写入 `agent_strategy` 表 key=`daily_plan_YYYYMMDD`
- [ ] 5.6 验证：运行 `python scripts/agent_planner.py --date today --print`，确认输出合理计划

## F. 飞书 Bot 集成

> **依赖：** 独立，可与其他任务组并行

> 使用开放平台 Bot + 交互卡片。服务器有公网 IP，直接作为回调地址。

- [ ] 6.1 飞书开放平台创建应用，开通权限（发送消息、接收消息事件），配置事件回调 URL 为 `http://<server_ip>:<port>/webhook/feishu`
- [ ] 6.2 新建 `scripts/feishu_bot.py`，实现 `get_tenant_token()` 含内存缓存（2小时 TTL 自动刷新）
- [ ] 6.3 实现 `send_text(open_id, text)` 和 `send_card(open_id, card_json)` 基础方法（POST to `/im/v1/messages`，5s 超时，失败仅记日志）
- [ ] 6.4 实现 `build_daily_approval_card(candidates)` — 生成含每篇文章信息和「发布/跳过/重新生成」按钮的交互卡片 JSON
- [ ] 6.5 实现 `build_weekly_report_card(report, weight_suggestions)` — 生成含「一键采纳」按钮的周报卡片 JSON
- [ ] 6.6 在 `web/app.py` 新增 `/webhook/feishu` POST 路由：处理 challenge 验证、验证签名、分发 card action（approve / skip / regenerate / adopt_weights）
- [ ] 6.7 实现 `send_alert(text)` 便捷方法，用于 CDP 失败/连续丢弃告警
- [ ] 6.8 在 `scripts/.env` 新增 `FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_OPERATOR_OPEN_ID / FEISHU_WEBHOOK_SECRET`，未配置时所有方法静默返回
- [ ] 6.9 验证：手动运行 `send_text` 确认消息到达；发送测试卡片并点击按钮，确认回调触发且 DB 中 `publish_xhs` 字段正确更新

## G. 智能体主循环

> **依赖：** 任务组 A/B/C/D/E/F 全部完成

- [ ] 7.1 新建 `scripts/agent_runner.py`，实现主流程：感知（趋势扫描+账号快照）→ 规划（今日计划）→ 执行（按计划抓取+生成+低分处理）→ 通知（推送飞书审批卡片）
- [ ] 7.2 实现进度持久化：每完成一个阶段写 `agent_strategy` 表（key=`runner_progress_YYYYMMDD`），重复运行时跳过已完成阶段
- [ ] 7.3 实现 `--dry-run` 模式：完整走流程但不写 DB、不发飞书
- [ ] 7.4 账号快照实现：调用 `get_profile_snapshot(user_id=XHS_MY_USER_ID)` 获取粉丝数（含「万」格式解析降级），调用 `get_content_data()` 获取近7天浏览/收藏数据，合并写入 `account_snapshots`
- [ ] 7.5 配置 crontab：`0 7 * * * cd /path/to/project && python scripts/agent_runner.py >> logs/agent.log 2>&1`
- [ ] 7.6 验证：运行 `--dry-run`，确认打印完整计划；去掉 `--dry-run`，确认飞书收到审批卡片，点击按钮后 DB 中 `publish_xhs` 字段更新

## 8. 每周反思循环（P2 — 需要至少 40 篇有效数据，冷启动期可跳过）

> 每日 2 篇配额下，积累 40 篇有效数据约需 20 周。冷启动期优先完成 P0 任务，此组任务在系统稳定运营 8 周后再实施。

- [ ] 8.1 新建 `scripts/reflection_runner.py`，实现主流程：更新 `topic_performance` → 运行维度相关性分析 → 生成权重建议 → 用 LiteLLM 生成自然语言周报 → 推送飞书周报卡片
- [ ] 8.2 实现 `update_topic_performance()`：对每个话题查询含 24h 数据的文章，滚动计算加权平均（近期权重 1.5，旧数据权重 1.0）
- [ ] 8.3 实现权重建议生成逻辑：r > 0.4 且 p < 0.05 → weight + 0.5；r < 0.1 或 p > 0.05 → weight - 0.3（下限 0.1）；存 `agent_strategy` 表 key=`pending_weight_suggestion`
- [ ] 8.4 实现周报生成：**数值统计部分（发布篇数/总浏览/总收藏/话题对比表）用模板填充，不调用 LLM**；只有「本周亮点摘要」和「下周建议」两段（各 50-100 字）通过 LiteLLM 生成，temperature=0.5，返回 `{"highlights": "string", "suggestions": "string"}`
- [ ] 8.5 配置 crontab：`0 23 * * 0 cd /path/to/project && python scripts/reflection_runner.py >> logs/reflection.log 2>&1`
- [ ] 8.6 验证：手动运行 `python scripts/reflection_runner.py`，确认飞书收到周报卡片，点击「一键采纳」后 `agent_strategy.json` 的 `dim_weights` 更新

## 9. 长文翻页抓取与 story 体裁信号

- [ ] 9.1 在 `yahoo_news_fetcher.py` 或 `yahoo_common.py` 的文章内容提取逻辑中，增加翻页检测：检查页面底部是否存在「次のページへ」按钮或分页 URL 参数
- [ ] 9.2 实现分页内容依次抓取并拼接：最多抓取 5 页（防止无限分页），各页正文按顺序合并，写入 `news.content_ja`
- [ ] 9.3 在 `news` 表新增 `is_long_form BOOLEAN DEFAULT 0`、`page_count INTEGER DEFAULT 1`、`page_fetch_error BOOLEAN DEFAULT 0` 字段
- [ ] 9.4 在体裁前置判断逻辑中，`is_long_form=True` 时自动将 `story` 加入 `format_suitability`（不依赖 LLM 判断）
- [ ] 9.5 验证：找一篇 Yahoo Japan 有翻页的访谈类文章，确认所有页面内容被拼接、`is_long_form=True`、`format_suitability` 包含 `story`

## 10. 话题标签三层策略

- [ ] 9.1 修改 `yahoo_common.py:generate_content_and_comment`，在生成 prompt 中追加「三层话题标签」输出（精准/垂类/泛流量），JSON 格式：`{precise: [], category: [], broad: []}`
- [ ] 9.2 修改 `news` 表（或 `tags` 字段结构）：`tags` 改存三层结构 JSON，向下兼容旧的逗号分隔格式
- [ ] 9.3 在 `trend_scanner.py` 扫描时，同步统计各话题关键词在 XHS 的近 7 天新发笔记数，写入 `topic_performance.weekly_post_count` 作为竞争强度信号
- [ ] 9.4 在生成 prompt 中注入竞争强度：`weekly_post_count > 5000` 的泛标签标记「高竞争，谨慎使用」
- [ ] 9.5 在 Web UI 文章详情页展示三层标签，支持运营者逐层编辑；标签总数 > 8 时显示警告
- [ ] 9.6 验证：生成一篇写真集资讯，确认精准标签包含艺人名、垂类标签包含「日本写真」类标签、泛标签根据内容质量决定是否添加

## 10. 整体集成验证

- [ ] 10.1 端到端 dry-run：`agent_runner.py --dry-run`，确认感知→规划→执行→通知全流程无报错
- [ ] 10.2 真实运行一次（非 dry-run），确认飞书收到审批卡片，点击发布后文章按时发出
- [ ] 10.3 验证体裁前置判断：抓取一篇「简单活动通知」，确认 `format_suitability` 只含 `news`，不强行套用轮换目标体裁
- [ ] 10.4 验证标签策略：发布的文章标签包含三层结构，精准标签有艺人名
- [ ] 10.5 验证回滚：从 crontab 移除 `agent_runner.py`，确认系统退回原手动模式，Web UI 功能完整
- [ ] 10.6 在项目 README 补充「智能体模式」启动说明、飞书 Bot 配置步骤、上线前运营决策清单

- [ ] 9.1 端到端 dry-run：`agent_runner.py --dry-run`，确认感知→规划→执行→通知全流程无报错
- [ ] 9.2 真实运行一次（非 dry-run），确认飞书收到审批卡片，点击发布后文章按时发出
- [ ] 9.3 `reflection_runner.py` 运行后确认 `topic_performance` 数据更新，权重建议合理
- [ ] 9.4 验证回滚：从 crontab 移除 `agent_runner.py`，确认系统退回原手动模式，Web UI 功能完整
- [ ] 9.5 在项目 README 补充「智能体模式」启动说明和飞书 Bot 配置步骤
