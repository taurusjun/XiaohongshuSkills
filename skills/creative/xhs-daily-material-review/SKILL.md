---
name: xhs-daily-material-review
category: creative
description: 每日小红书素材review。四层流程：1全量扫描→2价值建议→3跨时间关联→4发布数据回顾。完成后用 segment-send.py 分段发送到Telegram。
triggers:
  - User says "今天的素材" or "素材review" or "今日review" or "开始review" or "新素材总结"
  - User says "开始今日review" or "开始每日review"
  - 每天固定cron跑素材review
  - ⚠️ 用户说"开始review"时，立即加载本skill（skill_view），不要自己猜流程。已有多条目测错误记录了用户自己发现后纠正。
temperature: 0.3
---

# 每日素材 Review

### 执行纪律（最重要的一行——读三次）

**技能里写了多少步，就执行多少步。不能做80%跳20%。** 每条步骤做完后问自己：「这条步骤要求的所有子步骤我都做了吗？」而不是「差不多可以进下一步了吗？」

**用户不需要当你的QA。** 没有第二次机会——定时任务跑完直接输出，用户在那边等结果。

### 入口规范

**用户说"开始review" / "今日review" / "开始今日review"时，第一步必须是 skill_view('xhs-daily-material-review') 加载本skill。** 不要跳过加载直接开始查数据。7/7教训：用户发现我没加载skill，主动问"没有触发每日review的skill吗"。

### ⚠️ 交互模式执行铁律（7/24新增，7/24补充）

**1. 先检查存档文件，再决定怎么跑。**

用户触发 review（无论是 cron 跑完后用户追问，还是直接要求 review），**不要默认重新拉API数据+重新跑四层**。第一步先检查当天存档文件是否存在：

```bash
ls ~/.hermes/daily-reviews/$(TZ=Asia/Shanghai date '+%Y-%m-%d').md 2>/dev/null && echo "EXISTS" || echo "NOT_FOUND"
```

- 如果存档存在 → 直接用 read_file 读取完整内容发到对话，不用再跑一次四层流程
- 如果存档不存在 → 按完整四层流程跑

**2. 不要展示中间步骤给用户。**

用户要的是最终结果（要么已存档文件、要么跑完后的完整存档）。所有拉数据、聚类、分析、API查询都在后台完成，只有最终 write_file 存档完成后，一次性发结果给用户。不要在过程中一步步展示拉取的数据、聚类分析、分级列表。

**3. 不要在未确认最终产出前说"跑完了"或"完成了"**

必须实际确认以下至少一项才能汇报"完成了"：
- write_file 到 daily-reviews/ 成功（文件存在且>0字节）
- segment-send.py 发送成功
- cron 的 last_status=ok 且在当天有运行记录

没有确认最终产出前，用"正在跑""触发了一次""重试中"等状态描述，不用"跑完了"。

**4. ALWAYS DELIVER CONTENT DIRECTLY — NEVER SAY "往上翻"**

这是最高优先级规则。当用户问"结果呢"或"review呢"时：

- ❌ **绝对禁止**："review结果已经发到这个对话了——往上翻可以看到"、"你看上面我的回复"、"之前的消息里有"、"滚动上去看"
- ✅ **必须做**：
  1. 先查存档文件（如上方案1-2步）
  2. 如果存档存在 → read_file，把内容直接贴到回复里给用户看
  3. 如果存档不存在 → 查 crontab job 的 last_run_at / last_status → 判断是否需要重跑
  4. 即使刚才同一个对话里说过了、用户没看见，也**重新贴一次内容**，不假设用户翻到了

**5. 用户说"停止"时必须立即停止，不要找理由**

用户说"不要触发" / "停止" / "停" 时：
- 立即停止任何正在执行的触发性操作（创建job、run job、触发流程）
- 已经跑了的job不要再说"跑完了"或"已经发出了"
- 直接承认停下来了。不要说"已经跑完了所以没办法"——用户要的是你停止操作，不是听解释

**6. cron 跑完后用户问"结果呢"时的处理流程**

用户说"没看到"或追问结果时，按此优先级：
1. 先查存档文件是否存在（已写完但没投递到的概率不低）
2. 存档存在 → 直接用 read_file 发给用户（不要让他们翻历史！）
3. 存档不存在 → cronjob action=run 重跑（同时确认 last_run_at 是否已更新）
4. 如果 cronjob 返回 error（如 503）→ 用户如果急，主进程直做四层

---

---

## ⚠️ 重要：delegate_task 使用边界

**允许：** 用 delegate_task 分发 review 四层流程（第1层/第2~3层/第4层）给子 agent。各层子 agent 加载对应的 layer skill（`xhs-daily-material-review-layer1`, `xhs-daily-material-review-layer23`, `xhs-daily-material-review-layer4`）。

**禁止（子 agent 内）：**
- 写稿（调用 xhs-write-publish-flow 写稿件内容）
- 直接 DB 写入（update.sh / PUT /api/news 更新 publish_xhs、preselected 等）
- 标记发布

这些操作只允许**主进程**执行，或者在用户明确指令下执行。

**cron 模式 vs 交互模式：**
- **cron 模式**（每日定时跑）：用 `delegate_task` 分发各层给子 agent（已验证可行），主进程做汇总→存档→审计→输出。子 agent 加载各自 layer skill。也可以用主进程直做——两种方式均可，选择取决于 API 负载和用户偏好。
- **交互模式**（用户直接说"素材review"时）：主进程直做四层，理由同之前（交互模式下用户要的是即时反馈，不需要子 agent 预热开销）。

---

## 四层流程（严格按顺序）

### 第1层：全量扫描

#### 1a. 拉取全量当日素材

```bash
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=$(date '+%Y-%m-%d')&limit=200" | python3 -c "
import sys, json; d = json.load(sys.stdin)
print(f'Total: {d[\"total\"]}')
for r in d['rows']: print(r.get('key'), r.get('title',''), r.get('title_score'), r.get('content_score'))
"
```

先查总数确认范围。

#### 1b. 读取关键素材的内容

对以下素材必须看内容详情，不能只看标题：
- story_type='story' 或 format='story' 的条目
- title_score >= 4.0 的条目
- 目标赛道内（坂道系/AKB/娱乐女艺人）的所有条目
- 有争议标题的条目

#### 1b-1. ⚠️ 高效过滤：优先按 fetch_by 而非 category 过滤

**7/7教训：** 24条「体育」分类其实是グラビア模特内容，不是真正的体育新闻。`fetch_by` 字段比 `category` 更可靠地表示素材来源和类型。

| 需关注的 fetch_by | 可跳过的 fetch_by |
|-------------------|-------------------|
| AKB, 乃木坂, 櫻坂, 日向坂, アイドル | グラビア, セクシー女優 |
| Snow Man, なにわ男子（公众号方向） | |

**操作建议：** 批量拉取后先用Python统计 `fetch_by` 分布（而不是 `category` 分布），0.5秒内判断今天的素材结构。

#### 1c. 同事件条目聚类

先聚类再分级。同事件多条→保留1-2条最丰富的，其余合并跳过。标注聚类原因。

**聚类到同事件时注意「日期陷阱」（6/25教训）：** 同事件素材可能在昨天入库、昨天已发布。聚类时不要只看今天的素材，还要检查前一天的已发布素材。

#### 1d. 输出格式

先按来源分组输出全量列表，再聚类分析，再分级结果（S/A/B/C）。

#### 1e. 分级标准

| 级别 | 定义 | 处理 |
|------|------|------|
| S级 | 强烈推荐 | 优先写长文 |
| A级 | 可选 | 可选写稿 |
| ✅ AKB大TOP | 必须入库 | 写短news入库，后续决定发不发 |
| B级 | 轻量跑量 | 写500-600字短news |
| C级 | 跳过 | 跳过并说明原因 |

AKB大TOP「事件型」vs「晒照型」分流：
- 事件型（育儿/争议/对话/爆料）→ 写全文，preselected=1
- 晒照型（全家福/迪士尼/日常照）→ summary bullet入库（preselected=1但rewritten留空）

### 第2层：价值建议

对S级和A级素材，给出标题方向、写法建议、素材充实度、情绪预期、优先级。

### 第3层：跨时间关联

#### 3a. 查同关键词/同人物

**⚠️ 关键方法（6/28修正）：直接用API的 `search=` 参数搜索，不受limit截断影响。search会搜索全部记录（title+content_ja全文），返回按sort_by排序的前limit条。**

```bash
# ✅ 正确用法（推荐）：-G --data-urlencode，中文不需要手动编码
curl -s --noproxy '*' -G "http://127.0.0.1:5000/api/news" \
  --data-urlencode "sort_by=created_at" \
  --data-urlencode "sort_dir=DESC" \
  --data-urlencode "limit=200" \
  --data-urlencode "search=花田蓝衣" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'命中: {d.get(\"total\",0)}条')
for r in d.get('rows',[]):
    rew = r.get('rewritten_title','') or '(无)'
    pub = r.get('xhs_pub_time','') or '(未发)'
    v = r.get('xhs_views',0) or 0
    print(f'{r[\"key\"][:12]} | created={r.get(\"created_at\",\"\")[:16]} | pub={pub} | v={v} | rew={rew[:35]} | {r[\"title\"][:50]}')
"
```

**❌ 不要这样（6/28教训）：** `limit=200` 全量拉回Python过滤。当DB总记录超过200条时，大量旧记录不在前200条内，同人物旧文章完全搜不到。6/28时DB已有500+条记录，limit=200只覆盖最近的200条，能条爱未的34条旧记录一条都没触达到。

**6/25教训：** 花田蓝衣事件6/23晚爆发→6/24第一批素材入库并发布。查旧记录时因为用了 `date_from=2026-06-23&search=花田蓝衣`（search对中文不准）没命中，就以为「没有旧记录」跳到5月翻SKE旧账。实际上就在**6/24有3条同事件素材**。

**注意事项：**
- `search` 参数搜索 title + content_ja 全文
- `limit=200` 控制返回条数，搜索范围是全部记录（不只是前200条）
- `total` 字段显示实际命中的总数
- **推荐用 `-G --data-urlencode` 模式**传中文参数，避免URL编码和shell特殊字符冲突
- **不要用 `for kw in ...; do curl ... | python3 -c "kw='$kw'; ..."` 模式**——python3 -c 中嵌套shell变量和中文引号极易冲突（JSONDecodeError）。用 `-G --data-urlencode "search=$1"` 解决
- API返回空结果时用 `.get('rows',[])` 而不是 `['rows']` 避免KeyError

#### 3b. 关联类型判断

| 关联类型 | 含义 | 处置 |
|---------|------|------|
| 纯重复 | 同一新闻事件，标题类似 | 跳过 |
| 时间线补充 | 旧事件有新进展/后续 | 合并写新版 |
| 人物呼应 | 旧文写了A，新素材写了A的新事件 | 可以写续篇 |
| 新角度 | 旧文写了X面，新素材给了Y面 | 写不同角度新篇 |
| 无关联 | 无记录 | 新选题 |

#### 3c. 执行纪律

**第3层不能只写「待补充」。** 查出结果再写。如果同关键词没查到，拉全量（limit=200）再查一次。确定没有后标记「无关联」而不是「待补充」。

**写稿后的第3层（6/25教训）：** 写稿前必须做第3层查询。跳过第3层会导致写稿内容与旧素材重复（花田蓝衣写了第一波的内容，但第一波昨天已经发过了）。第3层和写稿不是先后关系，是因果关系。

### 第4层：发布数据回顾

#### 4a. 查询方式

```bash
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?publish_xhs=published&sort_by=xhs_pub_time&sort_dir=DESC&limit=50"
```

#### 4b. ⚠️ 关键检查点（6/25被用户纠正过的坑）

**每一条 published 数据，必须先检查这三个字段，再分析：**
1. `publish_mode` — 是 rewritten / caption / normal？
2. `rewritten_title` — 实际发布的标题（≠原始title！原始title是feed抓取时的标题）
3. `rewritten_content` — 有改写内容还是空？

**花田蓝衣案例（6/25）：** 原始title=`花田蓝衣剃光头，律师要战全世界`，但实际发布的rewritten_title=`AKB48史上首次解约，她剃了光头`。原始分析完全跑偏了，因为分析的是原始title而不是实际发的。

#### 4c. 数据分层

按 >48h（充分分析）/ 24-48h（初动参考）/ <24h（刚发不计）三段输出。

**每条>48h数据都输出反馈闭环（当初判断→实际数据→结论）。**

#### 4c-1. ⚠️ 数据判定边界规则（7/7修正）

**核心原则：刚发不足24h的数据不得用作否定性结论的证据。**

| 时间窗口 | 可做什么判定 | 不可做什么 |
|----------|------------|-----------|
| <24h | 只记录原始数据 | **不可**下"爆款"/"冷门"等结论 |
| 24-48h | 初动参考（趋势判断） | 不可做最终定论 |
| >48h | 充分分析+反馈闭环 | 可以做 |

**特殊规则——强IP（OG级人物）：**

前田敦子7/6发的跨类目素材在7/7查询时显示0阅读，但用户指出这是不恰当的过早判定。原因是：

- **强IP（AKB OG级：前田敦子、板野友美、大岛优子等）有粉丝主动搜索的流量护城河**，前24h曝光路径与普通艺人不同
- 非普通用户的粉丝会在发布后数小时到数天内通过搜索/关注列表到达内容
- 因此：**强IP素材即使<48h也慎重下"零阅读/冷门"结论**，标注"当前数据，待观察"

**判定检查清单（写每条反馈闭环前自查）：**
1. □ 这条素材发布了多久？（<24h→不写结论 / <48h→标"待观察" / >48h→可以分析）
2. □ 素材主角是OG级强IP还是普通艺人？（OG级放宽1个时间段）
3. □ 原始title有没有自带争议性/冲击力/民生属性？（模式#11/#13的信号词）
4. □ category是否与素材内容匹配？（跨类目→标注风险但不下定论）

#### 4c-2. ⚠️ xhs_pub_time 格式兼容 & CTR 空数据陷阱

**xhs_pub_time 格式：** API 返回的发布时间格式为 `2026-07-28 17:50`（16字符，无秒）。**不要用** `strptime(..., '%Y-%m-%d %H:%M:%S')`——会 ValueError。统一用：

```python
dt_str = pub[:16]
pub_dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M').replace(tzinfo=cst)
```

**CTR 数据大概率全空：** `xhs_ctr` 字段常常是空字符串 `""` 而非数字。解析时先检查空值再转 float，如果全部为空则在存档中标注「CTR数据缺位」。**9/18 实测进一步发现：`xhs_ctr` 有时整个字段在返回里根本不存在**（child agent 报告「xhs_ctr字段不存在→全758条空缺位」），而非空串——用 `.get('xhs_ctr','')` 并同时接受「字段缺失」与「空串」两种情况，不要因 None/KeyError 崩掉。同批 `xhs_click_rate` 758/758 全部有值，可作 CTR 替代口径。

**⚠️ pub_time 解析不兼容会导致分层数字错（9/18 实测）：** 除主流格式 `YYYY-MM-DD HH:MM`（16字符）外，published 集内还混有两种异形记录：① **8 条 `xhs_pub_time` 为空**（4-5月期旧记录，只有 `publish_time` 字段）② **8 条为 10 字符纯日期**（如 `2026-05-17`，无时分）。解析器只认 16 字符格式时，这两类会被异常吞掉或归错桶——9/18 初版误得 `737/18`，改用「空值分流 + try/except + 长度判断」的稳健解析器后才得到正确的 `747/8`。**写完趋势行后若发现分层数字变动，必须回头改那一行**，不能只改正文表。

详见 `references/layer4-datetime-pubtime-quirks.md`。

#### 4d. 强制更新 data-feedback-patterns.md

- 至少追加一条新模式（带日期标签和案例）
- 跨会话趋势表最后一行更新
- 格式与已有记录一致

**⚠️ 追加位置铁律（8/13实测）：data-feedback-patterns.md 的结构是「模式节… → ## 跨会话趋势表（文件末尾唯一一张表）」。更新时禁止无脑 `cat >>` 追加到文件末尾——模式节必须插在趋势表**之前**（以 `## 跨会话趋势表` 为界，在其前插入新模式节），趋势表新行必须插在表内最后一行数据之后、表结束前。8/13 曾直接 append，结果：①文件尾部凭空多出一个空表头 `## 跨会话趋势表` ②新趋势行孤悬表外，导致趋势表节数=2（审计Step 3会报错）。修复方法：先 `c = open(path).read()`，用 find 定位 `## 跨会话趋势表` 和最后一个 `| 日期 |` 数据行，分别插入；或先删除坏块再插入。8/13 实测文件 991→1000 行结构修复成功。**

**⚠️ 追加实现的两个 Python 坑（8/15实测）：** ① 用 `c.index(anchor)` 定位锚点时，如果此前做过一次 `c.replace(...)` 修改了 c，第二次 index 可能因 anchor 已被替换而抛 `ValueError: substring not found`——先 `print(anchor in c)` 再 index，或把两次修改写进同一个脚本分步打印。② 追加趋势行时，**文件最后一行可能没有尾部换行**（8/15：8/14趋势行恰是文件末行、无 `\n`），`c.index('\n', idx)` 会抛 ValueError。正确写法：`line_end = c.find('\n', idx); if line_end == -1: line_end = len(c)`，然后 `c = c[:line_end] + '\n' + new_row + '\n' + c[line_end:]`。

### 4e. 第4层数据解读的边界条件

**写入data-feedback-patterns.md的模式必须经得起「反例」检验。** 当一个素材的0阅读数据存在以下干扰变量时，不要断言单一因果关系：

| 干扰变量 | 案例 | 影响 |
|---------|------|------|
| **发布时间太短** | 某素材发后4h=0阅读，但48h后可能自然爬升 | 不足24h的数据不做>48h结论 |
| **IP自带流量** | 前田敦子跨体育类目发0阅读，但她之前红毯953、提前男友257（有流量基础但当前数据未归位） | 前田系素材的0阅读不代表「前田不行」，可能只是曝光周期+跨类目双重影响 |
| **单个/零星反例** | 模式#12最初案例全部0阅读，但前田敦子系同模式下有的数据不差 | 出现1个反例就要修正模式、加边界条件，不能当异常值忽略 |
| **跨类目发布时分类是否合理** | 前田敦子生日放到体育类≠大野智初SNS放科技类 | 同样是跨类目，「体育生日」比「科技偶像新闻」更贴近类目用户 |

**工作流铁律：**
1. 发现0阅读/低阅读素材时，先检查发布时间（>48h才做结论）
2. 检查该人物历史发布数据——如果同人物其他素材有过不错数据（阅读300+），不说「这人物不行」
3. 检查发布分类——跨类目是强警告信号，但对强IP人物要加「需观察24-48h」的定性标注
4. 发现反例后，修改对应模式（不是添加「但XX是例外」的例外条款，而是**修改模式的边界条件本身**）
5. 每轮第4层完成后，问自己一句：这些结论如果有反例会是什么？如果有，已经处理了吗？

---

## 存档

完成四层review后，write_file 到 `~/.hermes/daily-reviews/YYYY-MM-DD.md`，包含完整四层内容。

### 存档格式规范

**⚠️ 存档必须含「全量素材一览」节（8/20教训）：** 审计 Step 2 用 grep 匹配 `全量素材一览|全量扫描` 验证第1层完整性。汇总存档时不要把第1层压缩成只有「聚类分析+分级结果」——必须在开头保留按来源分组的全量列表节，节标题含「全量素材一览」（如 `## 一、全量素材一览（按来源分组，N条）`）。8/20 首版存档只有「聚类分析」开头 → Step 2 第1层 FAIL → 补写全量列表节 + 重排编号后 PASS。一次性写对，避免补写。

**重要：** 存档通过 `sendRichMessage` 发送，表格只有在 Markdown 格式正确时才会被 Telegram 渲染为可视表格。

#### 📋 表格格式铁律（违反必退化为纯文本）

每条写存档时逐条自查，缺一条就得重写该表格：

1. ✅ **表格前必须用 `##` 标题（heading）**，不能用 `**粗体**`（6/26纠正：`**发布方式分析**` 后接表格→纯文本）
2. ✅ **`|` 必须在行首**，无前导空格
3. ✅ **必须包含分隔行**（`|---|---|`）——没有分隔行的 `|` 格式一定不被识别为表格
4. ✅ **列数必须一致**——表头行、分隔行、所有数据行的 `|` 数量相等
5. ✅ **表格前后各一个空行**
6. ✅ **不能把表头行和分隔行写在同一行**（如 `S级 | 标题 | |----|------|` 是无效的）
7. ✅ **每个表格内容建议不超过 25 行** —— 避免在分段时表格跨段（ segment-send.py 有边界保护，但最优解是不要让它触发）
8. ✅ **第4层反馈闭环表必须出现字面「阅读」字样（9/12教训）** —— 审计 Step 2 用 `grep -q "阅读"` 验证「实际数据」子项。若列头只写「实际数据」而数值是裸数字（如 `3699→39311→97990@156.4h`），grep 不到「阅读」→ 子项报「❌ 实际数据 缺失」（不影响总判定 PASS，但制造噪音、容易被误读为审计失败）。写法：列头写「实际数据（阅读/互动）」，或数值旁标「阅读」。9. ✅ **表格单元格内不要出现 `|` 字符（即使转义为 `\|`，9/23新增）** —— 想把 `grep -c '^| 2026-'` 这类含竖线的命令写进单元格时，哪怕写成 `\|` 也会让该行被拆成多列、与表头列数不一致 → 整表退化。**改写为纯文字**（如「趋势数据行数（grep 统计以 `2026-` 开头的表行）」），不要靠转义硬塞。9/23 首版存档因此列数 5 vs 4 错位，被校验器抓出。
10. ✅ **表格前不能是 `**粗体行**`（规则1的同源复发点，9/23新增）** —— 「四道校验（主进程独立复核的实测值）：」这类粗体引导句后直接接表格，同样退化为纯文本。必须改成 `####`/`###` 标题（规则1写的是「用 ## 标题」，实际只要**是 # 标题**即可（`####` 也通过渲染）。
11. ✅ **标题必须「紧邻」表格——中间不能夹任何正文行（9/28新增，validate-tables.py 实测口径）** —— 9/28 首跑被规则1抓出2处粗体引导句，改成 `###`/`####` 标题后复跑**仍然 problems: 1**：因为该标题与表格之间还夹着一条正文（curl 检索口径那段）。校验器判定的是**表格上方的紧邻行**是否为 `#` 标题，夹一行普通段落即判违规。修法：把说明性段落挪到标题**之前**，或另起一个小标题让 `#` 行直接压住表格行（9/28 最终写法：先 `### 检索口径` + 段落，再 `### 关联检索结果` 紧接表格 → `tables: 6 | problems: 0`）。**写完存档跑一次 validate-tables.py 是硬动作，别靠肉眼看「我前面有标题啊」。**

**✅ 写完存档后必须跑一次性校验（9/23新增）：**

```bash
python3 ~/.hermes/skills/creative/xhs-daily-material-review/scripts/validate-tables.py \
  ~/.hermes/daily-reviews/$(TZ=Asia/Tokyo date '+%Y-%m-%d').md
```

脚本逐表校验上述全部规则（表头/分隔行/数据行列数一致、前后空行、表格前必须是标题而非粗体、单表>25行警告），输出 `problems: 0` 才算通过。纯 shell 可跑（不依赖 `execute_code`），适合 cron。9/23 首版存档被它一次抓出 2 处真实违规（粗体引导句 + 转义竖线列数错位），修完复跑得 `tables: 9 | problems: 0`，随后 6 段投递全部渲染正常。

**✅ 写存档前 30 秒自查「最高频复发 TOP3」（10/5 归纳——这三条几乎每天必中至少一条，写对一次省一轮返工）：**

1. **表格正上方紧邻一行必须是 `#` 标题**，且标题与表格之间**不能夹任何正文行**。若该小节既有说明文字又有表格：把说明文字上移到标题**之前**（10/5 首跑即因「`### 每条 published 字段三查` → 说明段 → 表格」夹行被判违规；修法=说明段上移，标题紧贴表格）。
2. **单元格里绝不出现 `|`**，包括写进单元格的 grep 模式（`'^| 2026-'`）——校验器会按竖线拆列 → 「列数不一致（4 vs 3）」。改写为纯文字（如「以 2026- 开头的表行」），**不要靠 `\|` 转义硬塞**。
3. **粗体引导句不能直压表格**（`**新增模式：** #362…` 后面直接跟表 → 违规）。修法：把粗体行上移到 `###` 标题**之前**，或改成 `####` 标题自己压住表格。

（10/5 首跑即被这三条中的两条命中：2 处粗体/夹行 + 1 处竖线列错位；按上述修法改完复跑 `tables: 18 | problems: 0`。这三条本质是规则 1/9/10/11 的合并速查版，写前扫一眼即可避免。）

#### 存档中的表格场景及标准写法

**分级结果：**
```markdown
### S级（强烈推荐）🔥

| ID | 标题 | 理由 |
|----|------|------|
| 2692 | 佐久间大介借宫馆 | 团爱+趣味 |
```

**发布方式分析：**
```markdown
## 发布方式分析

| 模式 | 条数 | 平均阅读 | 平均互动 |
|------|------|----------|----------|
| rewritten | 14 | 851 | 21.6 |
```

**关联类型判断：**
```markdown
| 关联类型 | 含义 | 处置 |
|---------|------|------|
| 纯重复 | 同一新闻 | 跳过 |
```

#### 什么时候用列表代替表格

对于列数少（≤3列）且每行数据量大的情况，Markdown 列表比表格更安全且更易读：
```markdown
### S级（强烈推荐）🔥

- **#2692** 佐久间大介借宫馆 — 团爱+趣味，今日最佳传播素材
- **#2691** Snow Man佐久间 — 少女漫画反差，标题分5.0
```

详见 `references/markdown-table-format.md`。

#### 聚类参考
详见 `references/6-30-cluster-tag-patterns.md`（tags+title 混合聚类技术）。

#### 高日量处理
单日>50条时用 `references/high-volume-day-processing.md` 加速流程（先按fetch_by分组过滤，再聚类+深读）。

### 存档后的发送（Step 5）

存档写完后立即调用 `segment-send.py` 发送到 Telegram：

```bash
python3 ~/.hermes/skills/creative/xhs-daily-material-review/scripts/segment-send.py \
  ~/.hermes/daily-reviews/$(TZ=Asia/Tokyo date '+%Y-%m-%d').md
```

**触发时机：** 只在这一步调用。不要在写稿、入库、或其他流程里调用此脚本。

**参考文档：** `references/segment-send-workflow.md` 有完整参数说明和实现原理。

**⚠️ 常见超时：** segment-send.py 因代理端口配置问题或2-stage直连→代理超时可能失败。失败时先用 `curl -x socks5h://127.0.0.1:<port>` 验证代理连通性，然后改用手动内联 Python 发送。详见 `references/segment-send-timeout-fallback.md`。

**✅ 首选 fallback（8/5验证通过）：** segment-send.py 失败时直接跑 `scripts/segment-send-curl-fallback.py <存档路径>` —— curl 原生走 socks5h 代理 + sendRichMessage（`rich_message.markdown`），**保留表格渲染**。不要再重试 Python urllib 路径（socks 包未安装，代理 fallback 必然失败）。发送前先 `echo $http_proxy` 确认当前端口（漂移史：7/20:20809、7/28:20808、8/5:20808、8/7:20808、8/10:20808、8/11:20808、8/18:20808、9/2:20808、9/10:20808、9/11:20808、9/12:20808、**10/2:20809**（10/2 实测：20809 返回 302、20808=000 已死；脚本读 $http_proxy 自动取端口，无需改代码）），并用 getMe 验证连通性。分段+表格边界保护逻辑与 segment-send.py 一致。8/5 用此脚本 3 段全部 ok:true 送达；8/7 再次验证 3 段全部 ok:true；8/18 再次验证 5 段全部 ok:true（含表格前内容边界段）；9/2 再次验证 6 段全部 ok:true（59条素材存档，含表格前内容边界段）；9/10 再次验证 5 段全部 ok:true（53条素材存档，segment-send.py 120s 超时后切 fallback 一次成功）；9/11 再次验证 7 段全部 OK（60条素材存档，segment-send.py 150s 超时后切 curl fallback 一次成功）；9/12 再次验证 4 段全部 OK（53条素材存档，segment-send.py 报 `urlopen error [Errno 61] Connection refused` 后切 curl fallback 一次成功）。9/15 再次验证 9 段全部 OK（66条素材存档，segment-send.py 200s 无输出超时【非 Connection refused，是静默挂死】→ curl 测代理得 302 → 切 curl fallback 一次成功；再次印证「urllib+socks 路径失败，与端口无关」）。9/16 再次验证 8 段全部 OK（57条素材存档，segment-send.py 200s 静默挂死超时 → curl 测代理得 302 → 切 curl fallback 一次成功；连续两日均为「静默挂死 + 302 代理正常」组合，**建议直接把 curl fallback 作为首选发送路径**，segment-send.py 可跳过以省 200s 等待）。

**✅ 9/18 已按此执行并验证（6 段全部 OK，59条素材存档）——「首选 curl fallback」由建议升级为默认路径。** 本次**完全跳过 segment-send.py**（零等待、不试探 urllib 路径），流程为：
```bash
echo -n "proxy: "; curl -s -o /dev/null -w "%{http_code}\n" -x socks5h://127.0.0.1:20808 https://api.telegram.org/ --max-time 15
python3 ~/.hermes/skills/creative/xhs-daily-material-review/scripts/segment-send-curl-fallback.py ~/.hermes/daily-reviews/$(TZ=Asia/Tokyo date '+%Y-%m-%d').md
```
一次成功、零等待。判定链仍是「curl 测代理得 302 → 直接发」，302 即代理健康信号。

**⚠️ `Errno 61 Connection refused` ≠ 代理挂了（9/12新增判据）：** 9/12 segment-send.py 全部段报 Connection refused，但 `curl -s -o /dev/null -w "%{http_code}" -x socks5h://127.0.0.1:20808 https://api.telegram.org/` 返回 **302**（代理完全正常）。根因仍是 Python urllib+socks 路径失败，与端口无关。**判据：** 收到 Connection refused 时，先 curl 测一次代理连通性——通则**直接切 curl fallback**，不要改端口、不要重试 urllib、不要改 config。`lsof` 可能看不到 20808 监听（权限/实现原因），**不要以 lsof 无输出为判据**，以 curl 实测为准。

**⚠️ macOS 发送环境细节（8/7新增）：**
1. **macOS 没有 `timeout` 命令**（GNU coreutils 才有）——不要写 `timeout 90 python3 segment-send.py ...`，会报 `timeout: command not found`。直接用 terminal 工具自带 timeout 参数即可。
2. **不要手搓 getMe 验证**：macOS BSD grep 不支持 `-P`，`grep -oP 'bot_token:\s*\K\S+'` 会静默失败/截断（8/7实测只提取出10字符token，getMe 返回404）。正确做法是直接跑 `segment-send-curl-fallback.py`（脚本内部用 Python re 正确读取 config.yaml 的 bot_token），或从 config.yaml 用 `sed -n` 查看行号后按上下文确认。若确需手搓，用 `grep -o 'bot_token:[^,}]*' | cut -d: -f2 | tr -d ' "'`（不依赖 -P）。

**⚠️ 终极 Fallback（7/22经验）：** 如果手动内联 Python 也失败（SSL: UNEXPECTED_EOF_WHILE_READING 等），说明代理出口无法路由 Telegram HTTPS。此时存档内容作为 cron 最终响应直接输出——Hermes 的系统级 deliver 机制会将 final response 投递到用户。存档仍正常写入 `~/.hermes/daily-reviews/`。详见 `references/segment-send-timeout-fallback.md`「第三层 Fallback」。

---

## CRON 执行模式

### cron prompt 结构

cron prompt 使用 delegate_task 分发各层，子 agent 加载对应 layer skill。主进程做汇总→存档→审计→输出。

```
加载 xhs-daily-material-review skill
  → delegate_task layer1（全量扫描+聚类+分级）
  → delegate_task layer23（价值建议+跨时间关联）
  → delegate_task layer4（发布数据回顾+模式更新）
  → 汇总 → write_file 存档
  → 加载 xhs-review-self-audit → 跑四步审计
  → 全部PASS → segment-send.py 输出（用 sendRichMessage + rich_message.markdown 发送，详见 references/telegram-rich-message-api.md）
```

**⚠️ cron prompt 里的 segment-send 调用使用绝对路径：**
```
~/.hermes/skills/creative/xhs-daily-material-review/scripts/segment-send.py
```
（不要用 `~/file` 缩写，用完整展开路径；不要写 `bash scripts/segment-send.sh`——那个 .sh 只是代理器，最终调用的是 segment-send.py）

### 重要约束

- 各层子 agent 的 toolsets = `["terminal", "file", "web"]`（terminal 跑 curl/sqlite, file 读文件, web 搜 session）
- 子 agent 只加载该层的 layer skill，不加载整套 review skill
- 子 agent **禁止调用 delegate_task、write_file 到 DB、update.sh**
- **⚠️ 并行子 agent 共享同一个 `/tmp` 命名空间（10/4 实测）：** L23+L4 以 batch 并行分发时，两者都在 `/tmp` 落中间脚本/数据；用**无前缀的通用文件名**（尤其层内固定名，如 L4 的 `/tmp/update_patterns.py`）可能被 sibling 覆盖或触发写前告警（10/4 L4 子 agent 报告 `/tmp/update_patterns.py 曾被 sibling 子 agent 触碰`，虽最终内容完整、四道校验全绿，但存在真实覆写风险）。**对策：delegate context 里要求每层用层前缀命名临时文件**（`/tmp/l1_*` / `/tmp/l23_*` / `/tmp/l4_*`），或落层专属子目录（`/tmp/l4_work/`）；层内固定名一律加前缀（`/tmp/l4_update_patterns.py` 而非 `/tmp/update_patterns.py`）。写完关键文件后（尤其 data-feedback-patterns.md 的插入脚本）先核对内容再执行，别假设刚写的文件还是自己的。

### ⚠️ 并行模式下 Layer 1 vs Layer 23 作用域不对称（7/20新增）

**问题现象：** Layer 1 子 agent 可能按「目标赛道」（坂道系+AKB+娱乐女艺人, 42条）做全量扫描和分级，而 Layer 23 使用**全量51条**（含経済/グラビア/セクシー女優），导致两边S/A列表看起来「不一致」。

**这不是分类对齐错误**（即并非两层的阈值不同），而是**作用域不对称**——两个子 agent 分析的素材集合不同。

**影响：** 主进程汇总时看到「Layer1有3条S级（全AKB）」和「Layer23有7条建议S级（含大桥和也、黑牢城等）」时，容易困惑哪个是对的。实际上两个都对——只是作用域不同。

**应对措施：**
1. **Layer 1 的 goal 中明确注明审核范围和排除规则**（如「经济分类素材排除」「グラビア排除」），在报告开头标注「目标赛道=N条，非目标=K条」
2. **Layer 23 的 goal 中指定是否包含非目标赛道**，与 Layer 1 对齐或明确说明差异
3. **主进程汇总时标注两条线的差异原因**——在存档中写「Layer1使用目标赛道42条，Layer23使用全量51条（含非目标）」让用户/编辑理解差异
4. 如果两层用了不同数据集，**不要在存档中合并为一份S/A表格**——分别列出或标注来源

详见 `references/7-20-parallel-scope-asymmetry.md`。

### ⚠️ Cron 模式下 `execute_code` / `from hermes_tools` 被 BLOCK

在 cron 模式（无用户审批环境）下，主进程和子 agent 的 `execute_code`（`from hermes_tools import terminal, ...`）会被安全系统直接 BLOCK。所有需要 Python 处理的操作必须使用内联 Python（`curl | python3 -c "..."` 或 `python3 -c "..."`）在 `terminal()` 中完成。

**影响范围：**
- **主进程汇总时**不能使用 `execute_code` 读取子 agent 的 workspace 文件或做批量数据处理
- **审计步骤**（xhs-review-self-audit）已适配——所有审计命令已改为 terminal 中的 shell 脚本
- **Layer2-3 分类对齐验证**（主进程验证 layer23 报告日期）必须用 `read_file` 而非 `execute_code`

**规避办法：**
- 用 `read_file` 代替 `execute_code` 的文件读取
- 用 `terminal("curl | python3 -c ...")` 代替 Python 批处理
- 永远不要依赖 `execute_code` 作为 cron 流程中的关键路径
- audit 脚本中的所有 Python 操作必须同步适配（`from hermes_tools` 在 cron 下不可用）

### ⚠️ Subagent 可能获取 ts=0 的已知问题

在并行 delegate_task 分发中，子 agent 调用 API 获取今日素材时，`title_score` (`ts`) 字段可能全部返回 0（而非实际值）。这是 API 端的问题（可能与并发请求时的缓存/计算缺失有关）。

**影响：** 子 agent 无法依赖 `ts>=5.0` 阈值做分级，会自行改用 `cj_len + 事件性 + 故事性` 标准，导致与 layer1 的 S/A 分级出现系统性差异。

**应对措施：**
1. 主进程汇总时**优先信任 layer1 的分级结果**（layer1 是第一个拿到数据的，通常 ts 正常）
2. 如果 layer23 报告标注了「ts字段均为0」，可以推断其 S/A 阈值偏宽松（倾向升格素材为 S），引用价值建议时注明「基于自行分级」
4. 子 agent 报告中如出现「ts字段均为0」，主进程不必再自行 curl 验证——确认其使用 cj_len+事件性 降级标准即可
5. 7/19 案例：layer23 ts=0 时自定 S 级 7 条（vs layer1 的 8 条），分类不完全一致但在合理范围内
6. **⚠️ 子 agent 报告头部计数可能与实际列表不符（8/7案例）：** layer1 报告标题写「A级（16条」但正文列表实为17条（汇总表也写17）。主进程汇总时必须**数正文列表条目数**为准，不能照抄报告标题里的计数——存档里用实际列表数，避免层级间数字对不上。

### ⚠️ 子 agent 可能无法 skill_view 加载 layer skill（8/4新增）

**问题现象：** 8/4 第1层子 agent 报告「skill_view 工具在本子代理环境不可用且 skills 目录中无 layer1 skill 文件」，但仍按 goal/context 中内联的步骤指令完成了全量扫描+聚类+分级（43条key全覆盖校验通过）。同日 layer23/layer4 子 agent 同样未明确加载 layer skill，全部正常产出。

**机制/教训：** delegate_task 子 agent 环境不一定能访问本 profile 的 skills 目录或 skill_view——「子 agent 加载对应 layer skill」是期望而非保证。

**应对措施（铁律）：**
1. 主进程 delegate 时必须把该层核心步骤内联进 goal/context（拉取命令、date_from 铁则、零素材扩展逻辑、分级阈值、禁止操作清单、输出格式），不能只写「加载 skill X 执行」——子 agent 可能加载不到
2. goal 里保留 skill_view 调用作为第一选择（能加载就加载，加载不到不阻塞，按内联步骤执行）
3. 验收不依赖「子 agent 声称加载了 skill」——用输出完整性校验（8/4 做法：43条key全覆盖比对）
4. **补充 fallback（9/12 实测）：** 子 agent 无 `skill_view` 时，可用 `read_file` 直接读取该 layer skill 的 SKILL.md 路径（layer23 = `~/.hermes/skills/xhs-daily-material-review-layer23/SKILL.md`；layer1 = `~/.hermes/skills/creative/xhs-daily-material-review-layer1/SKILL.md`；layer4 同 layer23 无 category 子目录）。9/12 layer23/layer4 均以此成功取得 skill 全文。
  - **⚠️ 路径前缀不对称（9/27 踩坑）：** layer1 在 `creative/` 子目录下，**layer23 与 layer4 不在**。给 layer4 传脚本/文件路径时**不要套 `creative/`** —— 正确路径是 `~/.hermes/skills/xhs-daily-material-review-layer4/scripts/layer4_aggregate.py`。9/27 主进程在 delegate context 里写成 `~/.hermes/skills/creative/xhs-daily-material-review-layer4/scripts/layer4_aggregate.py`，L4 子 agent 因此报「脚本不存在」并自建等价脚本（聚合结果仍正确，但白费一轮 tool call）。凡在 cron prompt / delegate context 里写 layer skill 或其 scripts 绝对路径，写完先自查有没有多余的一层目录。可在 delegate context 里附上该路径作为第二选择——但仍以**内联步骤为第一保证**，不要把「能 read_file 到 skill」当作前提。

### 层间数据传递模式（6/26经验）

主进程通过 delegate_task 的 summary 获取各层子 agent 的分析结果。子 agent 可能额外将完整报告写入 workspace 文件，但**这不是可靠的数据传递方式**——主进程不应依赖读取这些文件来获取结果。

**标准做法：**
- 主进程从 delegate_task 返回的 summary 中提取分析结果
- summary 天然包含完整分析（全量列表、聚类、分级、价值建议、关联分析等）
- 不需要单独去 workspace 查找中间报告文件
- 主进程汇总时直接在最终存档中压缩/引用 summary 中的关键点即可
- **8/10补充（补充通道，非主通道）：** layer1 子 agent 会把完整报告写到工作区文件 `review_l1_report_YYYY-MM-DD.md` + `review_l1_raw.json`（含全部精确 key / cj_len / 聚类明细）。当 summary 缺少精确 key 或聚类细节时，主进程可 `search_files(target='files', pattern='review_l1_report_*')` 找到**当日**文件 read_file 补全（8/10 验证：读取后获得全部精确 key，汇总更准）。只读当日文件，不要读历史日期文件（与 7/5 layer23 旧文件陷阱同源的镜像问题）。
- **9/27 升级：该 workspace 报告文件应视为「取全 key 的默认路径」，不是「补充通道」。** 即使 goal/context 里硬性写明「输出完整 40 字符 key」，L1 summary 仍会把 key 截断成 `1f0f8b4a…f3220` 形式（8/10 / 9/23 / 9/27 三度复现，说明这是 summary 输出的固有行为，靠指令压不住）。9/27 实测 `review_l1_report_2026-09-27.md` 22.4 KB，内含 55/55 完整 key + 按来源全量一览 + 聚类 + 分级表，主进程一次 `read_file` 即拿齐，比重新 curl 全量 JSON 做 key 映射省一次 tool call。**推荐固定动作：L1 delegate 一返回，主进程立刻 `read_file` 当日报告文件，再据其构造存档与 L23 context。**

**⚠️ 报告文件路径以 L1 summary 里报出的为准（10/2 实操）：** L1 子 agent 可能把报告写到 `/tmp/review_l1_report_YYYY-MM-DD.md` 而非 workspace（10/2 即写到 /tmp，主进程 read_file 该绝对路径一次拿齐 55/55 完整 key）。**不要硬编码 workspace 路径去 search_files**——先看 L1 summary 里报出的路径；找不到时再 `search_files(target='files', pattern='review_l1_report_*')` 兜底。两种落点都正常，别因工作区没有而误以为报告丢失。

### ⚠️ Layer 2-3 分类对齐问题（7/4修正 + 7/5补充）

**问题现象（类型A — 重新分类）：** layer23 子 agent 可能不信任 context 中传递的第1层分级结果，自行调用 `/api/news?date_from=...` 获取全量素材并重新分类，使用与 layer1 不同的 S/A 判定标准（如 T>=5.0+cj_len>=300=S），导致分类膨胀（18S vs 3S），AKB大TOP/B级素材被错误升格为S级。

**问题现象（类型B — 错误文件引用·7/5新增）：** layer23 子 agent 通过 `search_files(target='files', pattern='material_review_layer1_*')` 在 workspace 中搜索早期层输出文件，找到**前一天的旧文件**（如6/26的存档），将其当作今日素材进行第2~3层分析。结果所有素材都对应旧日期，完全没有触及今日实际素材。这是与类型A不同的独立故障模式——类型A是「不信任context自己去拉数据」，类型B是「用旧文件替换context数据」。

**修正措施：**
1. 主进程 delegate_task 时必须将第1层的显式分级结果（S级/A级列表，含key和标题）放入 context 字段
2. layer23 子 agent 的 goal 中明确写入：「基于以下第1层提供的S/A素材列表进行分析，不再重新分类所有素材」**且「不要通过 search_files 在 workspace 中查找旧报告文件——context 中的 data 就是你需要的全部输入」**
3. layer23 子 agent 仍可调用 API 拉今日素材以获取 content_ja 全文，但不应自行重定 S/A 阈值
4. 如果 layer23 发现某条素材确实值得关注但 layer1 未评级为 S/A，可以在分析中提到「补充建议：此素材也值得注意」，不改变原有分级
5. **主进程在汇总时必须验证 layer23 分析结果的日期是否正确**——检查第2~3层报告是否引用了今天日期的素材标题。如果发现 layer23 用了旧数据，主进程必须自行补做第2~3层分析（用已获取的第1层全量数据和API search），不能直接使用错误的子agent结果

### 时区

cron 直接用 `0 10 * * *`（CST 10:00）。Hermes scheduler 解析 cron 表达式使用**系统本地时区（CST/UTC+8）**，不是 UTC。`0 10 * * *` 就是 CST 每天上午10:00。不要改成 `0 2 * * *`——那是错误的 UTC 修正，会导致下一次触发变成第二天。

验证方法：创建 cron 后检查 `next_run_at` 字段的时区偏差。如果设了 `0 10 * * *` 后显示 `next_run_at: 2026-06-27T10:00:00+08:00`，说明正确——就是 CST 10:00。

### CRON 健康检查（确认定时任务实际执行了）

**⚠️ 调度器整体停 tick 的检测（8/15实测）**：除检查每日 review job 的 `last_run_at` 外，还要看**所有 job 的 `next_run_at`**——如果多个不同频率的 job（每5分钟/每30分钟/每10分钟 health check）的 next_run_at 全部停在同一天同一时刻附近（8/15：全部停在 8/14 18:43），说明 Hermes 调度器整体停止 tick，不是单个 job 失败。此时当日 review 未跑不是「没素材」而是「调度器挂了」。恢复路径：主进程补跑（写稿阶段补 L1/L3 → 补存档时补 L2/L4 → 审计 → segment-send），与 8/13 模式一致。8/15 完整验证：写稿先行 53 篇 → 补存档 2026-08-15.md（L2价值建议+L4发布数据回顾）→ 四步审计全 PASS → 模式文件 #149/#150/#151 → segment-send 失败（代理端口 20808/20809 无监听）→ 终极 fallback 直接输出。

**问题现象（7/15验证）：** 每日review cron在agent.log中显示 `Running job` 且API调用正常，但**无output文件、无delivery、`last_run_at`未更新**。任务在01:38:03创建了OpenAI client后静默消失，没有完成/交付记录。

**诊断流程：**
1. `cronjob list` — 检查 `last_run_at` 是否更新到当天。如果显示前一天，则任务未正常完成。
2. `ls ~/.hermes/cron/output/<job_id>/` — 检查是否有当天的output文件。没有=任务没有存盘。
3. `grep "<job_id>" ~/.hermes/logs/agent.log` — 检查agent.log最后一条相关记录。如果结束于创建OpenAI client而非 `deliver`/`result`，说明任务中途丢失。
4. `cronjob action=run job_id=<id>` — 手动重跑。

**⚠️ 排查沟通纪律（7/15用户纠正）：** 向用户汇报排查结果时，严格遵循「先数据，后结论」的顺序。每一步结论必须附带对应的原始日志行或命令行输出作为证据。禁止使用「可能、也许、大概是、看起来像」等推测性语言——如果没找到证据，就说没找到；如果找到了具体日志行，直接贴出来让用户自己看。用户要求的是数据，不是分析。

**手动重跑后确认：** 观察 `last_run_at` 是否更新 + output目录是否有新文件 + 用户是否收到deliver。

### 测试新 cron 架构的方法

**铁律：测试 job 的 prompt 必须模拟真实 cron 的完整流程，不能跳过任何步骤（如 segment-send）。** 6/26教训：测试job写了"本次测试不调用segment-send.sh"，结果测试完成后用户没收到输出，测试结论无效。

详见 `references/cron-testing-discipline.md`。

**不要直接修改主 cron prompt 来测试。** 创建一个独立的 one-shot 测试 job：

```bash
# 创建 one-shot 测试，设在后1分钟触发
cronjob create \
  name="XXX测试" \
  prompt="..." \
  skills=["xhs-daily-material-review"] \
  schedule="2026-06-26T10:22:00"
```

注意：`now+1m`、`now+5s` 等语法不被支持，必须用 ISO 时间戳。

**验证步骤：**
1. 创建测试 job → 确认 `next_run_at` 正确
2. **清理旧状态** — 删除同日期存档 `rm ~/.hermes/daily-reviews/YYYY-MM-DD.md`
3. 等待触发 → 检查 agent.log 有 `Running job 'XXX测试'`
4. 观察 180 秒内有无 `stale_stream_kill` 警告
5. 检查 `last_status` 是否为 `ok`
6. 确认用户收到了分段输出
7. 清理测试 job

- 注意：测试 job 和主 cron 同时跑时，输出可能混淆——因为两者都 deliver=origin，都会发到同一个对话

### 6/26死锁根因 + 6/26端到端验证通过

详见 `references/delegate-task-cron-verified-2026-06-26.md`。

### 7/04 三层并行验证通过

详见 `references/2026-07-04-parallel-delegation-success.md`——首次3层并行delegate_task成功执行的完整记录，包含分类不一致问题、执行数据、tool call消耗分析。

**症状：** cron 10:00触发了（agent.log有`Running job`），但无输出给用户，agent 卡死50分钟。

**根因链：**
1. 6/25创建cron prompt时，让主进程用 `delegate_task` 分发各层给子 agent
2. 子 agent 需要加载 `xhs-daily-material-review-layer1` / `layer23` / `layer4` 这些 layer skill
3. **但这些 layer skill 根本不存在！** 只是 skill 正文里提到了名字，没人创建过
4. 子 agent 加载不到 skill → 卡在 `skill_view` → 无后续指令 → DeepSeek 流180秒无响应
5. `stale_stream_kill` 杀连接 → 重试 → 又180秒 → 无限循环

**6/26端到端验证：** 创建了 layer1/layer23/layer4 三个真实 skill 后，用 delegate_task 分发的 cron 模式**成功执行**：
- Layer 1: 176秒完成（全量扫描80条+聚类8组+分级）
- Layer 4: 43秒完成（38条>48h数据分析+模式更新）
- Layer 23: 94秒完成（11条S/A素材价值建议+跨时间关联）
- 汇总→存档→审计（4步全PASS）→segment-send.py 分段输出
- 3段内容全部按3800字符正确分割
- data-feedback-patterns.md 成功追加模式64-68+趋势表更新

**结论：delegate_task 分发模式在 layer skill 存在时工作正常。**

### 7/08 并行执行验证（see `references/2026-07-08-parallel-delegation-metrics.md`）

**概况：** 56条素材，3层并行 delegate_task，总耗时约162s（比串行估算384s省58%）。三层全部完成，subagent 无超时。

**关键发现——分类膨胀持续存在：** layer23 因 context 中无 layer1 的分级结果，自行拉取全量数据后产生了 **10条S级**（vs layer1的5条S级），且 layer23 倾向于将商务合作/经济新闻升格为S。具体比对详见参考文件。

**应对：**
1. layer23 skill 已新增「并行模式下的分类对齐陷阱」指南（7/8补丁）
2. 主进程汇总时仍应优先信任 layer1 的分级，layer23 的价值建议按「建议」而非「判定」引用

### ⚠️ Layer 4 子 agent 超时风险与主进程 Fallback（7/6经验）

**问题现象：** 第4层子 agent 在 600s 超时后仍未完成（7/6的layer4用时600s timeout，31次API调用后退出）。

**根因分析：** 第4层涉及50条published数据拉取+时间分层+反馈闭环分析+data-feedback-patterns.md更新。每条>48h数据需输出反馈闭环（当初判断→实际数据→结论），消耗大量tool call配额。deepseek-chat处理大块返回数据时token消耗大，流式输出慢，容易在总结阶段撞时间或配额限制。

**标准应对措施：**
1. **主进程必须准备 Layer 4 Fallback 方案** — delegate_task分发的layer4超时时，主进程自行补做
2. **补做路径：** 直接curl拉published数据 → Python做时间分层 → 输出反馈闭环 → 读取并append data-feedback-patterns.md → 更新趋势表
3. **验证点：** 补做第4层后，主进程仍需跑完整审计（Step 3: data-feedback-patterns.md更新审计），不能跳过
4. **效率替代方案（考虑中）：** 第4层delegate_task仅做「查询+分析」，data-feedback-patterns.md更新改由主进程统一处理——省去子agent两次read_file+多次write_file调用

**验证：** 7/6 cron中layer4超时后主进程用约15秒完成补做，审计全部PASS，segment-send成功输出3段。

**铁律：**
- cron prompt 引用 layer skill 之前，必须先确认该 skill 真实存在（`skill_view` 成功返回）
- 新创建的 layer skill 必须经过测试 job 验证后再挂到 cron prompt
- 主 cron 的 `enabled_toolsets` 不要设——让子 agent 自己去决定需要的工具
- 主进程直做模式和 delegate_task 分发模式都是可行的选择——选择依赖用户偏好和 API 负载

### HTTP_PROXY 环境变量导致 DeepSeek stale stream

**重要诊断发现：** 本环境设置了全局 `HTTP_PROXY=socks5h://127.0.0.1:10090`，所有子进程（包括 cron job agent 的 DeepSeek API 调用）都走 SOCKS5 代理。DeepSeek 是**国内服务不需要代理**，走代理导致 API 连接不稳定 → `Stream stale for 180s` → `.tick.lock` 锁文件残留 → 后续调度全部跳过。

详见 `references/2026-06-27-proxy-stale-stream-root-cause.md`。

## ⚠️ 抓取(ingest)失败日 vs 真·零素材日（10/7 新增）

当日 `date_from=<today>` 返回 total=0 时，**先分清是「抓取失败」还是「真无新闻」**，再决定是否按 7/07 的 3 日扩展重审：

- **先诊断**：查 `/Users/user/PG/XiaohongshuSkills/logs/fetch_runner.log`（launchd `com.xhs.fetch-runner`，每日 00:30 触发）+ `data/logs/task_YYYY-MM-DD.log`。全关键词 `抓取出错: Connection timed out` + 结尾「❌ 所有关键词均未找到新闻」= 抓取全线失败。
- **抓取失败 + 最近几天素材已各自 review 过 → 不要按 3 日扩展重审**。原因：① 扩展=全部为已审旧素材、零新增价值；② **更危险——03:00 写稿 cron（`每日3点写稿`）会读本存档的 S/A 清单并「覆盖当日全部 S+A+AKB大TOP 素材」，入库时设 `preselected=1, publish_xhs=0`**，若本存档误列旧素材 S/A，会导致**重写旧稿 + 把已发布条 publish_xhs 回退为 0** → 重复发文/状态回退。正确交付：**「抓取故障状态报告 + 第 4 层发布数据回顾」，不含第 1~3 层、不含 S/A 清单**（写稿 cron 读不到 S/A 自然跳过）。
- **ingest 与 publish 是两条解耦链路**：素材断供 ≠ 发布停摆。故障报告须同时给出发布管道状态（MAX_PUB + 定时队列条数）作对照。
- **只有三者齐备才扩展**：当日 0 条 **且** 抓取只是延迟/未跑（无 timeout 失败证据）**且** 最近几天素材未被 review 过（存档缺失）。三者不齐不扩展。
- 诊断配方（Chrome CDP / `Page.loadEventFired` 判定失效）与决策细则详见 `references/2026-10-07-ingest-failure-vs-zero-news.md`。

## 注意事项

- **第3层查DB必须用 `search=` 参数（6/28修正）** — 不要用limit=200全量拉回Python过滤，会漏掉大量旧记录。直接用 `-G --data-urlencode "search=关键词"` 方式搜全部记录
- **第4层必须先查rewritten_title再分析** — 原始title和实际发布标题可能完全不同
- **当日无新素材处理（7/7经验）** — API返回0条时自动扩展日期范围至最近3天（再至7天fallback），存档首行标注数据范围。详见 `references/7-07-zero-entries-expansion.md`
- **执行纪律** — skill写了多少步就做多少步，不做80%跳20%
- **第3层和写稿是因果关系** — 写稿前必须先做第3层，否则会写出重复内容
- **cron 模式**：delegate_task 分发或主进程直做均可（已验证两种模式均可行）
- **存档表格格式（6/26教训）：**
  - 表格前必须用 `##` 标题而非 `**粗体**`，`|` 必须在行首，必须有分隔行
  - 大表格（>25行）尽量拆成小表或用列表代替，避免表格在分段时跨段
  - segment-send.py 有表格边界保护（表格行整体后移），但最优解是写存档时就不跨段
  - 详见 `references/telegram-rich-message-api.md`（sendRichMessage原理）和 `references/markdown-table-format.md`（标准写法）
- 网络连接：segment-send.py 已改为先直连后代理（7/12修正），详见 `references/segment-send-workflow.md`#网络
  - 如果表格在Telegram中仍显示纯文本，排查分段边界：`references/segment-send-table-boundary-pitfall.md`（6/26经验）
  - **代理端口漂移（7/28更新）：** 端口不定期漂移（7/20:20809、7/21:20809、**7/28:20808**）。每次发生 Connection Refused 时先 `echo $http_proxy` 确认当前端口。**更根本的根因：** segment-send.py 使用 Python urllib + socks5:// 协议，但 `socks` 包未安装，因此脚本的代理 fallback 总是失败——无论端口是否正确。推荐用手动 curl 发送（详见 `references/segment-send-timeout-fallback.md` 中的「curl 版」方案）。7/28 已验证 curl 手动发送成功。
- layer23 子 agent 错误引用旧 workspace 文件：详见 `references/7-05-layer23-stale-file-pitfall.md`（7/5教训）
- layer23 子 agent 可能错误断言 search 不查历史数据：详见 `references/7-12-layer23-false-search-claim.md`（7/12教训）——主进程应在汇总时自行用 curl 验证一次
- **L4/L23 子 agent 报告引用的是数字 id，不是 hex key（9/9发现）：** 子 agent 报告里的「6891」「6821」是记录的数字 id（对应 key f43ef26399fd、926b46c773c9），不是 key 前缀——主进程想拉取精确 rewritten_title 补全存档反馈闭环时，按 `str(r.get('id'))` 过滤 published 数据（`curl .../api/news?publish_xhs=published&limit=50` 返回含 id 字段），**不要**按 key startswith 匹配（会空返回，9/9实测）。同一批数据的 key 前缀（如 6813/6814/6819）与数字 id 恰好相近是巧合，别被误导。另外 published 接口的 DESC 排序并不可靠（9/9 实测首条是 9/5 pub 而非最新 9/6），客户端需按 xhs_pub_time 自行重排再分层。**⚠️ 9/11 实测：limit=50 页面甚至会把真实最新发布整条漏掉**——9/11 运行时页面内最新=6932(09-06 08:59)，但分页聚合核对发现真实最新=id 6940(09-06 **12:04**「台下空了一半，她唱完了最后一首」)不在该页内。因此「管道停摆起点」这类结论必须用分页聚合（拉 max limit 后自行 max(xhs_pub_time)）核对，不能只信 limit=50 页面。**9/12 实测：published 接口的 `page` 参数被忽略（传了没用），用 `offset` 分页有效——`offset=0/250/500` 可聚合全量 749 条；聚合后最新真实 pub 仍以 max(xhs_pub_time) 为准。** 详见 `references/9-09-l4-numeric-id-and-desc-sort.md`
- layer23 子 agent 可能把「定时发布队列」误报为「已发布」（8/18教训）：子 agent 声称「已发布」时先查 xhs_pub_time 是否未来时间，存档措辞统一用「已排定今日HH:MM定时发布」；结论可不受影响（跳过判定一致），但措辞错误会误导第4层口径。核实方法（关键词 search + 未来 pub_time 过滤）详见 `references/8-18-layer23-scheduled-vs-published-pitfall.md`
- 当日无新素材处理：详见 `references/7-07-zero-entries-expansion.md`（7/7经验）
- 并行模式下 Layer 1 与 Layer 23 分类不一致的具体比照案例：详见 `references/7-17-parallel-reconciliation-example.md`（7/17经验，含6件S级的逐条对照和3种差异类型分析）
- Subagent 拿到 ts=0 的已知现象：详见 `references/7-19-subagent-ts-zero-phenomenon.md`（7/19首次发现，含 layer1 vs layer23 实际数据比照）
- Layer 1 vs Layer 23 作用域不对称（7/20发现）：详见 `references/7-20-parallel-scope-asymmetry.md`——Layer 1 过滤了非目标赛道但 Layer 23 用的是全量数据
- ✅ 7/24 成功案例：Layer1和Layer23都用了全量52条，作用域完全对齐，S级数量均为7条。详见 `references/7-24-parallel-scope-alignment-success.md`
- ✅ 8/1 成功案例：采用「layer1先行 → layer23+layer4并行」顺序（先 delegate_task layer1 等完成，再 batch 并行分发 layer23+layer4），layer23 直接使用 context 传入的第1层 S/A 列表、未重新分级，S/A 完全对齐、无分类膨胀（S=3 与 layer1 一致）。**顺序执行是规避 7/8 分类膨胀与 7/20 作用域不对称的可靠模式**；layer4 与 layer23 互不依赖可同时并行，省一层等待时间。8/4 再次验证成功（43条，S=4/A=7，layer23 沿用 layer1 列表未重分级、S/A 完全对齐无膨胀）。8/5 第三次验证成功（56条，S=6/A=6，layer23 沿用 layer1 列表未重分级、S/A 完全对齐；layer1 先行时 ts 字段正常，无 7/19 ts=0 现象）。8/7 第四次验证成功（61条，S=6/A=17，layer23 沿用 layer1 列表未重分级、无膨胀；ts 正常无 None；layer4 子 agent 把 9 个重复趋势表节合并为唯一一张表、文件 1037→926 行，主进程抽查旧模式完整保留——详见 `references/8-07-layer4-trendtable-merge-verification.md`）。8/10 第五次验证成功（58条，S=5/A=6/AKB大TOP10，layer1先行→layer23+layer4并行；layer1 报告标题写「B级17条」但正文列表实为15条+注「含2条并入/低优补充」——再次印证 8/7「以正文列表计数为准」纪律；审计 Step3 发现日期节头「### 2026-08-10 …」被误判为最高模式编号 2026，已在 xhs-review-self-audit 修正）。8/11 第六次验证成功（59条，S=6/A=15，layer1先行→layer23+layer4并行；layer23 沿用 layer1 列表未重分级、S/A 完全对齐无膨胀；ts 正常无 None；layer4 新增模式 #137/#138/#139、趋势表仍唯一、文件977→989行；layer1 发现 ts=0.1 评分器异常条目（正源司阳子center）按事件型升级——见 layer1 skill 的 ts异常低值处理；audit 四步全PASS；segment-send.py 失败→curl fallback 4段全 ok:true）。
9/2 第七次验证成功（59条，S=5/A=9/AKB大TOP4，layer1先行→layer23+layer4并行；layer23 沿用 layer1 列表未重分级、S/A 完全对齐无膨胀；layer4 新增模式 #208/#209/#210、趋势表仍唯一、文件1134→1142行；审计 Step 4 首跑遇 grep 空值报错→稳健版整文件信号计数 9/10 PASS，见 xhs-review-self-audit Step 4；segment-send.py 失败→curl fallback 6段全 ok:true）。
9/18 第八次验证成功（59条，S=5/A=11/AKB大TOP18，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 59 条全量、S/A 完全对齐无膨胀，**作用域对齐**；layer4 新增模式 #272~#277、趋势表唯一（节数=1、表头=1）、文件1287→1297行；审计四步全 PASS（Step4 信号 7/12）；**首次默认走 curl fallback 作为唯一发送路径、完全跳过 segment-send.py**，代理测 302 → 6 段全 OK、零等待）。本次为「L1 先行 → L23+L4 并行 + curl fallback 默认发送」这一组合的稳定复现，可作为标准模板。
9/21 第九次验证成功（63条，S=8（12.7%）/AKB大TOP=14/A=18/B=13/C=10，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 63 条全量、S/A 完全对齐无膨胀，**作用域对齐**；layer4 新增模式 #290~#297、趋势表唯一（节数=1、表头=1、表体行为75）、文件1317→1329行；审计四步全 PASS（Step4 信号 7/12）；发送走 curl fallback 默认路径，代理测 302 → **6 段全 OK、零等待**）。⚠️ 本次新发现：**layer23 可能把「已排定 xhs_pub_time 但 publish_xhs 未归位」的记录当作最新已发布**——L23 报 MAX_PUB=09-20 17:37（织田裕二/室井 21c5a22d74ab），主进程用 published 集 offset 分页聚合核实该记录**不在 published 集内**（真实 MAX_PUB=09-20 11:35 id 7592）。判定结论不变但起点数字必须由主进程以 published 聚合为准。
9/23 第十次验证成功（52条，S=6（11.5%）/A=9/AKB大TOP=8/B=11/C=18，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 52 条全量、S/A 完全对齐无膨胀，**作用域对齐**；layer4 新增模式 #301~#306、趋势表唯一（节数=1、表头=1）、文件1337→1348行、diff 仅 `a` hunk；审计四步全 PASS（Step4 信号 6/12）；发送走 curl fallback 默认路径，代理测 302 → **6 段全 OK、零等待**）。⚠️ 本次复发同类误报：L23 报 MAX_PUB=09-22 18:24，主进程 published 聚合实测 MAX_PUB=**09-22 15:20（id 7867）**；补充核实手法——用该条 title 全文 search 与事件关键词（「TGC」）**双检索**均定位不到记录（命中的 45 条 TGC 相关条 publish_xhs 全为 0），可确证属「pub_time 已排定但 publish_xhs 未归位」误报，而非「主进程聚合漏条」。**凡 L23 报的 MAX_PUB 与 published 聚合不一致，一律以聚合为准，并可用「title search + 事件关键词双检索」做二次确证。**
9/23 另新增两项主进程动作，已固化为常规步骤：① 存档写完跑 `scripts/validate-tables.py` 校验表格渲染（见上方「表格格式铁律」规则9/10）；② delegate layer1 时在 goal/context 中硬性要求**输出完整 40 字符 key**（子 agent 默认截断为 12 字符前缀，主进程被迫多拉一次 JSON 做映射）。

9/24 第十一次验证成功（56条，S=6（10.7%）/A=15/AKB大TOP=11/B=12/C=23，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 56 条全量、S/A 完全对齐无膨胀，**作用域对齐**；layer4 新增模式 #307~#314（8条）、趋势表唯一（节数=1）、文件1348→1360行、diff 仅 `a` hunk（a-hunks=2/c=0/d=0）；validate-tables.py 报 `tables: 8 | problems: 0`；审计四步全 PASS（Step4 信号 7/12，占位文字 0）；发送走 curl fallback 默认路径，代理测 302 → **7 段全 OK、零等待**）。三项新增经验：① **本日 ts 全件正常**（无 None）但 ts=5.0 偏重 17 件、乃木坂系 story 的 cs 仅 0.0–1.1（疑 AI 再构成文本）→ 分级靠 cj_len＋事件性补正，不是 ts 异常日但需留意 cs 塌陷；② **MAX_PUB 三处一致**（L23／L4／主进程独立 offset 分页聚合均为 09-23 20:57 id=7976），前几日反复出现的「L23 误报 MAX_PUB」本次未复现——主进程独立聚合复核仍应保留为常规步骤，但一致时不需再做双检索确证；③ **审计 Step 3 的日期 grep 有 false-negative 陷阱已修**（趋势表日期列是完整格式 `| 2026-MM-DD |`，旧写法用短日期 `9/24` 只靠说明列文本偶然命中；改为优先 `^| $FULL ` 匹配、短日期兜底，并订正了 skill 中「趋势表用短日期」的错误说明）。本日还实测到 published 接口 DESC 排序确实不可靠：limit=3 的 DESC 首页直接跳过了 09-24 11:24 那条定时队列条——**分层与 MAX_PUB 必须靠 offset 分页聚合 + 客户端重排，不能信单页 DESC**。

9/25 第十二次验证成功（53条，S=7（13.2%）/A=10/AKB大TOP=8/B=18/C=10，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 53 条 S/A 列表、未重分级，**作用域对齐**；layer4 新增模式 #315~#320、趋势表唯一（节数=1）、文件1360→1371行、diff 仅 `a` hunk；validate-tables.py 报 `tables: 8 | problems: 0`（首跑被规则9「单元格内竖线」抓出1处，改写为纯文字后复跑通过）；审计四步全 PASS（Step4 信号 8/12，占位文字 0）；发送走 curl fallback 默认路径，代理测 302 → **6 段全 OK、零等待**）。三项新增：① **MAX_PUB 三处一致**（L23／L4／主进程独立 offset 聚合均为 09-24 17:55 id=7987）→ 「L23 误报」本日未复现，一致时无需双检索确证；② 本日 ts 全件有值无 None、无标题污染，但**多个 story 条 cs 偏低（0.5~2.6）** → 分级靠 cj_len＋事件性补正（cs 偏低似为 AI 再构成文本的常态，非评分异常）；③ **validate-tables.py 的价值再次兑现**：规则9（单元格内 `|`，哪怕转义）是最高频复发点，写存档时凡是把 shell grep 命令写进单元格就会中招——直接写纯文字描述即可。

9/26 第十三次验证成功（47条，S=5（10.6%）/A=10/AKB大TOP=10/B=15/C=17，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 47 条 S/A 列表、未重分级，**作用域对齐**；layer4 新增模式 #321~#323、趋势表唯一（节数=1）、文件1371→1379行、diff 仅 `a` hunk（1272a1273,1279 + 1371a1379）；validate-tables.py 首跑即报 `tables: 17 | problems: 0`；审计四步全 PASS（Step4 信号 9/12，占位文字 0）；发送走 curl fallback 默认路径，代理测 302 → **5 段全 OK、零等待**）。三项新增经验：① **MAX_PUB 三处一致**（L23／L4／主进程独立 offset 分页聚合均为 09-24 17:55 id=7987）→ 与前几日「L23 误报」不同，一致时无需再做双检索确证，但主进程独立聚合复核仍保留为常规步骤；② **低量日（47条）与评分器异常低值**：本日 ts 分布整体正常、无 None、无垃圾标题，但出现 3 条 `ts=0.0`（5c932109/6cadc10c/93f11ae0）＋1 条 `ts=0.9`（b9d654b7）——按 8/11 纪律「目标赛道+事件型才升级」处理：日向坂五期生新曲（223字短报）维持 B，ミスコン条（1571字充实但非目标赛道）不升级归 C；③ **模式编号必须取文件正文最大值，不能只看趋势表最后一行**：layer4 子 agent 自报「文件实际最大编号是 #320（bullet 节已到 #320），而趋势表最后一行只列到 #318」——首轮按趋势行取号造成 #319/#320 撞车，已从备份回滚重做为 #321~#323。审计 Step 3 的最高编号统计（行首 `### N.` + bullet `**#N`）正是为此设计的，汇总时务必核对。

9/27 第十四次验证成功（55条，S=5（9.1%）/AKB大TOP=18/A=7/B=14/C=11，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 55 条 S/A 列表、未重分级，**作用域对齐**；layer4 新增模式 #324~#328（5条）、趋势表唯一（节数=1）、文件1379→1388行、diff 仅 `a` hunk；validate-tables.py 首跑 `tables: 11 | problems: 0`；审计四步全 PASS（Step4 信号 3/12，占位文字 0）；发送走 curl fallback 默认路径，代理测 302 → **6 段全 OK、零等待**）。四项新增：① **MAX_PUB 三处一致**（L23／L4／主进程独立 offset 聚合均为 09-27 20:06 id=8020）→ 与前几日「L23 误报」不同，一致时无需再做双检索确证，但主进程独立聚合复核仍保留为常规步骤；② **L1 summary 的 key 截断不因硬性指令而消除**（8/10 / 9/23 / 9/27 三度复现）→ 见上方「层间数据传递模式」9/27 条，workspace 报告文件是取全 key 的可靠路径；③ **L4 子 agent 报「layer4_aggregate.py 不存在」实为 delegate context 路径多写了 `creative/`** → 见上方「路径前缀不对称」条；④ **24-48h 分层连续第2日为空**（9/25 全天 0 发布形成 44.5h 空窗，超 #322 的 31.7h），但未来队列 5 条活跃（09:11/11:15/15:01/18:01/20:06）→ **未触发停摆告警**，判据见 layer4 skill 的 9/27 边界澄清。

9/28 第十五次验证成功（52条，S=5（9.6%）/AKB大TOP=17/A=10/B=10/C=10，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 52 条 S/A 列表、未重分级，**作用域对齐**；layer4 新增模式 #329~#333、趋势表唯一（节数=1）、文件1388→1397行（+9）、diff 仅 `a` hunk、旧模式 #51 抽查完整；审计四步全 PASS（Step4 信号 9/12，占位文字 0）；发送走 curl fallback 默认路径，代理测 302 → **7 段全 OK、零等待**）。三项新增经验：① **MAX_PUB 三处一致**（L23／L4／主进程独立 offset 聚合均为 09-27 20:06 id=8020，9/27+9/28 连续两日一致）→ 「L23 误报 MAX_PUB」未复现，一致时无需双检索确证，但主进程独立聚合复核仍保留为常规步骤；② **L1 报告的「按来源分组全量列表」可能整组漏掉**（9/28 头部与分级都写52条、分级恰好覆盖52，但列表实际只有49 —— `fetch_by=なにわ男子` 3条整组缺失）→ 主进程必须重跑「各组条数之和 == total」核对并从原始 JSON 补齐，详见 layer1 skill 1e 的 9/28 条；③ **validate-tables.py 的「标题必须紧邻表格」口径**（把粗体引导句改成标题后若标题与表格间仍夹一行正文，复跑仍 problems:1）→ 见上方「表格格式铁律」规则11。

9/29 第十六次验证成功（55条，S=7（12.7%）/A=13/AKB大TOP=12/B=7/C=16，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 55 条 S/A 列表、未重分级，**作用域对齐**；layer4 新增模式 #334~#338、趋势表唯一（节数=1）、旧模式 #51 完好、文件1397→1406行（+9）、diff 仅 3 个 `a` hunk；validate-tables.py 首跑 `tables: 10 | problems: 2`（两处粗体引导句直压表格）→ 各加一个 `####` 标题后复跑 `problems: 0`；审计四步全 PASS（Step4 信号 8/12，占位文字 0）；发送走 curl fallback 默认路径，代理测 302 → **8 段全 OK、零等待**）。四项新增：① **MAX_PUB 三处一致**（L23／L4／主进程独立 offset 聚合均为 09-27 20:06 id=8020 key f109eccd8445）→ 连续第3日一致，无需双检索确证；② **新发现并已复核：API `search=` 不索引 content_ja 日文正文**（`我々は宇宙人` API=0 / 副本=28）→ 已 patch 进 layer23 skill；③ **新发现：副本「pub_time 非空」777 vs API published 集 775 的口径差 = 10 条 `publish_xhs='0'` 但已排定 pub_time 的未归位记录**（含 9/23 追查过的 TGC 条 id 7896）→ 分层基数必须用 API published 集，已 patch 进 layer4 skill 4a-4；④ **本日 ts 全件有值无 None、无垃圾标题，但 cs 普遍偏低（0.0~2.6）** → 分级靠 ts+cj_len+事件性补正（cs 塌陷似为 AI 再构成文本常态）。

9/30 第十七次验证成功（57条，S=6（10.5%）/A=8/AKB大TOP=17/B=5/C=21，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 57 条 S/A 列表、未重分级，**作用域对齐**；layer4 新增模式 #339~#343（5条）、趋势表唯一（节数=1）、旧模式 #51 完好、文件1406→1415行（+9）、diff 仅 3 个 `a` hunk；validate-tables.py 首跑即 `tables: 6 | problems: 0`；审计四步全 PASS（Step4 信号 7/12，占位文字 0，唯一 `待补充` 命中是存档里「无「待补充/待查询」占位」的自指否定句——属误报，自查时记住这类自指句会被 grep 捕到）；发送走 curl fallback 默认路径，代理测 302 → **6 段全 OK、零等待**）。四项新增：① **MAX_PUB 三处一致**（L23／L4／主进程独立 offset 聚合均为 09-27 20:06 id=8020 key f109eccd8445）→ 连续第4日一致，无需双检索确证；② **⚠️ 发布管道停摆告警首次正式触发**（自 9/10 以来首见）：MAX_PUB=09-27 20:06 距查询 54.4h（2.27天）、9/28+9/29 全天 0 发布、定时队列清空、<24h=0 → 三判据齐全，告警成立（模式 #343）；对照 9/27（同一条 MAX_PUB 但队列 5 条活跃）未触发 → **边界规则得到实证**；③ **推翻 #334**：板野育儿贴文 6891 第9窗口**恢复增长 +1.19%**（123462→124933），证明单窗口「零增长」≠ 终局冻结——凡把单窗口零变化写成终局结论的模式都要留「平台二次分发可恢复」的余地；④ 9/27 定时批次 5 条跨入 >48h：4 条 v>imp 中间态**快照全量归位**（8089横山由依 4047→7245 等），全库 v>imp 5→1；8128 三坂道合体 >48h 仍 0/0 → **零分发确认**（第5例）。浓度风险（建议合并/降级）：MINAMO『母の泥舟』6连入库0发布、『時給三〇〇円の死神』同片4条0发布、佐久間×土屋 MV 同日5条。

10/2 第十八次验证成功（55条，S=6（10.9%）/A=12/AKB大TOP=16/B=14/C=7，layer1先行→layer23+layer4并行；layer23 沿用 layer1 的 55 条 S/A 列表、未重新分级，**作用域对齐**；layer4 新增模式 #347~#351（5条）、趋势表唯一（节数=1）、文件1422→1431行（+9）、diff 仅 3 个 `a` hunk、旧模式 #47-#51 完好；validate-tables.py 首跑即 `tables: 4 | problems: 0`；审计四步全 PASS（Step4 信号 8/12，占位文字 0）；发送走 curl fallback 默认路径，代理测 **20809=302（20808=000 已死）** → **5 段全 OK、零等待**）。四项新增：① **MAX_PUB 三处一致**（L23／L4／主进程独立 offset 聚合均为 2026-10-01 22:01 id=8234 key=fa0f6a01ed73ac76c10be310284c62564c8cffed）→ 一致时无需双检索确证，但主进程独立聚合复核仍保留；② **⚠️ 发布管道停摆告警首次「解除」**：9/27 20:06→10/1 22:01 出现 **97.9h（4.08天）超长空窗**（创窗口新纪录，超 9/25 的 44.5h、9/30 告警 54.4h、10/1 升级值 78.4h），10/1 22:01 恢复发布 → #343/#344 连续2日停摆告警正式终结；定时队列同步恢复 4 条全 rewritten；③ **代理端口漂移 20808→20809**（curl 测 20809=302、20808=000），curl fallback 脚本读 $http_proxy 自动切换、无需改代码；④ **L1 报告落点可能在 /tmp**（本次 `/tmp/review_l1_report_2026-10-02.md`，非 workspace）→ 主进程按 L1 summary 报的路径 read_file，见上方「层间数据传递模式」10/2 条。
