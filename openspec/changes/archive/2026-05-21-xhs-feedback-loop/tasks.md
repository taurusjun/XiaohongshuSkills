## 1. 数据库 Schema 迁移

- [ ] 1.1 在 `sqlite_db.py` 的 `_ensure_columns` 中新增 `xhs_views / xhs_likes / xhs_saves / xhs_comments / xhs_collected_at` 五个字段（ALTER TABLE 兼容模式）
- [ ] 1.2 验证：启动 web 服务，确认 `news` 表新字段已添加且不影响现有数据

## 2. 创建 scoring_dimensions.json 初始文件

> 此步骤为 xhs-smart-operations-agent 的 P0 前置依赖。scoring_dimensions.json 由本 change 创建并在 feedback-loop 实施完成时可用，后续 agent change 再补全所有维度的完整定义。

- [ ] 2.1 新建 `config/scoring_dimensions.json`，初始内容包含：
  - 「收藏驱动」维度的完整条目（`name/category/direction/default_weight/definition/example_1/example_0/edge_case`，内容来自 `save-drive-dimension/spec.md` 中给出的 JSON 示例）
  - 其余 18 个现有维度的基础条目（只填 `name/category/direction/default_weight`，`definition` 等字段留空或填占位符，触发降级为裸名，由 agent change 统一补全）
  - 文件头含 `"version": "1.0.0"`
- [ ] 2.2 验证：调用 `evaluate_quality` 对一篇文章评分，确认 prompt 中「收藏驱动」维度包含完整四段定义，其余 18 个维度仍为裸名（降级行为正确）

## 3. 收藏驱动评分维度

- [ ] 3.1 修改 `yahoo_common.py:evaluate_quality` — 实现 `build_scoring_prompt(dims)` 函数，从 `scoring_dimensions.json` 动态读取维度定义构造 prompt；文件不存在或维度无 `definition` 时降级为裸名
- [ ] 3.2 将「收藏驱动」追加到维度列表，以等权 +1 计入 `content_score`（不引入 `agent_strategy.json`，等权逻辑由后续 change 统一改为加权）
- [ ] 3.3 确保 `score_dims.value` 写入时使用 `float()` 转换（不强制 `int()`），为后续支持 0.5 预留
- [ ] 3.4 在 `sqlite_db.py:_DIM_DEFS` 中注册 `'收藏驱动': ('内容', '加分')`
- [ ] 3.5 验证：对一篇文章调用 regenerate，确认 `score_dims` 中出现「收藏驱动」记录，`content_score` 有相应变化

## 3. XHS 实发数据回收模块

- [ ] 3.1 新建 `scripts/metrics_collector.py`，实现 `collect_pending_articles()` 函数：查询需要回收的文章（按 4h/24h/72h 时间点判断）
- [ ] 3.2 在 `cdp_publish.py` 中新增 `fetch_note_stats(note_url) -> dict` 方法，通过 CDP 访问笔记页面，提取浏览/点赞/收藏/评论数字
- [ ] 3.3 在 `metrics_collector.py` 中实现写回逻辑：调用 `fetch_note_stats`，更新 SQLite，追加 `xhs_collected_at` 标记
- [ ] 3.4 添加随机抖动（±5min）和错误重试逻辑，失败时记录日志不中断
- [ ] 3.5 验证：手动对一篇已发布文章运行 `python scripts/metrics_collector.py --key <key>`，确认数据回填正确

## 4. Web UI — 列表页收藏率列

- [ ] 4.1 修改 `web/app.py` 的 `/api/news` 接口，在返回 JSON 中追加 `save_rate` 字段（`xhs_saves / xhs_views`，保留两位小数）
- [ ] 4.2 在列表页 HTML 模板中新增「收藏率」列，无数据时显示「—」
- [ ] 4.3 实现列表按收藏率排序（前端 JS 排序，无数据行排末尾）
- [ ] 4.4 验证：浏览器打开列表页，点击收藏率列表头，确认排序生效

## 5. Web UI — 详情页实发数据面板

- [ ] 5.1 修改 `/api/news/<key>` 接口，返回 `xhs_views / xhs_likes / xhs_saves / xhs_comments / xhs_collected_at`
- [ ] 5.2 在文章详情页 HTML 中新增「实发数据」面板区块，展示各指标数字和最后回收时间
- [ ] 5.3 面板中新增「立即回收」按钮，调用新增的 `/api/collect-metrics/<key>` 后端接口
- [ ] 5.4 新增 `/api/collect-metrics/<key>` 端点：启动后台任务，调用 `metrics_collector.py` 抓取单篇文章数据，返回 task_id
- [ ] 5.5 前端轮询任务状态（复用现有 `/api/task/<tid>` 机制），完成后刷新面板数字
- [ ] 5.6 验证：打开一篇已发布文章详情页，点击「立即回收」，确认数据刷新

## 6. 维度相关性分析工具

- [ ] 6.1 新建 `scripts/dimension_analysis.py`，实现查询逻辑：JOIN `news` 和 `score_dims`，过滤有效样本（`xhs_saves > 0` 或 `xhs_comments > 0`，且 `xhs_collected_at` 包含 `72h`）
- [ ] 6.2 实现 Pearson 相关性计算（使用 `scipy.stats.pearsonr`），输出 r 值、p 值、样本数
- [ ] 6.3 实现三目标输出：分别对 `xhs_saves` / `xhs_comments` / `xhs_views` 计算，输出三张 Markdown 格式表格（`--targets` 参数控制，默认全部）
- [ ] 6.4 实现 `--output <file>` 参数，支持将报告写入文件
- [ ] 6.5 验证：运行 `python scripts/dimension_analysis.py`，确认三个目标变量均有输出，样本不足时显示警告；运行 `--targets saves,comments` 确认可按需指定

## 7. Cron 定时任务配置

- [ ] 7.1 在 `README.md` 或项目文档中补充 crontab 配置示例：`0 * * * * cd /path/to/project && python scripts/metrics_collector.py`
- [ ] 7.2 验证：手动执行 cron 命令，确认脚本可在非交互式环境下正常运行（CDP 连接、日志输出）
