# 10/6 单日批实作（10 story + 7 news + 1 gzh + 3 bullet + 7 merged）

单日单批（当日 60 条全部未写，无跨日积压）。启动判断、渠道路由、落地数据、新增坑都在这里。

## 1. 启动判断

- 当日 review 存档 `~/.hermes/daily-reviews/2026-10-06.md`（60 条，S4/A8/AKB大TOP9/B15/C24）。
- 跨日检查：10/3 written=9、10/4 written=15、10/5 written=18（前批已落地，其余 20/12 条是 skipped/bullet/被吸收备选，presel=0 属预期）→ **只需写当日**。最近一份批次 reference 是 `10-05-single-day-batch-flow.md` → 一致，无漏写日。
- 单日单批（≈18 篇正文）直接走三阶段（A 写稿 → B precheck+renwei → C 入库+review），不必拆子批。

## 2. 渠道路由

- 本日 review 第2层对 **A5 酒井麻衣**（f6cf7fa8，西畑大吾×福本莉子《时薪300日元的死神》監督演出論，ja2681）显式标注「新角度，转公众号深度」→ 走 gzh（`wechat_title`/`wechat_content`，`channel='gzh'`，preselected=0）。
- 其余男偶像报道型（timelesz 猪俣周杜、Snow Man 深泽辰哉、なにわ 中岛健人）默认走 xhs 正文；猪俣周杜是 10/2 已发稿（09784e351769）的续篇，用 related_keys 指向前篇形成时间线。
- 判据见 `material-routing-decision-guide.md`：报道型→xhs，分析型/方法论→gzh。

## 3. 落地数据（DB 实测）

| 类别 | 篇数 | 备注 |
|---|---|---|
| xhs story (lf=1) | 10 | 856–901 字，`##`≥2，5维度 8.1–8.8，全部 ≥8 无改稿轮 |
| xhs news (lf=0) | 7 | 171–534 字，密度 44–83%（news 门禁是密度≥30%，不是字数） |
| gzh | 1 | 酒井麻衣稿 1696 字，去魅+背景锚定通过，三维度 8.3 |
| AKB大TOP bullet | 3 | 17baf1ab 野吕佳代 / da15974d 斋藤飞鸟 / f8bfd740 金村美玖，只 `presel=1`+`score_dims='akb-top-bullet'` |
| merged-into | 7 | 295786961+66fd5e8a+5cc15f1b→b8b36c87；5dcc72cf→17baf1ab；88920150→fc2e1a55；188c3e49→a92ae85c；87d5faec→f8bfd740 |
| 跳过 | 32 | B级15 + C级17（含非目标赛道グラビア5+セクシー女優2、道枝巴黎3、其他低质），全部 presel=0 |

## 4. 本批新增坑 / 观察

- **⚠️ 入库复核的 API GET 必须用完整40位 key，12位前缀会静默返回空对象。** `GET /api/news/<key>` 只认完整 40 位 SHA1；传 12 位前缀时返回空对象（`preselected=None`、`rewritten_content` 空），**看着像「整批静默入库失败」，其实是查询 key 位数写错**，与 DB 落盘无关。10/6 首轮复核误用 12 位前缀，10 篇全返回 None，换成 40 位后全部正常。**判定顺序：先核对位数，再怀疑入库失败**；复核键一律取 list dump 的 `r['key']` 或 DB `SELECT key`。
- **发布节奏盘点：`MAX(xhs_pub_time)` 会被未来定时条污染。** 未来定时条同样带 `publish_xhs=1` + `publish_time` 非空 + **未来的** `xhs_pub_time`，裸 `max()` 取到未来时间 → 负数「距今天数」或误报「刚发过」。算「最新真实发布 / 本月每日实发量」必须加 `AND xhs_pub_time <= now`（10/6 实测：全库 max=10/6 22:23 是未来定时条，真实上次发布 10/5 22:09）。已同步写入 `xhs-publish-workflow` 的「发布节奏盘点」节。
- **news 首轮标题全部一次过（≤20 字硬门）**：写标题前先用 `xhs_word_count.py --check-title "<t>" 20` 跑一遍，10/6 七条 news 标题 14–18 字全过，省掉 10/5 那轮重写。
- **顿号行仍是 news/story 第一大 FAIL**：首轮 precheck 8 篇命中，全部是「并列人名/曲名/画面细节」的顿号枚举。修法照旧：≥3 项用中文间隔号 `·`（U+00B7），2 分隔 + `的` 收尾的句子把 `、` 拆成 `，` 或改写收尾。gzh 长段落也被同一条件命中（L15/L21），不能因为「是 gzh 长文」就跳过顿号预检。
- **story 扩写一次到位**：首轮 story 全部 640–790 字，靠「每个 FAIL 段补 1–2 句素材内细节」一次性推到 856–901，未出现第二轮。关键：扩写句子只从已读过的 content_ja 取，不要凭记忆从日文原文拼。
- **`batch_precheck.py` 计的是 `lines[1:]` 的正文**：draft 首行必须 `## 标题`（story/news 同规则），本批 18 篇全带首行，无 SKIP、无「检查完毕 N ≠ draft 数」。
- **入库统一走 sqlite3 UPDATE**（含 xhs/gzh/bullet/merged），一次 commit，逐类验证。`related_keys` 全部 40 位、无自引用；`score_dims` 也走 sqlite3。本批无 API PUT，未触碰静默失败陷阱。
- **发布管道本批已恢复活跃**：10/5 22:09 有实际发布（见上「未来定时条」坑）。推荐 5 篇已设 `publish_xhs=1`（保留 publish_method/related_keys），**未触发发布**。

## 5. 推荐待发布 5 篇（10/6）

猪俣周杜 c09eed403768（时效+事件线稳）、川口春奈 27e79a03bd3b（周榜时效）、川荣李奈 b8b36c87f928（人物历史最强 med2752/max5455）、井上和 c7714774a092（大河下集主役回）、深川麻衣 9e3e88f6410b（逆转型人物长文，8.8）。备选：天城里绪奈 bbe939bd88bc、田村保乃 36761f19c636。
