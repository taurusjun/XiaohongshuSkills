# 10/7 单日批实作（14 story + 4 news + 19 merged，跳过22）— 含两条新增坑

单日单批。当批最值得复用的不是题材，而是两个机械坑：**review 存档报「0 条/抓取故障」但 DB 实有当日素材**、以及 **Phase B 为过 renwei 改稿把正文压到 800 以下**。

## 1. 启动判断（本批核心）

- 当日 review 存档 `~/.hermes/daily-reviews/2026-10-07.md` **存在，但是一份抓取故障状态报告**：正文写明 `date_from=2026-10-07 total=0`、全库 `max(created_at)=2026-10-06 00:41`、**无 S/A 清单**，并判定「上游抓取全线失败」。
- **DB 实测与实际相反**：`SELECT count(*) FROM news WHERE date(created_at)='2026-10-07'` = **59 条**，`created_at` 集中在 `01:42:22–01:47:18`（抓取链路当天凌晨已恢复落库），59 条全部带 `content_ja`、全部未写。
- **处置**：不照单信存档的「0 条」，直接在主进程补做 **第1层（全量扫描+聚类分级）+ 第3层（跨时间关联）**，据此写稿。
- **规则（写进 SKILL.md）**：无论存档内容是否写着「0 条/故障」，启动一律先 `sqlite3 ... "SELECT count(*) FROM news WHERE date(created_at)=当日"` 核对；>0 即有素材，按有素材走。存档的「0 条」只是生成那一刻的观测。

## 2. 聚类与落地（59 条）

| 簇/主条 | 吸收的重复条 | 备注 |
|---|---|---|
| a6044d4af777 川口春奈电影登顶 | 46e6783cd35a, 87b1c8e8f24d, 505b49baeaca, 9e1c3e23601f | 同一票房榜 5 报 |
| dbbc39266ae3 乃木坂4人anime | aa73b8411ea4, 5dadd953a9ea, 3b5899f28429 | 同一预告 4 报 |
| 6bd8cf3d51c9 Snow Man AMENITY | cef59ba4e5bb, 26cf44574013, 58abbef114d6 | 同一专辑 4 报 |
| cfba58b7941b 川荣李奈 | 14510e99dbf8, 81d7b6b4b671 | 同一综艺 3 报 |
| 4fdeaee39c65 樱坂四期生元凶 | 6ce799940405, 4f702dfdc548, c96ba5617dff | 4 报 |
| ebac75f2e55b 真田广之 SHOGUN | c09471941290 | 2 报 |
| 9b3969fd5ae3 深泽辰哉粉丝/有吉 | 945c32ffc279, df9e3d2829f1 | 同人物、不同事件，挂 related |
| 35b4a74ce1bc 日向坂/小田/绿苹果 | 92839e9adcca | 同榜 |

- merged-into 共 19 条（`preselected=1` + `publish_xhs=0` + `score_dims='merged-into-<主条12>'`）。
- 单篇（无关联）：板野友美 015e8700a73d、道枝 9d4d08678bf9、峯岸 abc412c33915、能条 bae8a19ae4ff、小栗有以 a921b2a8f957（唯一 `publish_method='export'`，ja 4121）、以及 4 条 news。
- f733b80ebcea（timelesz 篠塚）related_keys 指向历史 10/6 `c09eed403768…`、10/2 `09784e351769…`，形成时间线。

**跳过 22 条**：三比菜々美 3fee15b58d14（セクシー女優猎奇，非人物弧光）；软胶娃娃 44967161d491、IDOL FILE 200人展 02224e1247e2（名单型）；グラビア/写真 9 条（大西桃香/与田祐希/金村美玖/池田蕾拉/音羽美奈/泽口爱华/星名美津纪/小岛南/苍井空）；无新闻价值 B/C 短讯 9 条。

## 3. 新增坑

### 坑1：Phase B 为过 renwei 的改稿会把「刚好过 800」的稿子改到 800 以下
- f733b80ebcea 初稿 **815 字**过 precheck；renwei exit=1（两处「意义拔高」聚集：结尾段的「格言公式(X是Y的Z)」+「标志性动词」），按指引删改掉两个结尾段后掉到 **799（<800 硬门）**。
- **修法/闭环：Phase B 的改稿闭环 = renwei → 字数重计 → 重跑 `batch_precheck.py`**（假名/新字体已含在 precheck 内），不能只重跑 renwei。补一句素材内陈述句到 834 后，precheck 通过、renwei exit=2。
- 参考：`xhs_content_len` 计所有非换行字符；renwei 报告里的 `(xhs: N)` 是另一套口径，本批实测比脚本**低约 42 字**（832↔815 附近），以脚本为准。

### 坑2：`normalize-dunhao.py` 一次修净顿号行
- 首轮 precheck FAIL 11 篇，原因全是「顿号行 ≥2」；`python3 scripts/normalize-dunhao.py --quiet <dir>` 一次修 **20 行**（行内第 2 个及以后的 `、`→中文间隔号 U+00B7），复跑 FAIL 0。间隔号不计假名、不触发顿号正则，双门禁一次过。
- 同批另两处 FAIL：标题 22 字（`--check-title` 硬门 → 就地改短）、正文残留 1 个假名（`落叶の方`→`落叶夫人`）。

## 4. review（5维度）与本批上限
- 14 篇 story 全部 ≥8.0（8.0–8.8），无改稿循环；4 条 news 记 `news-pass`。
- 两篇贴上限：a921b2a8f957（食品店铺介绍类，爆点1.5/情1.4/增1.7/深1.8/标1.6=8.0，上限附近通过）、9d4d08678bf9 / ebac75f2e55b（8.0，开头略平）。
- `score_dims` 全部 sqlite3 UPDATE 落盘，复核 0 条为空。

## 5. 发布推荐（10/7）
最新真实发布 10/6 22:23；10/7 已有 4 条定时排期（井上和/深川麻衣/猪俣周杜/川荣李奈）→ 推荐时**避开这 4 个已排期题材**。5 篇：板野友美 015e8700a73d、川口春奈 a6044d4af777、Snow Man 6bd8cf3d51c9、能条爱未 bae8a19ae4ff、峯岸南 abc412c33915（备选：深泽 9b3969fd5ae3 质量牌、道枝 9d4d08678bf9）。
