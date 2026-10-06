# docs 索引

本目录存放 `XiaohongshuSkills` 的设计文档、调查结论与运行手册。

## 链路与运维

| 文档 | 内容 |
|---|---|
| [agent_runbook.md](agent_runbook.md) | 内容流水线的运行指南 |
| [auto-publish-pipeline.md](auto-publish-pipeline.md) | 自动发布链路全解：`/api/trigger-publish` → 后台子进程 → `publish_pipeline.py` → `cdp_publish.py` 的 CDP 浏览器自动化 |
| [cdp-content-writing.md](cdp-content-writing.md) | CDP 如何向页面写入内容：标题/正文/图片/话题标签/定时时间各自的写法与原因，含改版排查顺序 |
| [known-issues.md](known-issues.md) | 已证实的稳定性问题清单（含证据、复现命令、修法选项、当前状态） |
| [wechat-editor-image-block.md](wechat-editor-image-block.md) | 公众号编辑器图片块：3 个 bug（块类型无工具 / 官方 ImageTool 不可换图 / 素材库混入远程封面 URL）+ 2 个坑（EditorJS 异步加载 vs 类声明求值顺序、CDP 探测触发 4s 自动保存污染数据） |
| [keyboard-free-topic-tags.md](keyboard-free-topic-tags.md) | 话题标签写入方案：Tiptap 事务直插（Tier 1）+ 敲字兜底（Tier 2）+ `topic_cache` 表 |

## 迁移

| 文档 | 内容 |
|---|---|
| [docker-migration-plan.md](docker-migration-plan.md) | **方案（未实施）**：从 macOS 迁到 Linux Docker —— 双容器拓扑、扫码登录接 admin UI、Hermes 一并迁移、代码改造清单、备份改造、迁移步骤与风险清单 |

## 其它

| 文档 | 内容 |
|---|---|
| [claude-code-integration.md](claude-code-integration.md) | Claude Code 集成指南 |
| [en_pipeline_architecture.md](en_pipeline_architecture.md) | Twitter/英文分发表 — 完整管线架构 |
| [research_english_jpop_market.md](research_english_jpop_market.md) | 英文日本娱乐赛道调研报告 |
| [bilibili_ai_agent_content_strategy.md](bilibili_ai_agent_content_strategy.md) | B站 AI Agent 内容创作策略 |
| [bilibili_ep1_ai_wrote_my_cdp_script.md](bilibili_ep1_ai_wrote_my_cdp_script.md) | B站 EP1 逐字稿：我怎么让 AI 帮我写了一整套浏览器自动化 |
| [code-review-2026-03-07.md](code-review-2026-03-07.md) | RedBookSkills 代码审阅报告 |
| [SYNC_UPSTREAM.md](SYNC_UPSTREAM.md) | 同步原仓库更新 |
| [20260521/](20260521/) · [20260522/](20260522/) | 2026-05 设计与评审归档 |
| [mockup_admin.html](mockup_admin.html) · [xhs_agent_arch.html](xhs_agent_arch.html) | 原型 / 架构图 |

## 数据快照

本文档中的数字取自 **2026-10-05** 对生产库 `data/news_dev.db` 的只读查询。
库是活的（有定时采集与排期发布），数字会变 —— 引用前请按下文命令重新取数。

## 结论分级约定

- ✅ **已证实** — 有代码行号、git 提交或 SQL 查询结果直接支撑
- ⚠️ **待验证** — 只从代码读出的风险，尚未在生产数据中观测到
- ❓ **未知** — 需要业务方确认的设计意图

## 重新验证的方法

```bash
cd ~/PG/XiaohongshuSkills
sqlite3 data/news_dev.db "SELECT COUNT(*) FROM news WHERE publish_xhs=1 AND status='active';"
```

> 严禁对 `*.db` 做文本/字节操作，只能用 `sqlite3` CLI 或 Python `sqlite3` 模块（见根目录 `CLAUDE.md` 规则 9）。
