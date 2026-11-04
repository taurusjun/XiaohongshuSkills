# 标题门禁迁移（Hermes skill → 容器 pipeline）

> 2026-11-04 · 分支 `docker-containerization`
> 背景：Hermes 的 `xhs-write-publish-flow` 针对**改写文标题**做了一次更新（10/31），
> 容器 pipeline 的代码门禁与 prompt 尚未同步。本文记录规则落位、LLM/硬流程边界、验证方案。

---

## 一、规则来源

Hermes `~/.hermes/skills/creative/xhs-write-publish-flow` 新增/更新：

| 文件 | 内容 |
|---|---|
| `references/title-generation-protocol.md`（新） | 三步生成法：选落点 → 选锚点 → 写 3 条候选再选；四问打分 |
| `references/title-failure-modes-casebook.md`（新） | 5 种失败形态实锤 + `merge_coverage.py` 校准值 |
| `references/title-name-anchor-standard.md`（更新） | 第八~十节：无主角名、空壳落点、黑话当锚点 |
| `scripts/batch_precheck.py`（更新） | 新增 `SHELL_*` / `JARGON_TITLE` / `JP_LOANWORD_RE` / `JP_TERM_BODY` |
| `scripts/scan_title_gates.py`（新） | 全库回归，量化误伤；FAIL 0 误伤才允许开门禁 |
| `references/chinese-review-criteria.md`（更新） | 标题扣分：空壳/术语/昵称最高 1 分 |

全库实测（Hermes 侧 2247 条 `rewritten_title`）：

```
空壳 FAIL    0 条
泛反应 WARN 11 条
黑话 FAIL    1 条
```

---

## 二、现有分层（prompt vs 硬门禁）

| 链路 | prompt（判断） | 硬门禁（代码） |
|---|---|---|
| DB 标题生成（抓取） | `config/prompts.py` `TITLE_RULES_*`（`scripts/yahoo_common.py` 使用） | 无 |
| 改写文生成 | `agent/prompts/write.md`（43 行）+ user 写作包 | `services/precheck.py` → `renwei` → `gzh_review` → 评分 |
| 日报/日批（orchestrator/Hermes） | `skills/.../SKILL.md`（1441 行，整日任务） | 指令要求过 `services.precheck` |
| 评分 | `skills/.../reviews/chinese-review-prompt.md`（2020 字符） | S/A 阈值在 `agent/write.py` |
| 术语表 | — | `services/lexicon.py`（生成前 `inject` / 生成后 `replace`） |

**硬门禁的代价**：`agent/write.py` 的 `precheck problems` 会触发最多 3 轮改稿，
误伤一次 = 三倍 LLM 成本。所以新规则必须先全库回归、0 误伤才开 FAIL。

---

## 三、LLM-only vs 可硬流程

`config/skill_audit/items.json` 的 106 条 `process` 项中：

| 类型 | 数量 | 说明 |
|---|---|---|
| **必须 LLM** | ~14（13%） | 生成正文/改稿/压缩、语义聚类、分级选题、质量评分、合并角度、事实取舍、假名人名译法 |
| **可硬流程** | ~92（87%） | 顺序闭环、批处理/拆分、key 处理、读取查询、机械检查、入库验证、改稿轮次、交互纪律 |

本次标题规则里：

| 规则 | 落位 |
|---|---|
| ≤20 字 / 代词开头 / 空壳落点 / 黑话无锚点 / 日文商品词 / 正文日文缩写 | **硬门禁**（`services/precheck.py`） |
| 3 条候选再选 1 条 | **可硬流程**（二期：LLM 出 `titles[]`，代码选第一条过门禁的） |
| 落点要画面 / 黑话≤1 / 全名 | **prompt**（`agent/prompts/write.md`，3 行） |
| 四问打分 | **prompt**（`reviews/chinese-review-prompt.md`） |
| 无主角名 | 启发式 WARN（二期） |
| 同批句式疲劳 / 标题-正文一致性 | 硬流程（二期） |

---

## 四、本次改动

### 4.1 技能同步（19 个文件）

新增 8：

```
references/title-generation-protocol.md
references/title-failure-modes-casebook.md
references/10-28-single-day-batch-flow.md
references/10-29-single-day-batch-flow.md
references/10-30-single-day-batch-flow.md
scripts/scan_title_gates.py
scripts/merge_coverage.py
scripts/verify_scheduled.py
```

覆盖 11：

```
SKILL.md
references/title-name-anchor-standard.md
references/chinese-review-criteria.md
references/7-29-api-put-silent-fail-sqlite3-workaround.md
references/longform-vs-900-routing.md
references/merge-update-existing-draft.md
references/naming-translations.md
references/operations-guide.md
references/publish-recommendation-step.md
scripts/batch_precheck.py
scripts/pub_time_plan.py
```

跳过 `10-31` / `11-02`（无引用；内容已蒸馏进 casebook / SKILL.md）。

### 4.2 术语表（`config/lexicon/manual/words.json`）

| 词 | 中文 |
|---|---|
| 别注 / 別注 | 特别定制 |
| 受注 | 接单 |
| 完売 | 售罄 |
| 入荷 | 到货 |
| 予約 | 预订 |
| 予約受付 | 预约受理 |
| 数量限定 | 限量 |
| W中心 | 双中心 |
| 立ち位置 | 站位 |

- 词表在 `_lx.replace()` 中生效（标题 + 正文，`action=translate`）
- 生成前 `_lx.inject()` 也会把命中的日文原词注入 prompt
- 正则门禁保留，作为**表外新词**的兜底
- 不进词表：黑话（语境依赖）、昵称（正文允许，全文替换会误伤）、空壳落点（非词问题）

### 4.3 硬门禁（`services/precheck.py`）

在标题长度检查之后加入：

```
代词开头                      FAIL
空壳落点（发/晒/回了+类别名词） FAIL；泛反应词 WARN
黑话无锚点                    FAIL；≥2 个但锚点够 WARN
日文商品词                    FAIL
正文日文缩写                  FAIL
```

渠道范围：xhs 全用；gzh 只套「日文商品词 + 正文日文缩写」。

锚点判断（无 plan name 时）：`《》` / `xx46` / 团名白名单 —— 与 `scan_title_gates.py` 同口径。

### 4.4 提示词

| 文件 | 改动 |
|---|---|
| `agent/prompts/write.md` | 【标题】段 +3 行：3 候选 / 落点画面 / 黑话≤1+全名 |
| `reviews/chinese-review-prompt.md` | 标题 2 分 → 四问各 0.5；空壳/术语/昵称上限 1 分 |

---

## 五、验证

### 5.1 全库回归（上线前必做）

```bash
cd skills/creative/xhs-write-publish-flow/scripts
XHS_API_BASE=http://127.0.0.1:5000 python scan_title_gates.py --limit 12000 --show 20
```

判据：

- `shell` FAIL 必须 0
- `jargon` / `loan` 逐条人工看；有误伤 → 降 WARN 或收窄
- 记录 FAIL/WARN 条数进提交信息

### 5.2 单测

`tests/test_title_gates.py`：

- 负样本（应 FAIL）：四个失败案例
- 正样本（应 PASS）：`一句台词，两种表情`、`日向坂46新单首周52.3万张再夺冠`、`伊藤理々杏毕业前站C位，歌名叫《编辑》`
- 词表：`别注` 被替换为 `特别定制`

### 5.3 指定稿件实跑

```bash
docker exec xhs bash -lc 'cd /home/user/PG/XiaohongshuSkills && \
  .venv/bin/python -m cli write-full --dry-run --key 8d7303ea0ee0d99c1de2cf443971a00a2aa6416e'
```

检查点：

- 标题不再出现空壳/日文商品词/无锚点黑话
- 正文无 `W中心` / `センター`
- precheck problems 为空
- 不改库（dry-run）

---

## 六、二期

1. **3 候选硬选择**：LLM 输出 `titles: [画面型, 对位型, 引语型]`，代码按门禁取第一条
2. **同批句式疲劳**：同批标题前 4 字重复 ≥3 → WARN
3. **标题-正文一致性**：标题实词在 `rewritten_content` 命中率 <60% → WARN
4. **无主角名**：`material_type` 单人 + story → WARN
5. **merge_coverage 接入评分**：related_keys 非空时必跑，采纳 0 → 否决
6. **审计表三态**：`code / judgment / deprecated` 替代现有 `code / process`

---

## 七、回滚

只涉及代码与词表，无数据迁移：

```bash
git revert <commit>          # 代码/提示词
python -m services.lexicon revert <term>   # 逐条撤词表（可选）
```

---

## 八、执行结果（2026-11-04）

### 8.1 全库回归

`scan_title_gates.py`（生产 5000 / 容器 15000，各 2296 / 2307 条 `rewritten_title`）：

| 门禁 | 生产 | 容器 | 结论 |
|---|---|---|---|
| 空壳落点 FAIL | **0** | **0** | 零误伤，开 FAIL |
| 日文商品词 FAIL | **0** | **0** | 零误伤，开 FAIL |
| 圈内黑话无锚点 FAIL | 1（期生曲） | 0 | 历史稿，可接受 |
| 泛反应词 WARN | 3 | 2 | 仅提示 |
| 代词开头 FAIL | 69 | 60 | skill 既有硬规则，历史遗留；重写时触发 |
| 超 20 字 FAIL | ~1200 | ~1200 | **迁移前就存在**的长度门禁，非本次新增 |

### 8.2 单元测试

```
tests/services/test_title_gates.py  9 passed
全量 pytest                        239 passed, 1 skipped
```

覆盖：4 个失败案例（应 FAIL）、3 个历史好标题（应 PASS）、泛反应 WARN、
正文日文缩写、gzh 渠道差异、术语表替换。

### 8.3 指定稿件实跑（dry-run）

```
key 8d7303ea0ee0d99c1de2cf443971a00a2aa6416e
python -m cli write-full --dry-run --key ...
```

结果：

- 标题 `指原莉乃把当年伤自己的规则删了` → **新门禁全部通过**（人名锚点、无空壳、无黑话、无日文词）
- 正文 465→532→548 字，三轮均 <800 → 按既有规则**放行并标豁免**（该 key 是 11/02 已知的
  「删收束句跌破 800」边界案例，属生成侧问题，非门禁误伤）
- dry-run 未写库

### 8.4 附带修复

`services/lexicon.py` 缺少 `if __name__ == "__main__"` 守卫，导致
`python -m services.lexicon seed/add` 静默无输出。已补。

---

## 九、标题生成 A/B（2026-11-04）

10 条近两周 S/A 素材，同一 prompt，只改调用参数：

| 组 | 参数 | 平均耗时 | 特点 |
|---|---|---|---|
| old | thinking 关 / temp 0.7 | ~1s | 短、稳，但常丢差异化信息 |
| new | thinking 开 / temp 0.9 | 1–22s（多数 3–8s） | 明显更具体，偶尔超 20 字 |

样本对比（节选）：

```
DB    ：FNS连播两周36组名单公开
old   ：相叶雅纪连主持两晚，36组挤进8个半小时   ← new 更好（两个事实）
old   ：180cm前女优妹妹劝她做网红              ← 丢了主角名
new   ：大崎菜绪180cm，妹妹说高个有市场          ← 有锚点

old   ：道枝骏佑三人副音轨爆料拍摄秘话          ← 抽象
new   ：道枝骏佑这部片3天2亿，三人副音轨聊了什么   ← 具体（略超字数）
```

**结论：采用 new**，同时补一条 prompt 硬约束：
「标题严格 ≤20 字；超长先砍修饰语，必须保留人名/团名」。
原「prompt 回吐检测」与占位符黑名单保留。

---

## 十、写作侧与三候选（2026-11-04 追加）

### 10.1 断掉 DB 标题锚定

- `agent/write.py` 写作包改为 `db_title=…（仅线索：可能是弱标题/张冠李戴，禁止直接复用或只做同义改写）`
- `agent/prompts/write.md`：标题至少含一个原文具体事实（规则名/数字/原话/作品名/团体名）
- 新增 `_reused_db_title()`：与 DB 标题相似度 ≥0.85 → 前两轮强制重写

### 10.2 三候选 + 确定性选择

- LLM 输出 `titles: [画面型, 对位型, 引语型]`
- `_pick_title()`：门禁全过 → 具体性打分（数字/书名号/引号 +2，数字+单位 +1，复用 DB 标题 -2）→ 取最高；全不合格回退第一条交给 precheck 反馈
- gzh 保持单标题

### 10.3 抽象落点 WARN

`services/precheck.py` / `batch_precheck.py` 新增：

```
末分句以 删了/改了/换了/变了/没了/做了/开始了/结束了 结尾 → WARN
```

全库实测：生产 3 条、容器 5 条（含目标稿 `8d7303ea`），占比 0.2%，不阻断。

### 10.4 复测

```
key 8d7303ea…  dry-run
  候选=3 → 选中「指原莉乃东京巨蛋领奖：成员把我带回来的」
  （旧行为：直接复用 DB 标题「指原莉乃把当年伤自己的规则删了」）
  正文 861 字，评分 5.5/8 → 既有「3 轮不达标放行」逻辑，非标题问题
```
