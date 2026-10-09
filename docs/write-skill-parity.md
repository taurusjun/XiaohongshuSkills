# 写稿 Skill：新旧对齐（6 阶段）

> 背景：容器化把旧写稿 skill（`xhs-write-publish-flow`，SKILL.md + ~130 references + 12 scripts）解耦为
> 「**机械服务 + 精简 prompt**」。本文给出 6 阶段逐条对照与已补回的功能，便于交付验收。
> 配套 [review-skill-parity.md](review-skill-parity.md) · [container-env.md](container-env.md) · [auto-publish-pipeline.md](auto-publish-pipeline.md)。

## 1. 组成与入口

| 项 | 旧（生产） | 新（容器 / 本仓库） |
|---|---|---|
| 提示词 | `SKILL.md`（+references/scripts） | `agent/prompts/write.md`（精简） |
| 编排 | Hermes agent 按 SKILL 走 6 阶段 | `agent/write.py`（一次 LLM/篇 + 机械门禁 + 改稿循环） |
| 机器接口 | 手动 curl/脚本 | LLM 输出严格 JSON（`channel/title/body/gzh_*/related`） |
| 触发 | 用户「开始」/ Hermes cron 03:00 | `cli write-full`；容器 cron **04:00**（= 生产 +1h） |

## 2. 逐阶段对照

| 旧 6 阶段 / 功能 | 新实现 | 状态 |
|---|---|---|
| 1 写前准备：S/A/B 分级 | DB `grade`（S/A/AKB大TOP），同 `cluster` 只取一条 | ✅ |
| 1 通读 content_ja（dump_ja） | `prepare_package`：一般 9000 字、**export 长文 20000 字** | ✅ 已补 |
| 1 体裁/字数路由（format+is_long_form） | `services/format_route.route`（news900 / story900 / export） | ✅ |
| 1 查关联素材（同事件+同人物） | `cluster_keys` + `related.find_related`（中日双形） | ✅ |
| 2 渠道路由 xhs/gzh | LLM 决定；`services/routing.route` **机械预判**接入 prompt | ✅ 已接 |
| 2 **同日双版本（both）** | channel 支持 `both`：一次 LLM 出 xhs 版 + gzh 版，分别门禁/评分/入库 | ✅ 已补 |
| 3 体裁自检（story≥800/`##`≥2；news 无 `##`） | `services/precheck.check_text` | ✅ |
| 3 标题≤20 / 假名≤5 / 顿号清零 / 日文新字体 | `_trim_title`+`kana`+`dunhao`+`precheck` | ✅ |
| 3 密度≥30% | `precheck`：**分母=主素材 content_ja**（旧 8/15~8/17 修正口径） | ✅ |
| 3 知识库/案例 | `references.relevant`（标题+日文标题检索 top-4 + 常读清单） | ✅ 已增强 |
| 4 入库 + 三步验证 | `update_news` + 读回校验；渠道字段 mode/method/channel/preselected/publish_xhs | ✅ |
| 5 密度 + xhs 5维评分 + 改稿循环（≤3轮） | `precheck` + `score_content` + `_produce` 循环 | ✅ |
| 5 **renwei 审读（六类信号 + exit 0/1/2）** | `services/renwei`（**完整移植** `renwei-pre-commit.py`）；exit1 才拦 | ✅ 已补 |
| 5 公众号 review（去魅+背景+3维） | `gzh_review`(机械) + `score_gzh`(3维，含去魅测试) | ✅ |
| 5 达标才交付 | `deliver` 只推 `ok` 稿（0 篇达标不推） | ✅ |
| 6 待发布 5=4 数据+1 随机+2 备选 | `recommend.stage6` | ✅ |
| 6 排期 09/12/15/18/20、随机禁整点 | `schedule.DEFAULT_SLOTS`+`build_plan(jitter)` | ✅ |
| 6 不触发发布 / 发布前图集 | 无 trigger；`gallery.sync` | ✅ |
| **关联素材体量过大 → 拆多篇**（默认关） | `services/split_write` + `cli write-full --split-large` | ✅ 已补 |

## 3. 已补回的功能（关键）

1. **renwei「人味审读」完整移植**：`services/renwei.py` 现覆盖六类信号（意义拔高/宣传腔/句式套路/格式痕迹/语气痕迹/填充与对冲）+ 聚集判定（同类≥2 或 跨类≥3 → exit 1；破折号不计入聚集）。`write_one` 仅在 **exit==1** 时拦（exit 2 接受）。
2. **同日双版本（both）**：一次 LLM 输出 xhs 版（`title/body`）+ gzh 版（`gzh_title/gzh_body`），各自过门禁/评分后入库；两者皆过则 `channel='both'`。
3. **机械渠道预判接入**：`prepare_package` 用 `routing.route(title, content_ja)` 得 `pre_channel` 注入 prompt；LLM 输出非法渠道时回退 `pre_channel`。
4. **export 长文正文入料放宽**到 20000 字（原 9000 会截断超长深访）。
5. **拆多篇 fallback**：`split_write` 按内容体量把 cluster 粗分为 ≤3000 字的组，每组一个主 key；**默认关闭**（旧 skill 要求用户明确同意才拆），`--split-large` 开启。
6. **references 命中度**：检索词 5、结果 4 篇、摘录 900 字，并始终附带 `ai-taste-checklist` / `deep-interview-density`。

## 4. 口径说明

- **密度分母**：合并稿/续篇/同框关联一律用**主素材 content_ja** 做分母（旧 8/15~8/17 实测定论），新实现即此口径，避免合并去重分母造成的假 LOW。
- **正文上限**：小红书图文软目标 ≤900（发布时可手动调节），写稿阶段不设机械上限。
- **Hermes 工具态问题**（批量三阶段调度、中断恢复、无声失败/`ALL_PROXY`、key 位数不固定）在代码化后已 moot。

## 5. 运行与验收

```bash
.venv/bin/python -m cli write-full --n 3 --dry-run          # 只写不落库
.venv/bin/python -m cli write-full --deliver                # 全批次 + 排期 + 交付
.venv/bin/python -m cli write-full --split-large            # 允许拆多篇
```

- 容器 cron：`0 4 * * *`（= 生产 03:00 + 1h；见 `ops/crontab`）。
- 验收：逐篇打印 `PASS/FAIL <key> [channel] att=… score=… body=… ##=… kana=…`。
- 单测：`pytest tests/services/test_renwei_six.py tests/test_write_both.py tests/services/test_split_write.py tests/services/test_precheck.py tests/services/test_routing.py tests/services/test_schedule.py tests/test_pick_cluster.py`。

## 6. 真实验证证据（2026-10-09）

复现：`.venv/bin/python ops/verify_write_alignment.py --date 2026-10-09`
（用**真实 DB 数据**跑**真实代码路径**；仅 compose/prepare_package 的 LLM 调用打桩，机械逻辑不打桩）。

| 缺口 | 真实验证证据 |
|---|---|
| 1 both 双版本 | `write-full --key 27aeaa0e --force-channel both` → DB `channel='both'`、`rewritten_content=986`、`wechat_content=910`、`preselected=1`、`publish_xhs=0` |
| 1 标记 | 标记表：xhs-only → `channel=''`（`0f696111/1c1195de/b1d4987e`）；both → `channel='both'`（`27aeaa0e/25ed19f8/58184e85`），均 `preselected=1` |
| 2 拆多篇 | 当日含 cluster 15 行；最大 cluster **13176 字**（阈值 3000）；`should_split=True`；`split_groups=[[354b05cc],[1c307512],[231ef75a],[27aeaa0e,57b3618e],[a4545a7e]]`；8 个 cluster>3000 |
| 3 routing 接入 | 真实 gzh 命中：`6a49b02c`「M!LK出道多年首破百万」/`2c4c0f6c`/`86ff8e18`；`prepare_package.pre_channel` 已计算；compose user 消息含「机械预判渠道」=True |
| 4 renwei 六类信号 | 19 篇真实正文：`exit=0`（7）/`exit=2`（12，命中「四、格式痕迹[破折号]/一、意义拔高[格言公式]」）；exit=1 聚集拦截由 `test_renwei_six` 覆盖 |
| 5 密度 | 19 篇真实值 32.5%~83.6% 全部 ≥30%，`mech_density` 与手算一致（分母=主素材 content_ja） |
| 6 references | 真实标题检索命中 `ai-taste-checklist.md`+`deep-interview-density.md`（如「道枝骏佑釜山…」） |
| 7 export cap | 真实行 `b6712f24`：`src=18623 → fed=18623`（cap=20000；旧 cap=9000 会截断） |

### 验证中发现并修复的标记 bug（已被上面的真实证据覆盖）
1. **xhs 写入未写 `channel`** → 残留旧值 `gzh`。改为**写后按字段实际存在**判定：`both`(有 xhs+gzh) / `gzh`(仅 gzh) / `''`(仅 xhs)。
2. **both 时 `preselected` 被 gzh 覆盖为 0** → 改为写后按 `has_x` 判定（both→`1`，仅 gzh→`0`）。

## 7. 涉及文件

- `agent/write.py` · `agent/prompts/write.md`
- `services/renwei.py` · `precheck.py` · `dunhao.py` · `kana.py` · `format_route.py` · `routing.py` · `gzh_review.py` · `references.py` · `split_write.py` · `schedule.py` · `recommend.py` · `gallery.py`
- `skills/creative/xhs-write-publish-flow/`（SKILL + references + `reviews/chinese-review-prompt.md`）
- `ops/verify_write_alignment.py`（真实验证脚本，可复现）

## 9. review→write 结构化数据审计

旧 write 会从 **review 存档（`~/.hermes/daily-reviews/DATE.md`，自然语言）**取数据；容器化后 review 只产结构化输出。逐项审计：

| 场景（write 从 review 取的…） | 旧形态 | 结构化字段 | 状态 |
|---|---|---|---|
| 分级 S/A/B/C/AKB大TOP | 存档分级列表 | `grade` | ✅ |
| 同事件聚类 / A级同S簇跳过 | 聚类标注 | `cluster_keys`（write 每组取一条） | ✅ |
| **gzh 方向**（阶段2） | 「公众号方向/改道」备注 | `channel_hint` | ✅ 补 |
| **AKB大TOP 事件型 vs 晒照型**（写不写正文） | 「事件型→写全文 / 晒照型→summary bullet」备注 | `akb_type`（event/bullet） | ✅ 补 |
| 跳过（非赛道 / 成人产业「行当玩法」） | 「跳过」备注 | `grade=C`（不入候选池）；改道=`channel_hint` | ✅ |
| 第2层价值建议（标题方向/情绪预期） | 存档自由文本 | —（写稿用不到；纯人读建议） | 不改 |

### `akb_type` 真实验证（2026-10-09）
```
review 真实输出：[persist] … / AKB分流 4 篇
DB：1c1195de/dc62bbe1/6779ef94/20d27ab6 均 grade=AKB大TOP, akb_type=event
模拟标 bullet 后 write --key 20d27ab6 → "BULLET …（仅入库不写正文）"
DB：akb_type=bullet, preselected=1, publish_xhs=0, rewritten_content 空
```

## 8. gzh 写稿触发机制（A 方案：结构化）

### 旧 skill 是怎么触发的（原始语义）
旧 `xhs-write-publish-flow` **阶段2**：「渠道路由 xhs/gzh/both。**从 review 分级结果中提取 gzh 方向素材**，review 标了 gzh 方向的**必须先处理**」。触发链是**自然语言**、非结构化：
- **review 侧**在日报存档（`~/.hermes/daily-reviews/YYYY-MM-DD.md`）里用文字备注「适合公众号 / 改道公众号深度 / xxx（公众号方向）」（见 `xhs-daily-material-review`、`xhs-daily-material-review-layer23`）；
- **write 侧**启动时「先扫一遍 review 的 S/A 级和**备注**」，人工/agent 提取 gzh 向素材（《gzh 路由判据》按**内容性质**：给路人看的人物·产业分析→gzh；给粉丝看的爆料·日常→xhs）；
- 判据还含：review 明写「强搁置→改道公众号」、男团产业·厂牌·销量·战略类、成人产业人物弧光等。

→ 即：**旧触发依赖 review 文本里的中文备注 + agent 人判，没有字段、没有确定性触发**；这也是我们容器化后 gzh 实际不触发的原因（我们的 review 只产结构化 grade/cluster）。

### A 方案：把「review gzh 方向标注」结构化
1. **DB 加字段 `channel_hint`**（`scripts/sqlite_db.py`，兼容迁移自动 ADD COLUMN）。
2. **review** 在机器 JSON 里输出 `channel_hint: {"<key12>":"gzh"}`（对产业/厂牌/行业分析类），`persist()` 写库并**清理当日未标注的旧 hint**。
3. **write**：
   - `pick_candidates` 把 `channel_hint='gzh'` 的素材**纳入候选池**（不受 S/A 门槛限制）；
   - `prepare_package` 里 `channel_hint='gzh'` → `pre_channel='gzh'`；
   - `write_one` 对 `channel_hint='gzh'` 的候选**强制走 gzh**（`compose(force_channel='gzh')`），覆盖 LLM 判定。

### 真实验证证据（2026-10-09）
```
review 真实输出: [persist] grade 53 / cluster 14 / gzh方向 4 篇
DB channel_hint=gzh: b1d4987e(S) 25ed19f8(S) 6a49b02c(B) 58184e85(S)
write（86ff8e18, grade=B, LLM 本判 xhs）置 channel_hint=gzh 后:
  pick_candidates 含该素材=True
  write-full --key 86ff8e18 → PASS [gzh]；DB channel='gzh' wechat_content=346 rewritten_content=0
```
