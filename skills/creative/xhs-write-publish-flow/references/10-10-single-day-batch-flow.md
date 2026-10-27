# 10/10 单日批实作（18 条 → 6 xhs + 1 bullet + 3 merged）

单日批：写稿 → batch_precheck + renwei → 入库 + 密度 + 5维度 review → 阶段6 待发布推荐。
低量正常日（fetch 00:30 首发，18/18 成功，非抓取故障）。

## 范围与产物

- 当日 total=18：目标赛道 10（日向坂3 + 櫻坂1 + AKB1 + アイドル5）、男团 Snow Man 3、非目标 グラビア4 + セクシー女優1。
- 范围＝S 3 + A 2 + AKB大TOP 2。
- 产物：**6 xhs**（story 3 + news 3）+ **1 bullet**（上西怜，晒照型）+ **3 merged**（挂 10/9 同事件行）。
- 跳过 11：B级 2（Gran☆Ciel / pinponpanpon，非坂道/AKB 发片情报）+ C级 9（Snow Man 3 非目标男团商务、グラビア4 纯颜值、セクシー女優、水濑さらら写真集）。
- 字数路由：松尾樱 `41783594ec8e`（cj=4214）→ **export 长文**（正文 1520）；其余 story/news → `post`（880/873/370/299/297，均 ≤900）。

## 本批三个坑（均已回写 SKILL.md）

1. **前一日同事件条目是 B/C（从未写稿）→ 不能照抄 review 的「跳过」。** review 第3层把 野村实代/松本和子/上西怜 判「纯重复→跳过」，但 10/9 那三行是 B/C、`rewritten_content` 为空 ⇒ 事件从未成篇。正确：写本日更高分级版本 + 把 10/9 那几行标 `merged-into-<本日主条key12>`。判据 `SELECT length(rewritten_content) ...`。
2. **入库后 API GET 验证必须用完整 40 位 key。** `curl /api/news/<12前缀>` 返回空 title/content（看着像没落盘），换全键立刻正常。是「GET 用前缀查不到」，非「PUT 未落盘」。
3. **draft 目录必须当天新建。** `/tmp/drafts/` 残留了 10/9 的 25 篇，precheck 扫进去、报告行数对不上。

## 其它实测记录（本批）

- **renwei「格言公式(X是Y的Z)」对普通句式误报**：`潮纱理菜是两人的直系前辈` / `潮纱理菜和两人同属日向坂46，也算直系前辈` 均被标记；改「是→同属/也算」仍命中，最终 exit=2（单处非聚集）接受。这类「A 是 B 的 C」白话句改名后仍可能命中，属已知误报，exit=2 直接放行，别为它反复改。
- **story 小 cj（~1000）也要 ≥800 正文 + ≥850 余量线**：野村实代(1038) 与 上村雏乃(1019) 首轮都 <800（687/570），靠挖素材细节补到 865/853 才过 precheck 的 850 余量线；补写后必须重跑假名 + renwei（本批两次补写都新增过克/落假名的风险点）。
- **`pub_time_plan.py` 预览打印的是 DB 的 `title` 字段**（原始日文标题），不是 `rewritten_title`——看到预览标题「日文/怪」不要慌，脚本只是列表展示，`--apply` 写的是 `xhs_pub_time + publish_xhs=1`，不动标题。
- **「待发队列」计数可能读 0 而实际有活跃定时队列**：10/10 04:04 查 `publish_xhs=1 AND (publish_time IS NULL OR ='')` = 0，但当天的 5 条定时条（08:56/11:55/…）其实挂着 `xhs_pub_time` 未来值且 `publish_time` 非空。**要看「还有没有排期」用 `xhs_pub_time` 未来值判断，别只信 publish_time 为空的口径。** 断更天数同理先 `xhs_pub_time <= now` 过滤。
- **阶段6 顺序（已按 spec 执行）**：先出数据 → 4 历史数据推荐 + 1 随机探索（`random.sample` 不看分数）→ 2 备选 → `pub_time_plan.py --keys K1..K5 --apply` → 逐条读回校验。全程未调 `POST /api/trigger-publish`。
- **人物历史面板**（`search=<人名>` 后筛 `publish_xhs==1 & publish_time 非空`，峰值为 xhs_views 最大）：上村雏乃 峰值 1665（9/27，热度在线）；大谷映美里 峰值 2630（7/05，但中位仅 150）；松尾樱 1331（6/09）；野村实代/井上瑠夏/清司丽菜 0 已发（新面孔）。
