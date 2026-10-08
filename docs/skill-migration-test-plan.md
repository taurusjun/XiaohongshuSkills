# review/写稿 skill 迁移 · 测试计划

> 配套 [docker-containerization-plan.md](docker-containerization-plan.md) 的 **P4（能力服务化）**。
> 目标：把 `~/.hermes/skills` 里的 review/写稿类 skill 迁入仓库、脱离 Hermes、暴露为 CLI/REST/MCP，
> 且**行为与现状一致**。本文件定义"怎么证明它没坏"。

---

## 0. 为什么这个任务难测（风险画像）

| 风险 | 说明 |
|---|---|
| **含 LLM 推理** | 写正文、5 维评分、素材聚类、推荐排序——输出非确定，无法用传统单测覆盖 |
| **规则极重** | `xhs-write-publish-flow/SKILL.md` 1400+ 行 + 140 篇 references，全是隐式判据（假名、顿号、体裁路由、密度…） |
| **多写方 SQLite** | 现状 webapp / MCP / metrics / fetcher / Hermes / skill 里的 `sqlite3 UPDATE` 都在写同一个库 |
| **三接口一致性** | CLI / REST / MCP 必须调同一 service，结果一致 |
| **交付渠道变更** | Telegram `segment-send` → 飞书 / web UI |
| **触碰生产数据** | `news` 表（394MB 主库），迁移期不能破坏 |

**结论**：不能只靠"跑通了"，必须建立**分层测试 + 黄金集回归 + LLM 机械门禁**。

---

## 1. 测试分层与门槛（L0–L8）

| 层 | 内容 | 工具 | 通过门槛 |
|---|---|---|---|
| **L0 静态** | 语法、无宿主硬编码、无密钥、无 Telegram 残留 | `py_compile` / `ruff` / `grep` | 0 命中 |
| **L1 单元** | `services/` 纯逻辑函数（不碰 DB/网络） | pytest | 覆盖核心分支 |
| **L2 契约** | CLI / REST / MCP 三皮**同输入同输出** + MCP schema 校验 | pytest + jsonschema | 三皮一致 |
| **L3 集成** | services ↔ SQLite（**只读快照库**） | pytest + 快照 DB | 字段级一致 |
| **L4 特征化/黄金** | 迁移前后对同一输入输出对比 | golden 文件 | 无差异（或已评审差异） |
| **L5 并发/一致性** | 单写方、`busy_timeout`、无丢失更新 | 多进程压测 | 0 崩溃 / 0 丢失 |
| **L6 LLM 在环** | 写稿/review 的机械门禁 + 稳定性 | 固定种子 + `batch_precheck` | 机械指标达标且方差可控 |
| **L7 端到端** | 一条素材 review→写稿→入库→dry-run 发布 | 脚本编排 | 全绿、**不触发真发布** |
| **L8 安全/合规** | 不误发、密钥不入库、渠道正确 | 断言 + grep | 0 违规 |

**每层是下一层的前置**：L0–L5 全绿才允许跑 L6；L6 抽检通过才允许 L7。

---

## 2. 逐 skill 测试矩阵

| skill | 确定性脚本（→ services，可单测） | 需 LLM 的部分（L6） |
|---|---|---|
| `xhs-write-publish-flow` | `write-api.py` `pub_time_plan.py` `batch_precheck.py` `dump_ja.py` `xhs_word_count.py` `batch_import_drafts.py` `normalize-dunhao.py` `fix_dunhao_lines.py` `verify_rewrite.sh` `get-key.sh` | 写正文、体裁路由、gzh 路由、改稿循环 |
| `xhs-daily-material-review`(+layer1/23/4) | `validate-tables.py` `segment-send.py`→飞书 | 全量扫描、聚类、S/A 分级、跨时间关联、L4 回顾 |
| `xhs-content-review` | — | 5 维评分、改稿建议 |
| `xhs-publish-workflow` | `db-queries.md` 里的查询 → query service | 发布节奏盘点、下一篇推荐 |
| `xhs-review-self-audit` | — | 四步审计 |

**原则**：**能变成 service 的绝不留成"提示词里的手工命令"**；剩下纯 LLM 推理的部分，用机械门禁兜底。

---

## 3. 黄金集与夹具（Fixtures）

1. **冻结 DB 快照**：`VACUUM INTO` 一份 `news_dev.db`，只读挂载给测试（禁写生产库）。
2. **固定素材样本**：S / A / B 级 + story / news + 长文(export) / 图文(post) 各若干，覆盖边界（假名、顿号、密度、合并稿）。
3. **现有脚本输出作 golden**：迁移前对每篇样本跑 `batch_precheck` / `xhs_word_count` / `pub_time_plan`，把 stdout+入库字段存为 golden。
4. **期望产物**：字段级断言（`rewritten_title` 长度、`##` 数、`publish_method`、`preselected`、`publish_xhs`、`xhs_pub_time`）。

---

## 4. 契约测试细则（三张皮一个芯）

- **同芯**：`services/write_service.write(...)` 唯一实现；CLI/REST/MCP 都是薄壳。
- **一致性**：对同一输入，三皮返回**规范化后相同**的结果（忽略传输包装差异）。
- **MCP schema**：每个 tool 的入参/出参用 jsonschema 校验；`fields` 裁剪、`limit/offset` 分页正确；列表默认**不返回** `content_ja` 全文。
- **写路径收敛**：断言 **无任何 service 之外**的 `sqlite3` 写；`grep -r "sqlite3.*UPDATE\|INSERT" services cli mcp_servers` 只允许出现在 `services/` 的 DB 层。
- **key 完整性**：入参 key 必须 40 位（防"截短 key = 无声失败"回归）。

---

## 5. LLM 在环评测方法（L6）

- **固定条件**：`temperature=0`、固定模型、固定 prompt/输入快照、固定随机种子（`pub_time_plan --seed`）。
- **机械断言（硬门禁，直接复用 `batch_precheck`）**：
  - story lf=1：正文 ≥800 字且 `## ` ≥2；news：`## ` ==0
  - 假名 ≤5；日文新字体 0（人名白名单除外）
  - 顿号行 ==0；密度 = 正文/`content_ja` ≥30%
  - 标题 ≤20 字；`publish_method` 与 `content_ja` 字数路由一致
- **稳定性**：同输入重复 N 次，机械指标**方差 ≤ 阈值**（防止"偶尔过、偶尔炸"）。
- **人工抽检**：机械门禁全过 ≠ 可发；每批抽 1–2 篇人工读（rubric 见 `xhs-content-review`）。

---

## 6. 迁移保真测试

- **文本保真**：`skills/` 迁入 git 后，`SKILL.md` 与 `references/` 内容 **hash 与 `~/.hermes` 一致**（只搬家，不改语义）。
- **脚本保真**：迁移前后，对同一输入输出**逐字节/逐字段一致**（用 §3 golden）。
- **去耦合**：断言不再出现 `skill_view` / `delegate_task` / `segment-send.py`→Telegram；路径不再依赖 `~/.hermes`，改由 env/配置注入。

---

## 7. 并发与单写方（L5）

- 多进程同时写：`busy_timeout=30000` 下无 `SQLITE_BUSY` 崩溃。
- **丢失更新测试**：MCP 与 REST 同时改同一条 → 结果可解释、无静默覆盖。
- 读并发：WAL 下查询不受写阻塞。

---

## 8. 端到端与回滚（L7）

- **dry-run 全链路**：review → 写稿 → 入库 → `publish_pipeline --preview` 填表停住。
- **硬断言**：**禁止** `POST /api/trigger-publish`；无 `PUBLISH_STATUS: PUBLISHED`。
- **回滚演练**：迁移出问题时，Hermes 版 skill 仍可原样运行（保留 `~/.hermes` 不改，直到验收）。

---

## 9. 测试环境与组织

- **环境**：容器内（隔离 DB 卷，最贴近生产 Linux）+ Mac venv 双跑。
- **目录**：`tests/services/` · `tests/contracts/` · `tests/e2e/` · `tests/llm_eval/`
- **现状**：现有 38 用例须持续通过；`tests/test_gap1_collected_at.py` 收集失败（引用已移除函数）→ 迁移中一并修或明确隔离。
- **纪律**：遵守项目规则 1（改完必须实跑自测）、规则 7（先复现再改）。

---

## 10. Definition of Done（P4 交付门槛）

1. L0–L5 全绿，纳入 `pytest -q`
2. L6：固定样本批 ≥ 90% 一次通过机械门禁，重复运行方差达标
3. L7：端到端 dry-run 全绿，且**零误发**
4. L8：无密钥入库、无 Telegram 残留、交付走飞书/web
5. 迁移保真：`SKILL.md`/脚本 hash 与 golden 一致（或差异已评审签字）
