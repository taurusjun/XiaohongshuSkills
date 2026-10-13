# 每日素材 Review：新旧 Skill 对齐（4 层）

> 背景：容器化时把旧 review skill（4 层 SKILL + 大量 references/scripts）解耦为「**机械服务 + 精简 prompt**」。
> 本文给出**新旧逐条对齐表**与 4 处已补回的硬动作，便于交付/迁移时对照验收。
> 配套 [docker-containerization-plan.md](docker-containerization-plan.md) · [container-env.md](container-env.md) · 第 4 层数据源 [auto-publish-pipeline.md](auto-publish-pipeline.md)。

## 1. 组成与入口

| 项 | 旧（生产） | 新（容器 / 本仓库） |
|---|---|---|
| 提示词 | `skills/creative/xhs-daily-material-review/SKILL.md`（76KB）+ layer1/layer23/layer4 四个 SKILL | `agent/prompts/review.md`（精简，**只输出 二~六 节**） |
| 编排 | Hermes agent 按 SKILL 逐步执行 | `agent/review.py`（确定性骨架 + 一次 LLM 调用 + 落库/校验/交付） |
| 骨架 | agent 手写 TL;DR/全量表 | `services/review_archive.py::build_archive` 程序生成「一、全量素材一览」 |
| 关联检索 | API `search=` + 只读副本 | `services/related.py::find_related`（只读 DB 三字段 OR） |
| 第4层聚合 | `scripts/layer4_aggregate.py` | 同名脚本，`services/feedback_patterns.py::aggregate` 调用 |
| 反馈累积 | 手改 `data-feedback-patterns.md` | `services/feedback_patterns.py::update`（程序化、仅追加） |
| 表格校验 | 手跑 `validate-tables.py` | `services/validate_tables.py`，`review.run()` 收尾自动跑 |
| 触发 | Hermes cron 01:30 | `cli review-full --date …`；容器 cron **02:30**（= 生产 +1h） |

> `agent/review.py` 顶部的 `SKILL_FILES` 仅作参考保留，**未注入** prompt —— 解耦即弃用 76KB 原文，规则沉淀到精简 prompt + 服务。

## 2. 逐条对齐表（旧 4 层 → 新实现）

| 旧 skill 步骤 | 新实现 | 状态 |
|---|---|---|
| 1a 拉全量（按 `fetch_by`） | `news.query_news` 当日全量 | ✅ |
| **1b 读关键素材正文** | `compact_rows(with_body=True)`：**每条** `content_ja` 全文（`body`，上限 6000）喂 LLM | ✅ 已补 |
| 1c 同事件聚类（跨来源 / 日期陷阱） | 三（LLM 聚类）+ 二（日期陷阱） | ✅ |
| 1d 输出格式（**按来源分组**） | `build_archive(single_table=False)` | ✅ 已补 |
| 1e 分级（定量阈值） | 四（`S ≤ 总数×15%` + ts/cj 门槛） | ✅ |
| 第2层 价值建议 | 五 | ✅ |
| **第3层 跨时间关联（中日双形）** | 五；实体抽取输出「日文汉字 + 简体中文」两形，`related.variants()` 再补 `zhconv` 变体；**只读 DB 三字段 OR** | ✅ 已补 |
| 第4层 发布回顾（>48h / 24-48h / <24h） | 六；数据来自 `layer4_aggregate.py`（只读、走 `127.0.0.1:5000`、不走代理） | ✅ |
| **4d 更新 `data-feedback-patterns.md`** | `feedback_patterns.update()`：模式节插 `## 跨会话趋势表` **之前**，趋势行追加**表末** | ✅ 已补 |
| **表格铁律 `validate-tables`** | `review.run()` 收尾自动跑并打印 `tables/problems` | ✅ 已补 |
| CRON 执行模式 | 容器 cron（见第 5 节） | ✅ |

## 3. 四个已补回的对齐点（关键坑）

1. **正文入料**：旧 1b 读正文，新实现曾只给标题+长度。现 `compact_rows(with_body=True)` 把**每条** `content_ja` 全文放入素材 JSON（`body` 字段，上限 6000 覆盖最长正文），prompt 明确「逐条阅读 body 再聚类/分级」。
2. **`validate-tables` 硬动作**：`review.run()` 组装 md 后调用 `validate_tables.validate_text(md)`，打印 `tables/problems`。为满足「单表 ≤25 行 + 表格前紧邻 `#` 标题」，**section 一改为按来源分组**（`single_table=False`，旧 1d 原意），并在 prompt 固化表格铁律。
3. **反馈累积**：`feedback_patterns.update()` 严格**仅追加**——模式节插在趋势表之前（避免旧教训：直接 `cat >>` 会在文件尾多出空表头），趋势行追加到趋势表**最后一个 `|` 行之后**。**同日幂等**：同日趋势行已存在则**替换**、同日模式节**跳过**（防手动/cron 重跑重复）。
4. **中文 ↔ 日语双查**：今日素材多为**简体中文**、DB `content_ja` 为**日文**；「简繁双查」的实质是「中文 + 日语双形」。① 实体抽取 prompt 要求每个实体**同时给日文汉字与简体中文两种写法**；② `related.variants()` 用 `zhconv` 再补 `zh-cn/zh-hant/zh-hk/zh-tw` 字形；③ **API `search=` 不索引 `content_ja` 日文正文（旧 9/29 实测）**，故用**只读 DB 三字段 OR**（`title / content_ja / rewritten_title`）。

## 4. 结构化共享数据（review → write）

`review.persist()` 只写**当日行**，并**清理当日未标注的旧值**：

| 字段 | 取值 | 消费方 |
|---|---|---|
| `grade` | `S / A / B / C / AKB大TOP` | write 选材（S/A/AKB） |
| `cluster_keys` | 同事件成组、互指 | write 合并 / 去重（每组取一条） |
| `channel_hint` | `gzh`（适合公众号的产业·厂牌·行业分析类） | write **优先/强制 gzh** |
| `akb_type` | `event / bullet`（仅 AKB大TOP） | write：event 写全文 / bullet **只入库不写正文** |

`related_keys` 由 **write** 阶段写（review 不写）。

机器接口（**2026-10-12 重构**）：**LLM 只输出一个 JSON 对象**（`response_format=json_object`，**不写 Markdown/表格**）；**报告（二~六节 md）由程序模板渲染**（报告=数据的渲染，格式由代码保证、单一真相）：
```json
{"date_trap":[{"key":"<k12>","kind":"前日重复|前日已发|正常","note":"…"}],
 "clusters":[{"keys":["<k12>",…],"main":"<k12>","note":"…","grade":"S"}],
 "grades":{"<k12>":{"grade":"S|A|B|C|AKB大TOP","reason":"…"}},
 "advice":{"<k12>":{"title_dir":"…","how":"…","richness":"…","priority":"P0","emotion":"…"}},
 "cross_time":{"<k12>":[{"key":"<k12>","hit":"中日双查命中"}]},
 "layer4":{"conclusion":"…","top":"…","low":"…","zero":"…","anomaly":"…"},
 "channel_hint":{"<k12>":"gzh"}, "akb_type":{"<k12>":"event|bullet"},
 "feedback":{"patterns":[…],"trend_row":"…"}}
```
- `review.persist()` 取 `grades`(→`grade`)/`clusters`(→`keys`)/`channel_hint`/`akb_type`。
- **渠道判定留痕**：每次判定把 `key → 渠道 → 依据` 打印并追加 `logs/channel.log`（TSV：ts/date/key/channel/reason）。
- **渠道判定（2026-10-12；2026-10-13 对齐原 skill 9/12 口径 + 补 skip）**：`channel_hint` **从主调用拆出，独立一次 LLM**（`review.judge_channels`）——输入=当日 S/A/AKB 候选 + 每篇实体的**近期发布序列 + 连续0曝光计数**（Python 预取，`_entity_pub`）。**判据=两条互相独立的适配门**（对齐原 skill「对每条做两套判断」，按**内容性质**、不看偶像性别）：
  - **xhs 门**（给粉丝看·有趣/有画面/有情绪）：日常/趣闻/穿搭/反差萌/亲子/怀旧；**争议/八卦/恋爱/爆笑短讯**/同框/私物/综艺花絮（争议型在小红书天然有流量；男团粉丝争议/私物/同框/综艺也走 xhs）；坂道系/女艺人/AKB大TOP；偶像成长/逆袭/**人物心路**；简单事件。
  - **gzh 门**（给路人看·有深度/需思考）：电影/音乐/摄影/美学等深度分析；娱乐产业/经纪/厂牌/销量/榜单/战略行业分析；**人物深度**长访（完整叙事弧/公共议题）；跨文化分析；多人物对照/行业史；该人物/题材**连续0曝光≥3**→改道；成人产业/风俗/AV 的**人物弧光/公共议题**。
  - **合成**：两门都过 → `both`（尤以**人物心路＋人物深度兼有**）；只过一门 → 该渠道；都不过 → `xhs`（默认）；命中 **skip 判据** → `skip`。
  - **skip（❌，不写稿；对齐原 skill）**：纯榜单/列表无上下文；品牌合作软文（>50% 产品参数）；セクシー女優写真宣传；与日娱无关（体育/经济/政治）；夜店/风俗/AV 的**行业操作细节/猎奇本身**（非人物弧光）。write 侧 `pick_candidates`/`write_one` 对 `channel_hint=skip` **直接不写**。
  输出 `{k12: gzh|both|skip}`（xhs 不列=默认）。主 review prompt 已移除 channel_hint。
- 渲染：`services/review_render.render(data, titles)` → 二~六节 md；`_extract_machine_json` 保留作**兜底**（旧「md+末尾 json 块」格式）。
- 收益：`grades/feedback` **格式有保证**（不再依赖 LLM 手写 md 的自觉）、省 token、可单测；代价：报告文风与生产（Hermes 手写 md）不同，但流水线只吃结构化数据。

## 5. 运行与验收

```bash
# 运行（容器内）
.venv/bin/python -m cli review-full --date 2026-10-09          # 只落盘 + 打印
.venv/bin/python -m cli review-full --date 2026-10-09 --deliver  # 额外交付
```

- 容器 cron：`30 2 * * *`（= 生产 01:30 + 1h；见 `ops/crontab`）。
- 验收关注输出三行：`[persist] grade … / cluster …`、`[validate-tables] tables=… problems=0`、`[feedback-patterns] 已更新…`。
- 单测：`pytest tests/test_review_machine.py tests/services/test_feedback_patterns.py tests/services/test_related.py`。

## 6. 涉及文件

- `agent/review.py` · `agent/prompts/review.md` · `services/review_render.py`（报告模板渲染）
- `services/related.py` · `services/review_archive.py` · `services/validate_tables.py` · `services/feedback_patterns.py`
- `skills/xhs-daily-material-review-layer4/scripts/layer4_aggregate.py`（第4层数据源，只读）
- `skills/creative/xhs-daily-material-review/references/data-feedback-patterns.md`（跨会话累积，运行时更新）
