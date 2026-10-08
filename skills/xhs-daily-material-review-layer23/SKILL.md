---
name: xhs-daily-material-review-layer23
category: creative
description: 每日素材review第2~3层：对S/A级素材输出价值建议（第2层），再查DB做跨时间关联（第3层）。接收第1层的输出作为输入。禁止写入操作。
temperature: 0.3
---

# 第2~3层：价值建议 + 跨时间关联

**⚠️ 重要边界：本子 agent 只能做 分析 + 查询 + 建议，禁止任何写入操作（update.sh / PUT /api/news / 写稿）。**

**📎 支持文件：**
- `references/publish-status-audit.md` — 发布状态取证：API 过滤能力实测表、DB 定位与只读副本配方、SQL cookbook、9/23 实测样例。
- `scripts/person_pub_audit.sh` — 逐人物发布状态聚合（入库数 / 已发布 / lastpub）+ 管道健康度，一条命令出结论。

## 输入

你需要先从主进程或第1层的结果中获取：
- 今日的S级和A级素材列表（含key和标题）
- 聚类信息

**注意：作为 delegate_task 子 agent 运行时，第1层结果会通过 context 字段注入。** 读取 context 中的 layer1 输出，以 layer1 的 S/A 分级为准。**不要重新拉取全量素材自行判定 S/A 级别。** 如果需要读取某条素材的 content_ja（列表 endpoint 已包含该字段），可以通过 `r.get('content_ja', '')` 获取，不需要额外 curl。

如果 context 中没有传递 S/A 列表，再自行调用 API 获取并说明原因。

### ⚠️ 并行模式下的分类对齐陷阱（7/8补充）

**场景：** 当主进程使用并行 delegate_task（3层同时分发）时，layer23 的 context 中不会有 layer1 的 S/A 列表——因为 layer1 还没做完。此时 layer23 会自行拉取今日全量素材，但很容易使用与 layer1 不同的判定阈值，导致分类膨胀（e.g., 10 S vs layer1's 5 S）。

**并行模式下正确的处理方式：**

1. **拉取全量素材后，优先复用 layer1 的分级逻辑**（而非自创一套）：
   - ts>=5.0 + cj_len>=500 = S候选（与layer1对齐）
   - 优先选有"事件型/争议性/话题爆发"属性的素材
   - 慎选商务合作/轻量晒照类素材（这些layer1通常给A或B）

2. **在报告中明确标注「本分析基于自行拉取的今日数据，非 layer1 传入结果」**，以便主进程在汇总时识别分类偏差。

3. **输出时使用「建议S级」「建议A级」而非「S级」的措辞**，向主进程传递「这是基于API数据的自行判断、非layer1正式输出」。或者确实按 layer1 的标准分级，但要注明依据。

4. **不要同时保留 layer1 的分类和 layer23 的分类**——如果 layer23 重新做了全量分级，就直接输出 system-graded 结果，主进程在汇总时选择信任哪一套。

5. **限制自创S级数量在5-8条** — 超过这个数通常是阈值放太宽。56条素材中真正适合S的不会超过15%。

### ⚠️ 关键：不要从 workspace 搜索旧报告文件（7/5教训）

**不要使用 `search_files(target='files', pattern='material_review_layer1_*')` 或在 workspace 中查找早期的 layer1 报告文件。** 这些文件是之前日期的存档，其素材与今天的素材完全不同。从 workspace 找到的旧文件会导致整个分析跑偏（7/5实际案例——子agent找到6/26的存档文件并基于它分析了7/5的素材）。

**正确的做法：**
1. 主进程的 context 已包含今天的 S/A 列表 → 直接使用
2. 如果需要 content_ja → 调用 API 获取今日素材（`date_from=2026-XX-XX`）
3. 如果需要确认关键 → 使用 API search 查询关键词，不要搜索本地文件

**主进程的验证责任：** 主进程必须在汇总时检查 layer23 报告中的素材标题/日期是否为当天。如果子 agent 误用了旧数据，主进程应自行补做第2~3层分析。

## 第2层：价值建议

对每个S级和A级素材，给出：
- **标题方向**：1-2个选题角度
- **写法建议**：冷静叙述/场景细节/引语引入
- **素材充实度**：是否足够写一篇完整稿（要实际读content_ja判断）
- **情绪预期**：读者可能的情感反应
- **优先级排序**：哪个素材优先处理

## 第3层：跨时间关联

### 3a. 查同关键词/同人物

**⚠️ 关键方法（6/28修正）：直接用API的 `search=` 参数搜索，不要用 limit=200 全量拉回后Python过滤。search 参数支持对 title+content_ja 全文搜索，不受 limit 截断影响。**

```bash
# ✅ 正确用法（推荐）：-G --data-urlencode，中文不需要手动编码
curl -s --noproxy '*' -G "http://127.0.0.1:5000/api/news" \
  --data-urlencode "sort_by=created_at" \
  --data-urlencode "sort_dir=DESC" \
  --data-urlencode "limit=200" \
  --data-urlencode "search=能条爱未" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'命中: {d.get(\"total\",0)}条')
for r in d.get('rows',[]):
    rew = r.get('rewritten_title','') or '(无)'
    pub = r.get('xhs_pub_time','') or '(未发)'
    v = r.get('xhs_views',0) or 0
    print(f'{r[\"key\"][:12]} | created={r.get(\"created_at\",\"\")[:16]} | pub={pub} | v={v} | rew={rew[:35]} | {r[\"title\"][:50]}')
"

# 快速多人物批量查询方法（shell函数版）
search_kw() {
  curl -s --noproxy '*' -G "http://127.0.0.1:5000/api/news" \
    --data-urlencode "sort_by=created_at" \
    --data-urlencode "sort_dir=DESC" \
    --data-urlencode "limit=200" \
    --data-urlencode "search=$1" | python3 -c "
import sys, json
d=json.load(sys.stdin)
print(f'$1: {d.get(\"total\",0)}条')
for r in d.get('rows',[])[:5]:
    print(f'  {r[\"key\"][:12]} | ts={r.get(\"title_score\",0)} | {r[\"title\"][:50]}')
"
}
# 用法：search_kw "柏木由纪"; search_kw "高桥恭平"
```

**❌ 不要这样（6/28教训）：** `limit=200` 全量拉回Python过滤。当DB总记录超过200条时（6/28时已有500+条），大量旧记录不在前200条内，同人物旧文章完全搜不到。

**URL编码参考：**
- `python3 -c "import urllib.parse; print(urllib.parse.quote('能条爱未'))"` → `%E8%83%BD%E6%9D%A1%E7%88%B1`
- 也可以直接用 `--data-urlencode` 参数：`curl -G --noproxy '*' --data-urlencode "search=能条爱未" "http://127.0.0.1:5000/api/news"`

**注意事项：**
- `search` 参数搜索 title + content_ja 全文
- `limit=200` 控制返回条数，搜索范围是全部记录（不只是前200条）
- `total` 字段显示实际命中的总数
- **推荐用 `-G --data-urlencode` 模式**传中文参数，避免URL编码和shell特殊字符冲突
- **不要用 `for kw in ...; do curl ... | python3 -c "kw='$kw'; ..."` 模式**——python3 -c 中嵌套shell变量和中文引号极易冲突（JSONDecodeError）。用 `-G --data-urlencode "search=$1"` 解决
- API返回空结果时用 `.get('rows',[])` 而不是 `['rows']` 避免KeyError

**⚠️ 效率坑（7/3教训）：delegate_task子agent只有50次tool call上限。** 每个S/A素材独立调用curl做search + 读content_ja，8个S/A素材轻松消耗16-24次curl调用 + 8次content_ja读取 = 24-32次调用，再多做几次就撞max_iterations。**批量查询策略：**
1. 用API的 `search=` 参数一次查一个关键词（这是对的，不能换）
2. 但可以**合并content_ja读取**— list endpoint已返回content_ja字段，不需要额外curl读详情
3. 对**同人物多次**的素材（如龟梨27条、草间18条），用`--data-urlencode "search=关键词"` + `--data-urlencode "limit=5"` 只返回最新5条就够了，不需要200条
4. 先做完所有search再分析，不要在每次search之间做长篇分析——分析也会消耗tool call
5. 如果8个S/A素材→搜索8-16次→已用8-16次调用→必须控制剩余的调用给content_ja读取（0次，已在list数据中）和最终输出
6. **（9/23 实测）shell 函数 + 一次 terminal 调用可跑完 ~30 个关键词（含简繁双查），全程只花 1 次 tool call。** 做法：用 `write_file` 落两个文件 —— `/tmp/parse.py`（从 stdin 读 JSON、`python3 /tmp/parse.py "$kw"` 打印 total + top5）和 `/tmp/kw_batch.sh`（定义 `search_kw()` + `for kw in A B C ...; do search_kw "$kw"; done`），然后 `bash /tmp/kw_batch.sh` 一次执行。**解析器一定要落成独立 .py**，避免 `python3 -c` 里嵌中文/引号/`$kw` 炸掉（这是 6/28 教训的根因）。这是本层最省调用的形态，务必用这个模式，不要逐关键词 curl。

### 3b. 关联类型判断

| 关联类型 | 含义 | 处置 |
|---------|------|------|
| 纯重复 | 同一新闻事件，标题类似 | 建议跳过 |
| 时间线补充 | 旧事件有新进展 | 建议合并写新版 |
| 人物呼应 | 旧文写了A，新素材有新事件 | 建议写续篇 |
| 新角度 | 旧文写了X面，新素材给了Y面 | 建议写不同角度 |
| 无关联 | 无记录 | 新选题 |

### ⚠️ 常见错误：子 agent 断言「search只查active数据」（7/12教训）

**错误现象：** 子 agent 使用 search 参数后声称「API的search参数仅支持查询active状态（今日入库）素材，published/archived状态的旧记录无法通过search检索」。

**这是错误的。** 验证结果：search 参数**确实能查全库历史记录**。例如：
- `search=上白石萌音` → 返回 9 条匹配，含 6/22 已发布的旧记录（有 xhs_pub_time）
- `search=中森明菜` → 返回 13 条匹配，含 7/11、7/4 等旧记录

**根因分析：** 子 agent 在测试 search 时仅返回了 0 条直接关联（同人物在同批数据中无命中），错误地将「同批无关联」结论扩大为「search 不查过去数据」。这是一种错误的因果推断——「没有命中旧记录 ≠ search 不查旧记录」。

**铁则：** 当 search 返回 0 条但你需要确认是否真的没有旧记录时：
1. 换个更宽泛的关键词再搜一次（如「龟梨」而非「龟梨和也被整蛊」）
2. 用 `limit=5` 缩小返回量看是否有任何 past 日期出现
3. 检查返回的 `total` 字段是否 >0
4. **永远不要断言「search 不能查历史记录」**——这个说法已经有多个反例证伪

**主进程验证责任：** 如果子 agent 报告「search 只查 active 数据」或类似断言，主进程应自行验证（快速 curl search 一个已知有人物的关键词），不要直接采信子 agent 的负向技术断言。

### ⚠️ 同名/近似名人物混淆（8/4教训）

**search= 命中记录必须先核对人物身份，再判断关联类型。** 8/4 查「水川」命中5条，其中2条是水川堇（不同人，7/11-7/14已发布 v=42957），险些把「水川堇有流量基础」误当成「水川かたまり有历史记录」。单姓/常见名/名字相近的检索（水川、佐久間、山田等）极易命中多个不同艺人。

处理：命中记录看 rewritten_title / content 上下文确认人物；拿不准时用全名再搜一次（如「水川かたまり」「空気階段」）复核；报告里标注「同名混淆项已排除」，让主进程知道该排除动作。

### ⚠️ 简繁双查（8/5教训）

**今日素材标题多为简体中文，而 DB 历史记录（title/rewritten_title/content_ja）多为日文汉字——search= 只查一种形式会漏掉历史记录。** 8/5 案例：对高桥恭平/土屋太凤/桥本萌花/藤岛果步做了简繁双查（如「藤岛果步」+「藤島果歩」、「高桥恭平」+「高橋恭平」）才命中历史记录；单查简体时 0 命中不代表无关联。

操作：对每个 S/A 素材的人物名/事件关键词，**简繁各查一次**（`--data-urlencode "search=简体"` 和 `--data-urlencode "search=日文汉字"`），报告里注明「已简繁双查」，避免主进程把「单形式无命中」误判为「无关联」。多人物批量查询仍用 shell 函数版 + `-G --data-urlencode`，注意 50 次 tool call 上限（简繁双查会让 S/A 素材的搜索次数翻倍，8 个素材约 16-24 次，仍在预算内）。

**⚠️ 方向补充（9/16 实测，与 8/5 方向相反）：** 本库历史记录（title/rewritten_title/content_ja）**以简体中文为主**，日文汉字形式多为 0 命中。9/16 实测：仓木华 6 / 倉木華 0；上坂堇 2 / 上坂すみれ 0；芦田爱菜 18 / 芦田愛菜 0；堂本剛 0 / 宮田恭男 0 / 西野七瀬 0 / 山田裕貴 0 / トモダチ100人 0。**结论不变（仍须简繁双查，双查不会漏），但注意：凡出现「简体命中 N 条 + 日文汉字 0 条」时，这是库内语言分布的常态，不是「日语素材无历史记录」的信号。** 报告里可直接写「简体命中 N 条，日文汉字形式 0 命中（库内以简体为主，属常态）」。

**（9/23 实测补充）但日文汉字形式并非总是 0 命中 —— 团体成员的正式汉字/假名名有可观存量，不要预判为 0：** 宫馆凉太 132 / 宮舘涼太 7、深泽辰哉 111 / 深澤辰哉 9、土屋太凤 65 / 土屋太鳳 3、青叶坂46 31 / 青葉坂46 2、石田千穗 5 / 石田千穂 1、石川小百合 5 / 石川さゆり 0。结论仍是「必须双查」，但**两个形式的命中条数要分别如实报出**（用于合并去重），不要用「日文形 0 命中」当默认预期去推断历史无记录。

### ⚠️ 措辞纪律：「已发布」≠「定时发布队列」（8/18教训）

关联查询命中**昨日/更早入库**的记录时，先核对 `xhs_pub_time` 再下结论：
- `xhs_pub_time` 为空 → 未发布
- `xhs_pub_time` > 当前时间 → **定时发布队列**（排定未来发布，发布前 0 阅读/0 互动属预期，#112/#114 口径）——报告里写「已排定今日HH:MM定时发布」，**禁止写「已发布」**
- `xhs_pub_time` < 当前时间 → 已发布

8/18 案例：青叶坂46（d753bf32eca0，created 8/17，pub=2026-08-18 11:10）被本层误报「今日11:10已发布」，实际是定时队列（当日凌晨02:30运行时尚未发布）。跳过结论不受影响，但措辞错误会误导下游把定时队列的 0 数据误读为「发布后冷门」。深夜/凌晨跑 review 时（日本时间 < 发布时刻），「已发布」的说法基本都该先怀疑是定时队列。

### ⚠️ 多连入库0发布 = 评审搁置信号（9/9教训）

第3层命中同人物/同系列多条历史记录时，顺带统计发布状态（`xhs_pub_time` 是否为空）。若同一人/同一系列**连续 N 条入库但全部未发布**，说明评审层对该题材持续审慎搁置——这是重要先例信号，不是「无关联」：

- 案例①：るるたん系列 9/2「首秀盛况」(948db850cc9) → 9/3「时薪8000到月入1亿」(7cb1cf5f53ab) → 9/5「太客不伺候」(b53c934ade5b) → 9/9 第4条，**4连入库0发布** → 建议慎重/走公众号深度方向/合规不通过则跳过
- 案例②：电影《我々は宇宙人》宣发 8/26×3 → 8/28 → 8/29 共5波全未发 → 今日第6波宣发建议跳过或等上映窗口+目标赛道人物（山崎天）作钩子

处理：报告中注明「同系列 N 连入库 0 发布先例」，第2层建议相应降级或改道（走公众号/深度方向），不能当普通「无关联/可写」处理。

**⚠️ 边界条件（9/10修正）：先区分「全局管道停摆」vs「局部评审搁置」。** 若**全库** XHS 最近实际发布已是数天前（9/10 运行时最新 pub=09-06 08:59，停摆第4天）、published 集内无 <48h/24-48h 条目、定时队列为空——此时所有系列都呈现「多连0发布」，是**发布管道停摆**（流程状态），不是评审层对题材的持续搁置。处理：报告中注明「XHS 发布管道自 <日期> 后停摆 N 天，多连0发布为管道状态非搁置信号」；「搁置信号」强判定仅适用于管道存活期已有过产出（XHS已发>0）的旧批次系列。9/10 案例：板野友美更衣室系列 09-06→09-10 连续5批15+条0发布、而本人同期生活向正常过审 → 仍判强搁置；09-09/09-10 新爆发系列（AKB×中国中止、松田里奈毕业）只标「浓度风险」不标「已搁置」。同时把管道停摆作为流程告警写入报告，提醒主进程写稿/入库前确认发布管道与评审队列状态。

### 📌 发布状态 / 搁置信号的取证方法（9/23 新增，重要）

**痛点：** 3b 要求判定「同系列 N 连入库 0 发布」与「管道是否停摆」，但 **API 没有按发布时间过滤/排序的能力**。9/23 实测以下参数全部被服务端**静默忽略**（仍返回全量 7436 条、仍按 created_at DESC）：

```
sort_by=xhs_pub_time / pub_time / publish_time / updated_at   ← 全部忽略
xhs_pub_from=2026-09-01                                       ← 忽略
status=published / published=1                                ← 忽略（total 仍 7436）
/api/stats /api/overview /api/summary /api/health             ← 全部 404
```

**正确做法：读原库的「只读副本」 —— 这是查询，不违反本层「禁止写入」铁则。**

```bash
# 1) 定位库文件（app 的 cwd 即项目根，DB 在 data/news_dev.db）
lsof -p $(pgrep -f "web/app.py" | head -1) | grep cwd
#   → 例如 /Users/user/PG/XiaohongshuSkills  ⇒  DB = <cwd>/data/news_dev.db

# 2) 复制副本到 /tmp（含 -wal/-shm 才读得到最新提交），之后只查副本
cp <cwd>/data/news_dev.db /tmp/nd.db
cp <cwd>/data/news_dev.db-wal /tmp/nd.db-wal 2>/dev/null
cp <cwd>/data/news_dev.db-shm /tmp/nd.db-shm 2>/dev/null
```

逐人物聚合直接用 `scripts/person_pub_audit.sh <人名...>`；手写等价 SQL：

```sql
SELECT title, count(*) n,
       sum(CASE WHEN COALESCE(xhs_pub_time,'')!='' THEN 1 ELSE 0 END) pub,
       max(xhs_pub_time) lastpub
FROM news
WHERE title LIKE '%人物名%' OR content_ja LIKE '%人物名%' OR rewritten_title LIKE '%人物名%';
```

**⚠️ API `search=` 未索引 content_ja 日文正文（9/29 实测，主进程已独立复核）：** `search=我々は宇宙人` → API **total=0**，而只读副本三字段 OR → **28 条**（主进程实测确认 28）。多组日文形关键词（我々は宇宙人／大島涼花／浜崎あゆみ／梅澤美波／矢久保美緒／桑原みずき／高宮麻里）API 均为 0，而这些命中记录**只出现在 content_ja、不出现在 title**。对照组 API 正常返回（佐藤綺星 7／猪俣周杜 48／金村美玖 81／指原莉乃 100，均为主进程复核值）。**结论：API `search=` 覆盖 title／中文 content／rewritten_title，但不覆盖 content_ja 日文正文。** 这不否定「search 能查历史记录」（title 命中确实可见），但**凡用日文汉字/假名关键词做历史关联，必须改用只读副本三字段 OR**，API total=0 不可直接判「无关联」——这是 6/28「limit 截断漏旧记录」教训的同类陷阱（负向结果不可轻信单一通道）。

**三条铁则：**

1. **必须做 title + content_ja + rewritten_title 的 OR 全文匹配**，不能只用 `title LIKE`。9/23 实测：西畑大吾 只用 `title LIKE` = 61 条 / 0 发布，全文 OR = 144 条 / 4 发布 / lastpub 07-16。**只用 title LIKE 会系统性夸大「0 发布」**，进而把「管道有产出的人物」误判成强搁置。
2. `sqlite3 -readonly <path>` 在 WAL 库上会报 `unable to open database file (14)` —— 不要在这个报错上纠缠，直接用**复制副本**的办法（第 2 步），既读到最新数据又不碰原库。
3. 「管道停摆 vs 局部搁置」（9/10 口径）现在可以**实测**：先看全库最新 pub 日期 + 当日发布条数 + 定时队列是否为空。9/23 实测最新 pub=09-22 18:24、当日 4 条、定时队列空 → 管道**存活但积压严重**（今日入库 52 条）⇒ 多连 0 发布按**局部搁置**判定，同时把「积压率」作为流程告警写进报告。

### 3c. 输出格式

每个S/A级素材输出：
```
素材：<标题>
第2层（价值建议）：
  - 标题方向：...
  - 写法建议：...
  - 素材充实度：...
  - 预期情绪：...
  - 优先级：...
第3层（关联分析）：
  - 查<人物>：命中<N>条旧记录 / 无关联
  - 关联类型：<类型>
  - 建议：<写或跳过>
```

## 禁止操作

- 禁止调用 update.sh
- 禁止 PUT /api/news
- 禁止写稿件正文
- 禁止标记 publish_xhs / preselected
