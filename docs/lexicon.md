# 术语表（lexicon）——日→中专名整体映射

**一句话**：把「日文专有名词」按**整体**映射到中文（`translate`）或**保留不译**（`keep`），统一由一套 DB 表管理，写稿**前置注入** + 事后兜底 + 门禁，并支持**自动判定 + 人工审 + UI 编辑**。

## 单元与动作
- 单元 = **专有名词整体**（人名/团体/作品/剧/曲/节目/舞台名）。例：`春よ来い、マジで来い` 整体译为 `春来了，真的来了`，**不拆**成 `マジで`。
- 动作二值：`translate`（给中文）/ `keep`（拟声/neta 等不可译 → 原样保留）。
- 不再用「连续假名 run」当词条（那是碎片 `マジで/から/あり` 的根源）。

## 存储（DB 为真源）
`news_dev.db` 两表：
- `lexicon(term, type, action, zh, aliases, status, source, src_key, note, created_at, updated_at)`
  - `type`: person|group|work|word|variant|font|keep
  - `status`: pending|auto|manual|rejected（`manual`>`auto`；生效读取 manual+auto）
- `lexicon_audit(id, term, old_json, new_json, actor, reason, ts)`

`config/lexicon/manual/*.json` 仅作 **seed/export 快照**（进 git）；`cli lexicon export` 生成。

## 服务 `services/lexicon.py`
`inject`（本文命中选注，绝不全量）/ `replace`（只替 translate，长词优先）/ `gates`（假名扣 keep、新字体）/ `normalize`（实体归一）/ `discover`·`discover_names`（发现候选→pending）/ `add`·`update`·`delete` / `pending`·`judge`·`promote`·`revert` / `seed_from_config`·`export_config`。

## 流程接入
- **fetch/review**：实体抽取后 `discover_names`（未在表→pending）+ `normalize` 归一。
- **write**：`_compose_msgs` 调 `inject()` 把本文命中术语注入 prompt；`_produce` 用 `replace()` 兜底；`precheck` 假名门禁扣 `keep_spans`；写后 `discover()` 发现新专名。
- **自增长**：pending → `judge()`(LLM 判定 type/action/zh，带碎片护栏) → `status=auto`（**直接生效**）→ 在「📚 术语表」待审 tab 人工 保留(→manual)/改/撤回。

## Admin UI
左侧「📚 术语表」：按 type/status 过滤 + 搜索；新增/编辑/删除；待审「保留/撤回」；「🤖 自动判定待审」「⬇ 导出快照」。

## 迁移
`ops/lexicon_migrate.py`（一次性）：旧 5 表按规则自动分类入库（`kana_replace/artist_name_map/ja_localize/name_variants/newfont` → names/works/words/variants/fonts/keep），判不准→pending，碎片丢弃。旧文件已删除。

## 工具
- `ops/lexicon_audit.py`：体检（状态/类型分布、无中译、alias 冲突）。
- `cli lexicon seed|export|list|pending|judge|add|revert`。
