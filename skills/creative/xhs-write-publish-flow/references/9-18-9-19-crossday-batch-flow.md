# 9/18+9/19 跨日批实作（26 + 18 条落地）— 命令级复盘

**启动判定：** `date_from=2026-09-18&date_to=2026-09-19&limit=500` → 125 行。遍历 `preselected/rewritten_title/channel` 三字段后发现 **9/18 的 59 行全未写（0 条遗漏可解释）**，9/19 的 66 行也全未写。⇒ 本轮为跨日双批（9/18 缺一批，未写）。

**批次规模：**
- 9/19 批：12 篇 xhs（其中 3 组为合并稿）+ 2 篇 gzh + 4 条 AKB bullet + 1 条跳过并补关联
- 9/18 批：22 篇 xhs（其中 3 组为合并稿）+ 4 条 AKB bullet
- renwei：9/19 exit0×12 / exit2×2（非聚集）；9/18 exit0×14 / exit2×8（非聚集）
- 密度：全部 ≥30%（story 另过 ≥800 字门禁）；dcc88b6e82ca 万字深访 4715 字 / 38.3%
- 五维评分：story 8.0–9.3，news 全部 news-pass，gzh 三维度 7.7/7.8

---

## 1. 启动范围（本轮最大的一次范围误判风险）

9/17 批（`references/9-17-single-day-batch-flow.md`）只写了 9/17 当日。9/18 一整天没有写稿会话，因此 **9/18 的 59 条全部留在未写状态**。启动时若只看 `preselected/rewritten_title` 会把 9/18 当「上批主动跳过项」放过——必须回看上一批 reference 的跳过节：9/17 的跳过节只列了 9/17 的条目（cd6a0ae1f7e9 等），没有 9/18 的任何 key ⇒ 9/18 属真漏写。

## 2. 体裁（每条都查 DB，不用 review 分级推断）

9/19：story/lf=1 共 12 条（含 cf092b3d4c62＝gzh 改写）；news/lf=0 共 10 条。
9/18：story/lf=1 共 15 条；news/lf=0 共 13 条。
**反例记录：** 9/19 的 d124e9570ca0（松井珠理奈）DB 为 `story/lf=1`，但 review 标注「晒照型→summary bullet」——按 9/17 已有先例（1d2877f9d87a、5aff743f1cf2 同为 story/lf=1 却按 bullet 处理），AKB大TOP 的「晒照型/summary bullet」指令优先，不入正文。

## 3. 合并稿（同事件多素材 → 1 篇）

| 主 key | 关联 key | 处理 |
|---|---|---|
| 21c5a22d74ab（织田裕二初日，S1） | 894cf0a54c1d（S2）+ e07fae44cf6a（8/27 旧稿） | 合并写 1 篇，1596 字 / 60.5% |
| 121a6d06a51f（ラブトラ35+ MC，S3） | 080895daf15c（S4） | 合并写 1 篇，811 字 / 40.3% |
| d6dec683f4ea（あざとくて・藤原，A3） | e71501c14a8a（A4） | 合并写 1 篇短 news |
| 6673b132b149（マッチング 场面写真，A3） | 14c4d5524523（A4）+ 898eeaf71f55（9/19 塚田访谈） | 合并写 1 篇 |
| bbf43f4df236（逃げろお嬢さん・井口理，A5） | ad99cfbab9fc（A6） | 合并写 1 篇 |
| c5dfe633d849（Neo小節 厂牌） | 29195ca7c34b（呼魂ひかり） | **不合并**：视角可辨（厂牌结构 / AI 歌声的完美 vs 人的音准抖动），互设 related_keys |

密度分母按主素材 content_ja 计（合并稿口径），未合并去重分母。

## 4. 走 gzh 的 2 条（review 建议改道）

- `cf092b3d4c62`（安田章大 SNS 论，cj=4223，当日最长）：写 1267 字 gzh 长文，`wechat_title/wechat_content` + `channel='gzh'` + `preselected=0`、`rewritten_content=''`；三维度 7.8。
- `898eeaf71f55`（塚田僚一 A.B.C-Z 访谈，story/lf=1）：男偶像/STARTO ⇒ gzh 通道，967 字；三维度 7.7。

## 5. 跳过并补关联（不写第二篇）

- `7e67f6857463`（川崎樱 CanCam 专模）：与 9/16 已写的 `1bea0a416816` 近重复 ⇒ 不写，挂到该稿 `related_keys`（已验证 40 位）。
- 9/19 的 C 级重复项（b874e4a9301c / 0327b6b685eb / 89907295d6dd / b22d39034a3b / 94bebc92f247 / 75ea84132243 / 6e79b81c4bf4 / ea56b5b73abe / e7154c607cc2 / 916670255873 / 82c8d70f9801）按聚类合并/去重，信息已由主稿覆盖。
- 9/18 聚类4 的 4 条重复（6411cc710f61 / ef87159c32cd / 6c305701f8fc / bbe16f932819）：AKB大TOP「必须入库」但作为发布去重对象 ⇒ `score_dims='akb-top-bullet'` 纯预选，不写正文。

## 6. 入库与验证

一律 sqlite3 UPDATE 写 `rewritten_title/rewritten_content/publish_mode/preselected=1/publish_xhs=0/related_keys`（gzh 写 `wechat_` + `channel='gzh'` + `preselected=0`），随后机械复查：
- `preselected=1` / `publish_xhs=0` / `rewritten_content` 非空 / `score_dims` 非空（gzh 反向检查 `rewritten_content==''`）
- `related_keys` 每个 key `len==40` 且 `SELECT 1 FROM news WHERE key=?` 存在，且不含自身 key
- 结果：**36 条写入（9/19 18 条 + 9/18 26 条）PROBLEMS: none**

## 7. 假名/新字体终检

全线 kana≤5（实际全批 0）。新字体扫描命中 3 处，全部按人名保留规则放行：`丰嶋花`（嶋，人名保留）、`石田壱成`（壱，人名保留）、`最強→最强`（dcc88b6e82ca 标题，已改简体）。
**本轮新增：** 题目行也会漏检「強」这类新字体——扫描类需含 強（最強/勉強）与 実/気/対/経/済，仅靠既有正则（発恵価壱歳嶋竜徳沢辺栄広芸戦売買読選抜）会漏。

## 8. 本次踩过的坑

1. **c5dfe633d849 / 29195ca7c34b 首轮 kana=10** — 来源是 `日本コロムビア`（片假名）。⇒ 老牌公司名要预先中文化（日本哥伦比亚），不能只清人名/曲名。
2. **renwei exit1 的三类写法（9/18 批）** — ①排比三连顿号（`作词作曲、编曲、视觉和歌声`）②自问自答（引号内问句紧跟解释）③破折号≥2。改法照旧：顿号改中文逗号或间隔号·、把问答拆成叙述句、破折号改句号。
3. **合并 patch 造成小标题重复** — 6673b132b149 追加小节时复制出第二个 `## 上一部留下来的坑`，靠 grep `^## ` 揪出并改名。
4. **万字深访（12576 字）Phase A 就按 ×0.31 写足** — 4715 字一次到位（38.3%），未留密度扩充尾巴。

## 9. score_dims 落盘

story：`爆点X.X/情X.X/增X.X/深X.X/标X.X|X.X分|理由`；news：`news-pass`；bullet：`akb-top-bullet`；gzh：`gzh去魅通过/背景锚定通过|X.X分|三维度：…`。全部 sqlite3 UPDATE，22/22（9/18）+14/14（9/19）复核非空。

## 10. ⚠️ 流程告警（写稿侧观察到，未处理）

XHS 发布管道自 **2026-09-16 17:28 之后 0 发布**（9/17、9/18、9/19 三天），定时队列为空，`pending=0`。本批 36 条全部落为 `publish_xhs=0`，等发布侧确认后再入队。
