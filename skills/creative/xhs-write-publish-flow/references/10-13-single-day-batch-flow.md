# 10/13 单日批实作（50 条 → 12 xhs + 1 gzh + 3 bullet + 14 merged）

单日批：写稿 → batch_precheck + renwei → 入库 + 密度 + 5维度 review → 阶段6 待发布推荐。
正常日（cron L1 先行，total=50；API list dump 50 == review 50）。上午批次，当日 5 篇定时队列（09:08–19:57）尚未到点，故「已发」仍以 10/12 20:07 为准。

## 范围与产物

- 当日 total=50。范围＝S 4 + A 6 + AKB大TOP 10。
- 产物：**12 xhs**（story 3 + news-story 1 + news 8）+ **1 gzh**（斋藤飞鸟深访 86d6c471，cj=4220>3000）+ **3 bullet**（西野未姬袴服 1190e061 / 吉川七濑 a56ebb93 / 柏木由纪牧野真莉爱 2bb3145f，晒照型）+ **14 merged**。
- 5维度：story 最低 8.3（芳根京子 offshot），最高 8.8（高山一实触摸争议）；gzh 8.4。news 全部 news-pass（密度 35–75%）。
- xhs 明细：271e3b60(高山一实·story 824) / 5e538392(いれいす·story 954) / 82ad2ff4(芳根京子·story 818) / 348377cd(菅本裕子·story lf=0 364) / 43907e59(早安少女组·news 374) / ee5f0df6(目黑莲·news 273) / de37d5b2(樱井翔阿部·news 423) / 6442e925(西野未姬·news 374) / 3f842ab5(齐藤京子·news 322) / 5fbefee6(道枝CDTV·news 299) / 91a6e85a(乃木坂6期生·news 222) / 1ef8e0c8(梅泽美波·news 176)。

## 路由与合并决策

- **斋藤飞鸟（S1）→ gzh**：review 第2层明写「公众号长文首选」，cj=4220 深访 → wechat_ 字段，preselected=0。
- **S2 道枝釜山（a9ffda26）跳过** → `merged-into-bbc8fa27a343`（10/12 已写同事件稿；10/11 9ca4a071 已发布 v=1115）。
- **S3 上白石萌音（c10e4a11）跳过** → `merged-into-3c4ac76b1224`（10/12 已写同专访 export 1045，review 明写「择一发」）。
- **A3 冰川町（891d4d31）跳过** → `merged-into-35b1abd4a1ea`（10/12 已排 10/13 14:53 同访问）。
- **C2 乃木坂6期生 5 条 → 主条 91a6e85a**（news 619 最全），其余 4 条 `merged-into-91a6e85a0167`。
- **C3 齐藤京子 3 条 → 主条 3f842ab5**，48ce53d9/d584e26d merged。
- **C4 CDTV 2 条 → 主条 5fbefee6**，eeaec051 merged（与 C1 道枝重叠）。
- **C5 プーさん 3 条 → 主条 de37d5b2**（832 最全），1e717508/ebe21709 merged。
- **C9 いれいす 2 条 → 主条 5e538392**（2661，review 指定代表条），629f67b8 merged（3632 但为同事件改写重复）。
- **T2 河西智美（500e472a）→ T3 菅本裕子（348377cd）**：review 第3层指出 TOP2 标题误标（80kg/心不全主角实为菅本裕子），合并、主角改菅本裕子。

## 本批坑（新增）

1. **⚠️ heredoc 里 dict 与 list 相加 / 文件名前缀笔误 = 整段静默失败。** 评分落盘脚本写 `list(SC)+NEWS`（SC 是 dict）直接 TypeError，前面的 UPDATE 因在异常前已 commit 才没丢；另一次 `rep('xhs_draft_5fbefee6cdd8.md', ...)` 把 key12 写错（正确 `5fbefee6dcd8`）→ FileNotFoundError 中断整段，此后所有 fix 全部未执行（T5/T1/T6 白跑）。**规则：批量补丁脚本每个 rep 前先 `assert os.path.exists(fn)`；异常必须逐条捕获（print MISS 继续），不能裸 assert 中断；落盘后必须复查输出条数 == 预期。**
2. **⚠️ 用 patch/replace 加内容时，若插入点紧跟一个已存在的 `##` 小标题，会把标题复制成两份。** S4 扩充时 old_string 用了「…取乐。」，new_string 里又补了 `## 这档节目不是第一次踩线`，而原文下一行本就是同一小标题 → 出现两个同名 `##`。**规则：插入型 patch 的 old_string 只取插入点后的**第一行原文**，或改用「在锚点后追加」的 append 语义；落盘后 grep `^## ` 查重名。**
3. **中文成稿比直觉短 ~30%**：S4 首稿按 900 字直觉写，实测 body=669；A6 写了两轮才从 393→818。**story lf=1 落笔后立即用 `xhs_word_count.py` 量 body，<800 当场补，别等 precheck。**
4. **precheck 的 ja 必须用 `xhs_word_count.py` 的 `xhs_content_len` 计**（不是 raw len）——raw 会偏大 1–3%，密度被低估。plan.json 由脚本生成，不手填。
5. **人名/团体名中文化一次到位**：いれいす→IReis、りうら→Riura（无既定中文，走罗马字）、绀野ぶるま→绀野布露玛、森カンナ→森康娜、咲妃みゆ→咲妃美优、峰岸みなみ→峰岸南、ゆうこす→菅本裕子（DB 既有写法）。**曲名夹日文新字体也要改**：《愛 MUST BE》→《爱 MUST BE》（愛→爱，不在脚本 SHINTAI_FAIL 类里，靠人工）。**山本圭壱的「壱」按人名保留规则放行（precheck WARN 非 FAIL）。**

## 阶段6 待发布推荐（10/14 排期）

- 数据：断更 0（最新实发 10/12 20:07）；10/13 定时队列 5 条（09:08–19:57，本批执行时未到点）；候选池 1635（全库积压）；待发队列 8；10/14 无占用。
- 5 篇（4 历史数据 + 1 随机）：271e3b60（高山一实·08:58，历史 953/674/910 稳定高位，争议型同题材已验证）/ ee5f0df6（目黑莲向井康二·12:02，目黑莲 10/02 4485 近期最热）/ 6442e925（西野未姬·14:52，历史 755，AKB育儿共情）/ 43907e59（早安少女组·17:59，510/472，跨团迷因）/ **5e538392（いれいす·19:55，随机探索，published=0 无历史数据）**。
- 备选：3f842ab5（齐藤京子，歌手出道人物弧光）、de37d5b287e7（樱井翔/阿部，同维尼企划 10/06 491）。
- 避开：10/13 已排乃木坂（井上和）→ 乃木坂6期生/梅泽美波降备选；道枝骏佑 10/12 15:03 刚发 → CDTV 稿降备选。
- 排期只用 `pub_time_plan.py --keys ... --apply`（写 xhs_pub_time + publish_xhs=1，保留 publish_method/related_keys），**未触发发布**（active-tasks 空）。
