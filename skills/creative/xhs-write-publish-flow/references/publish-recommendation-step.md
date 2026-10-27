# 阶段6 · 待发布推荐（写稿流程的最后一步）

> 写稿 + review 全部完成后**自动进入**本阶段，不向用户请示、不中途汇报。
> 本阶段只**排期**，绝不**发布**（禁止 `POST /api/trigger-publish`）。

## 0. 产出口径（一次给全）

1. **5 篇推荐**：每篇给「标题 + 一句话理由（含人物历史数据）+ 完整 40 位 key + 预发布时间」。
2. **2 条备选补位**。
3. **数据总览**：断更天数、本月每日发布量、候选池规模、当前待发队列条数。
4. **落库确认**：5 篇 `xhs_pub_time` + `publish_xhs=1` 已验证。

## 1.5 ⚠️ 直接查 DB 时 `publish_xhs` 是整数 1/0，不是 `'published'`（10/09 实测）

`'published'` 只是 **API 的枚举别名**（`/api/news?publish_xhs=published` 服务端映射成 `publish_xhs=1`）。
**用 sqlite3 直接查历史数据/人物面板时必须写 `publish_xhs=1 AND COALESCE(publish_time,'')!=''`**；
写成 `publish_xhs='published'` 会**静默返回 0 行**——10/09 阶段 6 的人物历史面板因此全部 n=0（川荣李奈/道枝/乃木坂… 全查不到），白跑一轮。
另外，判断「已发布」不能只看 `publish_xhs=1`（**定时队列也是 1**），必须叠加 `publish_time` 非空。

## 1.6 候选池/节奏盘点（本节 SQL 与上面 §1 同源）

**候选池**（当日新写优先）：
```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
 "SELECT substr(key,1,12), substr(rewritten_title,1,30), format, is_long_form, score_dims
    FROM news
   WHERE preselected=1 AND publish_xhs=0
     AND COALESCE(rewritten_content,'')!=''
     AND date(created_at)>=date('now','-2 day')
   ORDER BY created_at DESC"
```

**4 篇「历史数据推荐」排序口径**（见 `xhs-publish-workflow` 的「发布节奏盘点」节）：

| 优先级 | 判据 |
|--------|------|
| 1 | 时效性素材（当天/近两日事件，过夜打对折）——必须说明「今天就得发」 |
| 2 | 人物历史数据最强（`search=<人名>&publish_xhs=published` → 已发条数/中位 `xhs_views`/最高/最近3条） |
| 3 | 分数最高但本人近期走低的 → 标「质量牌」而非「流量牌」，别让用户误判预期 |

约束：**同人物/同题材不挤在同一天**；同一事件的稿件不挨着发；避开明天已有排期的题材（见 §3）。

**1 篇「随机探索」**：从 4 篇之外的剩余候选中**真随机抽 1 篇**（`random.sample`，不看分数/data）。目的：探索未被历史数据覆盖的题材/人物，防止推荐被既有流量池锁死。输出时**明确标注「随机探索」**。

**⚠️ 随机池也必须限时效（10/27 实测近失）**：随机 ≠「从全库 1761 条待发里抽」。10/27 第一版池子按印象拼了 11 个 key，抽中的 `245997d1f319` 其实是 **6 月 10 日**的老稿（四个月前的婚礼题材）——排进明天等于发过期新闻，靠「`SELECT date(created_at)` 复核」才拦下。
**正确做法：随机池 = `preselected=1 AND publish_xhs=0 AND rewritten_content!='' AND date(created_at)>=date('now','-3 day')` 里**过闸**的那批（排除已选的 4 篇），再 `random.sample`。抽完对中签 key 跑一次 `SELECT date(created_at)` + `pub_history_gate --strict --keys <key>`，两项都过才进推荐。


> 数据面板**先出**再给结论——这个用户对「先摆数据后给结论」敏感。

## 2. 预发布时间：明天 09/12/15/18/20（带随机偏移，禁整点）

用独立脚本 `scripts/pub_time_plan.py` 生成（东京时区「明天」）：

```bash
cd ~/.hermes/skills/creative/xhs-write-publish-flow/scripts
# 1) 看计划（不写 DB）
python3 pub_time_plan.py --seed 42
# 2) 直接写库：xhs_pub_time + publish_xhs=1（按顺序对应 5 个时段）
python3 pub_time_plan.py --keys <K1>,<K2>,<K3>,<K4>,<K5> --apply
```

- 默认时段 `09:00,12:00,15:00,18:00,20:00`，偏移默认 `±8min`，**区间内随机且排除 0**，
  结果分钟必不为 `:00`（示例：`08:55 / 11:52 / 14:59 / 17:59 / 19:56`）。
- 需要复现同一批时间时带 `--seed`；换时段用 `--slots`；换日期用 `--date`。
- `--apply` 只做 `UPDATE news SET xhs_pub_time=?, publish_xhs=1 WHERE key=?`，
  写后自动读回校验；`--no-publish-flag` 则只写 `xhs_pub_time`。
  **不碰** `publish_mode` / `publish_method` / `related_keys` / `rewritten_*`。
- ⚠️ **`--apply` 不会真的踢到图集下载（10/26 实测）**：脚本尾巴的图集环节 5 条全 skip，
  报 `URL can't contain control characters. "/api/gallery-download/('0f9fcd9e…"`（把 tuple 的 repr 拼进了 URL）；
  且 `--apply` 走 sqlite UPDATE，本来就不经过 `PUT /api/news` 那条自动起线程的路径。
  **排期后手工逐条补**：`curl -s --noproxy '*' -X POST "http://127.0.0.1:5000/api/gallery-download/<40位key>" -H 'Content-Type: application/json' -d '{"gallery_url":""}'`，
  再等 `gallery-status=done` 且多张 → 回填 `publish_images`（否则线上只有封面一张图）。

**10/27 实测两点补充：**
1. **`pub_time_plan.py --keys` 只认完整 40 位 key**（`WHERE key=?` 精确匹配）。传 12 位前缀 → 5 条全部 `FAIL <key> 不存在`，而且**时间表已经先打印出来了**，容易误判成「已排好」。先解析：`SELECT key FROM news WHERE substr(key,1,12)=?`，拼成逗号串再传。
2. **手工触发图集后不一定都有图**：10/27 五条里 2 条拿到（16 张 / 5 张），3 条返回 `error: 未检测到图集链接`（Yahoo 原文本身没有图集）。**只有 `gallery-status` 返回非空 `images` 才回填 `publish_images`**；空图集不要写 `[]`，留给发布侧用封面图。
   回填格式（与历史一致，JSON 数组存绝对路径）：`UPDATE news SET publish_images=? WHERE key=?`，值 `json.dumps(sorted(glob.glob(os.path.expanduser(f'~/.cache/xhs_images/{key}/*.jpg'))), ensure_ascii=False)`。


## 3. 冲突检查（排期前必做）

### 3.1 发布历史闸门（机械必过，10/22 事故后立规）

**事故：** 10/22 03:00 的每日写稿 cron 推荐了 5 篇，其中
- 道枝骏佑（10/19 刚发，3 天前）
- 山内瑞葵（**当天 11:56 已发**——排期时它已在今日队列里，没查）
- 目黑莲（近 14 天 2 条）、大西流星（近 14 天 2 条）
- 大谷映美里跳索兰节（10/11 发过，索兰节题材 10/13 又发过）

用户直接打回：「选择待发布的时候，没有去看发布历史吗」。根因不是数据缺失，是
**§1.6 的「同人物/同题材不挤在同一天」只有一句话约束、没有机械检查**，且 §3 只查
「明天时段是否被占用」，完全不查人物/题材的最近发布史。

**所以本步骤改为强制跑脚本，人工判断不能替代：**

```bash
cd ~/.hermes/skills/creative/xhs-write-publish-flow/scripts
# 1) 扫候选池，看哪些能过闸
python3 pub_history_gate.py --strict
# 2) 定下 5 篇后复核（支持 12 位前缀）
python3 pub_history_gate.py --strict --keys K1,K2,K3,K4,K5
# 退出码 0 才允许 --apply 排期；非 0 必须换稿
```

**判定（`--strict` 下任一命中 = FAIL）**

| 规则 | 阈值 | 含义 |
|---|---|---|
| 硬重复 | 同 tag 最近发布距今 ≤ 3 天 | 前几天刚发过，直接换 |
| 题材疲劳 | 14 天内同 tag 已发 ≥ 2 条 | 同一人物被写密了，换 |
| 排期撞车 | 同 tag 已被**另一篇**排在今天/明天 | 防 24h 内二次自我分流 |
| 泛化标签 | 组名/品类（AKB48、乃木坂、日本娱乐…）不参与判定 | 见脚本 `GENERIC` 集合 |

**输出要求：** 5 篇推荐里每篇必须附闸门结果的原话，例如
`✅ 最近同tag: 指原莉乃 10-18(4天前), 近14天1条` —— 让用户一眼看到「查过了」。

**已知局限：** 闸门靠 DB 的 `tags` 字段。tags 缺主角名时（10/22 那道枝稿 tags 里
没有「道枝骏佑」，只有共演者）会靠共演 tag 间接命中；若整条 tags 都填得很泛，
需人工补一眼标题。选题本身信息量不足时，换稿成本低于硬写。

### 3.2 时段占用检查

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
 "SELECT substr(key,1,12), substr(title,1,28), xhs_pub_time
    FROM news WHERE xhs_pub_time LIKE '<明天日期>%' ORDER BY xhs_pub_time"
```

若明天某时段已被占用，把冲突篇排除出候选（或说明「该时段已排」），不要两条挤同一时段。

### 3.3 当日新稿大多过不了闸时，回退到哪（10/26 立规）

实测：当日 11 篇新稿里只有 **2 篇**过 `--strict`，其余 9 条被 tag 驳回（真重复 3 条，
误伤 6 条——樱坂新旧团名同 tag、`EV电动车` 脏 tag、`日本明星` 泛 tag、`YOASOBI` 共演 tag）。

**补位顺序（硬规则）：当日新稿 → `created_at` 最近 1–3 日的过闸候选 →（到此为止）**

- **绝不从全库过闸池里捞老候选。** 10/26 全库 914 条过闸，最早到 **6 月**，其中不少人物数据很漂亮
  （小嶋阳菜 n=8/最高 37808、能条爱未 n=5/中位 1122），但都是四个月前、从未发布的稿 → 排进明天等于发过期新闻。
- 一句话判据：**闸门管「会不会自我分流」，时效管「值不值得发」，两个都要过。**
  历史数据只用来排**同时效档内**的顺序，不能用来给过期稿加分。
- 误伤驳回按机械规则执行（换稿），但汇报里要写清「是 tag 撞了旧团名 / 脏 tag，不是真的重复选题」，
  别让用户误以为选题烂。

详见 `references/10-26-single-day-batch-flow.md` §2。

## 4. 铁律 / 坑

- ❌ **不触发发布**：本阶段只写 `xhs_pub_time` + `publish_xhs=1`；`POST /api/trigger-publish` 一律禁止。
- ❌ 不设 `publish_time`（系统发布后自动填）。
- ⚠️ 写 `xhs_pub_time` 两条路都可，但要清楚差异：`pub_time_plan.py --apply` 走 `sqlite3 UPDATE`（只碰 `xhs_pub_time`+`publish_xhs` 两个字段）；`bash update.sh` 走 API `PUT`。**10/24 实测**：API `PUT` 只更新传入字段 —— 换稿后逐条复核，`related_keys`(3条)/`publish_mode`/`publish_method`/`rewritten_content`(len 2335、915) 全部未被清。**但涉及 `wechat_*` 字段时仍必须走 `sqlite3 UPDATE`**（历史上往 wechat 写值时 xhs 侧字段被清，10/9 记）。同一个 key 上两侧字段别用两种方式交替写。
- ❌ `update.sh` 的参数必须是 `field=value`。传裸参数（如 `bash update.sh <key> --`）会把 `"--"` 当字段名写进请求体，**API 还返回 `{"ok": true}`** —— 静默污染、不会报错（10/24 实测，复核后该字段未被持久化，但不能依赖）。调用前先 `GET /api/news/<key>`（或列表接口）拿**完整 40 位 key**，不要用 12 位前缀去猜。
- ✅ `publish_xhs=1` 是「已排期/待发布」标记，**不会自动发布**（发布只由 `trigger-publish` 或人工触发）。
- ✅ 保留写稿阶段设好的 `publish_method`（长文 `export` / 图文 `post`）与 `related_keys`。
- ⚠️ 与「写稿入库只设 `preselected=1`」的分工：那条规则管**入库**，本阶段是**流程最后一步**，
  只对**被推荐的这 5 篇**设 `publish_xhs=1`，其余稿子维持 `preselected=1 / publish_xhs=0`。

## 5. 换稿 / 撤稿（用户给 key 说「这篇换一下」「换一篇」「这条不要」）

**先判语义**：`换一下` = 把这篇**从待发队列撤下、换备选顶上**，**不是**「改写这篇」。先 `GET /api/news/<key>` 看它的 `xhs_pub_time` / `publish_xhs`：在队列里就是撤换；正文本身有问题才是改稿。判断依据是当前队列状态，不是猜。

**不要问用户「换哪条」** —— 按备选池顺序顶第一条，汇报时补一句「要换成另一条我直接调」。

1. 定位：`GET /api/news/<key>`，记下 `xhs_pub_time`（**时段必须保留**）
2. 撤出：`bash update.sh <完整40位key> publish_xhs=0 xhs_pub_time=""`
   - **不清正文、不清 `related_keys`、不归档**（`status` 保持 active）——撤下的稿子照样能重排
3. 顶上：取本批「备选补位」第一条（先为它复跑 `python3 pub_history_gate.py --strict --keys <备选key>`，exit 0 才用），
   `bash update.sh <备选key> publish_xhs=1 xhs_pub_time="<原时段>"`
4. 验证：`GET /api/news?publish_xhs=pending&limit=500` → **条数 = 排期条数**，时段无空洞（见 §6）〔原文：查 `xhs_pub_time` 非空且 `publish_time` 空的一组〕
5. 汇报：表格给「时间 / 新篇标题 + **完整 40 位 key** / 来源（新顶入·备选）」，并说明撤下那条现在的状态（草稿态、正文多少字、related 几条仍在）

**排期构成会变，要主动说**：原时段若是 `export` 长文（2000+ 字），换成 900 字 `post` 后「早间第一档给足阅读窗口」这个理由就不成立了 —— 一句话点出来，让用户决定要不要把长文排回去，别默默降级。

## 6. 队列卫生：bullet / merged 占位条绝不能带 `publish_xhs=1`

**10/24 实测事故**：当日写稿把 bullet 条（`score_dims='akb-top-bullet'`）和 merged 占位条（`score_dims='merged-into-<key12>'`）残留成 `publish_xhs=1` + `xhs_pub_time` 为空。这个组合在发布侧是危险的：

- `scripts/sqlite_db.py:588` 的待发取稿条件 = `publish_xhs=1 AND publish_time 空 AND status='active' AND (publish_method IS NULL OR 'post')` → 占位条完全符合
- 网页 UI 明写 `不设=立即发布` → 会被当成「立即发布」，把 200 字的原始短讯当稿件推出去

**排期后立刻查这**：

```bash
curl -s --noproxy '*' "http://localhost:5000/api/news?publish_xhs=pending&limit=500" \
 | python3 -c "import json,sys; rows=json.load(sys.stdin)['rows']; print('pending=',len(rows)); [print(repr(r.get('xhs_pub_time')),r['title'][:26],r['key'][:12]) for r in rows]"
```

条数 > 排期条数，或出现 `''` 时段 = 有占位条残留 → 逐条 `publish_xhs=0` 清掉。

**⚠️ 判据要加时间维度（10/26 实测假警报）：** 直接查 `score_dims LIKE 'merged-into-%' OR score_dims='akb-top-bullet' AND publish_xhs=1` 会捞出 4 条，
但它们的 `xhs_pub_time` 全在过去、`xhs_views>0` —— 那是历史上**真的被当作占位发出去**的老条，不是本批残留。
只有 **`AND xhs_pub_time > datetime('now','localtime')`**（或 `xhs_pub_time` 为空）才是「残留占位」。
对着历史条清 `publish_xhs` 反而会把已发布的记录搞乱。

## 7. 「为什么 X 这次一条也没有？」——按处置分类出账，别只答闸门

用户问某个团体/人物为什么没进推荐（10/24 问 AKB），正确答法是把当日该团体的**每一条素材的去向**列全，而不是笼统说「被闸门筛了」：

| 处置 | 判据字段 | 说明 |
|---|---|---|
| bullet（只入库不写稿） | `score_dims='akb-top-bullet'` | 170–800 字原始短讯，**无 `rewritten_content`** |
| merged 占位 | `score_dims='merged-into-<key12>'` | 重复簇归档，同样无 `rewritten_content` |
| C 级跳过 | `preselected=0` | 未达入库标准 |
| 成稿 | `publish_mode='rewritten'` + `rewritten_content` 非空 | 才可能进候选池 |
| 已排期 / 备选 | `publish_xhs=1` 或本批备选清单 | **别忘了算旁支**（AKB 系含 HKT48/NGT48 出身） |

**根因通常是候选池硬条件**：`rewritten_content 非空` 把 bullet/merged 结构性排除 —— 素材量不小，但没走成稿通道 = 供给断在**写稿环节**，不是闸门否决。

**给结论前必须先跑一遍闸门复核**（对当日该团体的各条 `pub_history_gate.py --strict --keys ...`），用「多数其实过闸、问题是没稿」的数据把责任归位 —— 否则用户会误以为闸门过严，进而要求放宽阈值（那是推翻正确规则）。
