## 1. agent_strategy.json 结构升级 + 兼容

- [x] 1.1 yahoo_keyword_map 改为 `{topic: {keyword, max}}` 格式
- [x] 1.2 _seed_test_config() 检测旧格式自动升级（topic → {keyword: topic, max: daily_quota}）
- [x] 1.3 验证旧格式 DB 数据自动升级通过

## 2. yahoo_news_auto_sqlite.py 去硬编码

- [x] 2.1 无 `--keywords` 参数时从 agent_config 读 focus_topics + yahoo_keyword_map
- [x] 2.2 DEFAULT_KEYWORDS 加 deprecated 注释
- [x] 2.3 验证无参运行使用 agent_config 数据

## 3. agent_runner Phase 3 统一调用

- [x] 3.1 删除 Phase 3 自写抓取循环
- [x] 3.2 改为 subprocess 调用 `yahoo_news_auto_sqlite.py --keywords <json>`
- [x] 3.3 从 agent_config 构造 keywords JSON（含各自 max）
- [x] 3.4 保留飞书通知 + topic_performance 更新

## 4. Web UI /api/keywords 数据源切换

- [x] 4.1 `/api/keywords` 改为读 agent_config
- [x] 4.2 返回新结构 `[{topic, keyword, max}]`
- [x] 4.3 前端适配新结构

## 5. Web UI 配置管理面板

- [x] 5.1 新增 `/api/agent-config` GET/PUT
- [x] 5.2 管理页新增配置区域（话题/搜索词/配额表格）
- [x] 5.3 支持增删行，修改后写入 agent_config 即时生效

## 6. 端到端验证

- [x] 6.1 Web UI 关键词面板显示 agent_config 数据
- [x] 6.2 yahoo_news_auto_sqlite 无参运行正确
- [x] 6.3 agent_runner --dry-run 无报错
- [x] 6.4 Web UI 配置面板修改即时生效
