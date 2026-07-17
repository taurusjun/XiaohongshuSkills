## Why

关键词配置散落在 4 处（agent_strategy.json、DEFAULT_KEYWORDS 硬编码、custom_keywords.json、agent_runner 自写抓取循环），导致维护不同步、无 Web UI 管理入口、所有话题共用单一配额。

## What Changes

1. **yahoo_keyword_map 扩展** — 从 `{topic: keyword_str}` 改为 `{topic: {keyword, max}}`，每个话题独立配额
2. **去硬编码** — yahoo_news_auto_sqlite.py 和 /api/keywords 改为读 agent_config
3. **agent_runner 统一** — Phase 3 放弃自写循环，改为调用 yahoo_news_auto_sqlite.py
4. **Web UI 配置面板** — 管理页新增策略配置区域，可编辑话题/搜索词/配额
5. **向后兼容** — 旧格式自动升级

## Capabilities

### Modified

- `agent-config` — yahoo_keyword_map 结构扩展，Web UI 管理入口
- `agent-runner` — Phase 3 统一调用 yahoo_news_auto_sqlite.py
- `keyword-fetch` — yahoo_news_auto_sqlite.py 去硬编码
