# 9/17 单日批实作（36篇 xhs + 1 gzh 改道 + 3条 AKB bullet）— 命令级复盘

**批次规模：** 58条当日素材 → 36篇 draft（16 story + 19 news + 1 gzh）+ 3条 AKB preselect-only + 1条同事件跳过。renwei exit0×23 / exit2×12（全非聚集）/ exit1×1（修净）；密度 34.7–83.8%（另1篇 29.6% 补写至 37.0%）；story 五维 8.2–9.1，另1篇 7.9 判公告类天然上限。

---

## 1. 启动范围判定（本次最大的一次「差点白干」）

`date_from=2026-09-16&date_to=2026-09-17&limit=500` → 115 行。遍历结果：9/16 有 33 行「未写」、9/17 有 58 行全未写。

按 SKILL.md「跨日检查三态表」逐条核对 9/16 的 33 行：**0 条遗漏**——
- 2 条是 AKB bullet（`preselected=1` + `rw=0` + `score_dims='akb-top-bullet'`）
- 1 条是 gzh（`channel='gzh'` + `wechat_content=1781` + `preselected=0`）
- 9 条在上一批 `references/9-16-single-day-batch-flow.md` §8「跳过的条目」里有明确决策（同素材已发/同角度第3次/字数不足/无人物钩子/非目标赛道软文）
- 其余为 C 级合并项

⇒ **结论：只写当日批。** 教训：只看 `preselected/rewritten_title` 两个字段会把上批的 bullet + 主动跳过项当漏写，启动前必须同时读上一批 reference 的跳过节。

## 2. 素材落盘 + 体裁一览（一个脚本顶掉 38 次 curl）

```bash
mkdir -p /tmp/mat && curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=<当日>&limit=200" -o /tmp/list.json
python3 - << 'PYEOF'   # targets = {key12: 'S1 xxx'}
import json
d=json.load(open('/tmp/list.json')); rows={r['key'][:12]: r for r in d['rows']}
for k,label in T.items():
    r=rows[k]; cj=r.get('content_ja') or ''
    open(f'/tmp/mat/{k}.txt','w').write(cj)
    print(f"{k} | {label:22} | ts={r['title_score']} | fmt={r['format']} lf={r['is_long_form']} | cj={len(cj)} | keylen={len(r['key'])}")
json.dump({r['key'][:12]: r['key'] for r in d['rows']}, open('/tmp/keymap.json','w'))
PYEOF
```
把「关联/同事件 C 级 key」也一起落盘（本次 15 个 rel 文件），写稿时按组打印：主素材全文、关联素材 `[:1200]` 截断。38 篇素材只用了 2 次读取调用。

**B 级素材同样必须看 `lf`：** 本次 3 篇 B 级（松田里奈 2861 / SKE48 37单 3413 / SKE48 仓岛杏实 4351）都是 `story lf=1`，按 SKILL.md 8/24 规则 Phase A 直接写足 1200–1700 字，没有留到 Phase C 扩充。

## 3. 提交前机械自检一条龙（36篇一次跑完）

假名（含标题行）/ 日文新字体 / `《《` / `^### ` / body 字数（story≥800）/ `##` 数量（story≥2、news 正文=0）/ 破折号≤3。本次命中并修复：
- `田沢梨乃 → 田泽梨乃`（沢属「与简体确实不同」类，照 8/16 柳沢→柳泽 先例替换）
- 保留不动的：`山本圭壱`（壱为人名固定写法）、`水嶋春人`·`长嶋凛樱`（嶋按人名约定保留）
- `みなぽち → MINAPOCHI`（4个假名，罗马字）
- `一発ギャグ → 搞笑才艺`（ギャグ 3个假名）

## 4. renwei 批量（退出码必须重定向取）

`exit0=23 / exit2=12 / exit1=1`。12 篇 exit2 全是「单类信号1处」的非聚集（格言公式 / 破折号 / 顿号分列各1处）→ 按规则接受，不改。
唯一 exit1 = AAA 名单稿：两段歌手名单各成一条顿号枚举（L9/L15）。**修法照 9/13 规则**：拆成「有…也有…还有…」三句式 + 组名间用 `·`，一段解决，重跑 exit0。

## 5. related_keys 解析（本次踩到 self-key）

```python
def find(term, exclude_full40, n=2):     # exclude 必须是40位全键，不能传12位前缀
    cur.execute("SELECT key FROM news WHERE (title LIKE ? OR rewritten_title LIKE ?) AND key<>? ORDER BY created_at DESC LIMIT ?", ...)
REL[main] = 本批同事件key(insert(0) 置顶) + 同人物历史key   # 最后统一 [:3]
```
本次 39 条中 28 条带关联。写完断言两条：①每个 related key `len==40`；②related 里不含本条自己的 key（传前缀时这条会静默失败）。

## 6. 入库 + 验证链（36/36 一次过）

单脚本循环（无一轮手工修补）：`PUT` → `GET` 校验 `rewritten_title==draft首行` 且 `len(rewritten_content)>100` → 不符则 `sqlite3 UPDATE`（title+content+publish_mode+preselected+publish_xhs+**related_keys 一起带**）→ 再 `GET` 复核。最终机械复查：`preselected=1` / `publish_xhs=0` / related 全 40 位 / gzh 行 `preselected=0`。
**gzh 稿（合规零先例赛道：逮捕/药物类）**：`sqlite3 UPDATE news SET wechat_title=?, wechat_content=?, channel='gzh', wechat_publish=0, preselected=0, related_keys=?`，验证三件套 = wechat_title 落盘 + channel=='gzh' + `rewritten_content==''`（xhs 字段未被污染）。

## 7. 密度贴合线的一次补写

38 篇密度表全跑后只有 1 篇 <30%：`8370f03d764f` 29.6%（241/813，news 类）。补一句素材内已有事实（上田从第1集起演中餐馆Toki店长、戏份分布）→ 301字/37.0% → 重入库复核。教训重申：短news 也别卡 30.0%，按 ×0.32 留余量。

## 8. 五维评分：公告类天然上限的破线尝试（AAA名单稿）

首评 7.2（爆点1.5/情1.2/增1.6/深1.4/标1.5）——纯名单公告，无引语。按 SKILL.md「先试两招再判天花板」执行 1 轮：
- 开场换成**阵眼句**（「日本这边只来了一组偶像，名字在名单的第二行：浪花男子」）
- 补背景锚定（颁奖礼分两天、演员和歌手混颁、两天名单几乎不重复）
- 结尾加读者动作（想两天都看到道枝，票得买两天）

→ 7.9（爆点1.7/情1.3/增1.6/深1.6/标1.7），落在 7.5–7.9 区间 ⇒ 标「名单公告类天然上限」通过，不再开第 2 轮。**注意：即使改稿后仍 <8，也必须重跑 renwei + 重入库（重带 related_keys）。**

## 9. score_dims 落盘与复核

一律 `sqlite3 UPDATE`（API PUT 会静默失败）。格式：story `爆点X.X/情X.X/增X.X/深X.X/标X.X|X.X分|一句话理由`；news `news-pass`；bullet `akb-top-bullet`；gzh `gzh去魅通过/背景锚定通过|X.X分|三维度：标题X.X/叙事X.X/适配X.X。理由`。
复核查询只用 `WHERE substr(key,1,12) IN (...)`（用12位做 `key IN (...)` 恒返回0行）。

## 10. 本批新增/确认的名称中文化映射

| 日文 | 中文写法 | 备注 |
|---|---|---|
| 森本くるみ（SKE48 11期） | 森本久留美 | 音译；DB 无既有写法 |
| 田沢梨乃 | 田泽梨乃 | 新字体→简体 |
| 日本ガイシホール / クロコくんホール | 日本碍子大厅 / 鳄鱼君大厅 | 场馆名 |
| バンテリンドーム ナゴヤ | 万特瑞巨蛋名古屋 | SKE48 20周年目标场馆 |
| マイスウィートピアノ | My Sweet Piano | 三丽鸥角色 |
| みなぽち / のんちゃんのお焼き / ゆうこりん | MINAPOCHI / 省略昵称写「妻子做的烤饼」 / 小仓优子 | 假名归零用 |
| ずっと真夜中でいいのに。 / あいみょん / きゃりーぱみゅぱみゅ / ももいろクローバーZ / マキシマム ザ ホルモン / レミオロメン | ZUTOMAYO / 爱缪 / 卡莉怪妞 / 桃色幸运草Z / Maximum the Hormone / Remioromen | RIJF 阵容，音乐节稿反复出现 |
| ハゴロモ / Aぇ!group / Essential | HAGOROMO / Ae！group / Essential | 品牌与团名，罗马字保留 |
| タトゥーシール / ワンオペ / ヘンナイト | 纹身贴 / 一个人带娃 / 「怪夜」 | 词条意译 |

## 11. 跳过并补关联（不写第二篇）

`cd6a0ae1f7e9`（さんま御殿，同事件第3连）按 review「等 9/22 放送后合并」跳过；改为给 9/16 已写的 `5ddc2e9a495f` 补 related_keys 指向它 + 同批被合并的 `c5236111485e`。**跳过 ≠ 什么都不做：同事件跳过项要挂到已有主稿的 related_keys 上，这样后续关联查得到。**
