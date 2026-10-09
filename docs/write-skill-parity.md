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

## 6. 涉及文件

- `agent/write.py` · `agent/prompts/write.md`
- `services/renwei.py` · `precheck.py` · `dunhao.py` · `kana.py` · `format_route.py` · `routing.py` · `gzh_review.py` · `references.py` · `split_write.py` · `schedule.py` · `recommend.py` · `gallery.py`
- `skills/creative/xhs-write-publish-flow/`（SKILL + references + `reviews/chinese-review-prompt.md`）
