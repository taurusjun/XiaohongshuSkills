---
name: xhs-daily-material-review-layer1
category: creative
description: 每日素材review第1层：全量扫描、聚类、S/A/B/C分级。本skill只负责第1层，不包含后续层。
temperature: 0.3
---

# 第1层：全量扫描 + 聚类 + 分级

**⚠️ 重要边界：本子 agent 只能做 查询 + 分析 + 分级，禁止任何写入操作（update.sh / PUT /api/news / 写稿）。**

## 任务

执行每日素材review的**第1层**：全量扫描今日素材 → 聚类 → 分级输出。

## 步骤

### 1a. 拉取全量素材

**⚠️ 铁则（7/9教训）：必须带 `date_from` 参数，禁止无日期范围的全库查询。**
- ❌ 错误：`curl .../api/news`（无date_from → 全库3192条，完全不是当日素材）
- ✅ 正确：`curl .../api/news?date_from=$(TZ=Asia/Tokyo date '+%Y-%m-%d')&limit=200`
- 先拉 `total` 字段确认当日数量。当日 = 大部分节点 ~50-300条；全库 = 数字大4-5倍

**⚠️ 7/7教训：API 可能返回当日0条素材**（节假日、周末、feed延迟等原因）。此时不能放弃或报错——必须自动向后扩展日期范围，获取最近可用的全量素材。

**⚠️ 扩展前先排除「抓取(ingest)失败日」（10/7 新增）：** 当日 0 条时，先看 `/Users/user/PG/XiaohongshuSkills/logs/fetch_runner.log` / `data/logs/task_YYYY-MM-DD.log`——若全关键词 `Connection timed out`（结尾「❌ 所有关键词均未找到新闻」）且最近几天素材**已各自 review 过**（`~/.hermes/daily-reviews/<date>.md` 存档存在），这不是「真无新闻」而是抓取失败：**不要自行扩展重审**（会产出旧素材 S/A，误导 03:00 写稿 cron 重写旧稿、把已发布条 publish_xhs 回退为 0）。此时在报告首行标注「🔴 抓取失败日・当日素材0条」并交回主进程决策，不要自动扩展。详见主 skill `references/2026-10-07-ingest-failure-vs-zero-news.md`。

**⚠️ 范围扩展时必须同时使用 `date_from` + `date_to`**（否则又变成全库查询）。
扩展后必须在报告中**第一行注明实际查询范围**（如"数据范围：2026-07-07~2026-07-09"），以便下游的 layer23 / 主进程能识别数据是否跨天 —— 避免 layer23 用当日素材分析、主进程发现分级结果不匹配时不知数据来源。

**标准做法：先查当日，如果为0则扩展前推：**

```bash
# 第1步：查当日
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=$(TZ=Asia/Tokyo date '+%Y-%m-%d')&limit=200" | python3 -c "
import sys, json; d = json.load(sys.stdin)
total = d['total']
print(f'Total: {total}')
if total == 0:
    print('ZERO_ENTRIES_FOR_TODAY')
else:
    for r in d['rows']:
        k = r.get('key','')[:12]
        t = (r.get('title','') or '')[:60]
        ts = r.get('title_score',0)
        cs = r.get('content_score',0)
        st = r.get('story_type','') or ''
        fm = r.get('format','') or ''
        src = (r.get('source','') or '')[:15]
        print(f'{k} | ts={ts}/cs={cs} | st={st} fm={fm} | {src} | {t}')
"
```

如果输出包含 `ZERO_ENTRIES_FOR_TODAY`，**自动扩展范围**：

```bash
# 扩展为最近3天（例：7/5~7/7 或 7/4~7/6）
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=$(TZ=Asia/Tokyo date -v-3d '+%Y-%m-%d')&date_to=$(TZ=Asia/Tokyo date '+%Y-%m-%d')&limit=200" | python3 -c "
import sys, json; d = json.load(sys.stdin)
total = d['total']
print(f'Total (3-day range): {total}')
if total == 0:
    print('STILL_ZERO_EXPAND_TO_7D')
for r in d['rows']:
    k = r.get('key','')[:12]
    t = (r.get('title','') or '')[:60]
    ts = r.get('title_score',0)
    cs = r.get('content_score',0)
    st = r.get('story_type','') or ''
    fm = r.get('format','') or ''
    src = (r.get('source','') or '')[:15]
    print(f'{k} | ts={ts}/cs={cs} | st={st} fm={fm} | {src} | {t}')
"
```

如果仍为0，扩展到7天（`date -v-7d`）。**如果完全无数据，必须明确标注「⚠️ 今日无新素材」并终止后续层执行**——不能对空数据做聚类和分级。

**输出字段：** key, title, title_score, content_score, story_type, format, source, category, tags, content_ja_len，为后续聚类和分级提供完整信息。先查总数确认范围。一次拉回后即可在Python中做所有判断，不需要重复curl。

对以下素材必须看内容详情，不能只看标题：
- story_type='story'或format='story'的条目
- title_score >= 4.0的条目
- 目标赛道内（坂道系/AKB/娱乐女艺人）的所有条目

通过 `curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=$(TZ=Asia/Tokyo date '+%Y-%m-%d')&limit=200"` 先获取全量数据，再在Python中直接用 `.get()` 读取各字段（title、content_ja、source、tags、category等）。

**⚠️ 坑：** `GET /api/news/<key>` 详情端点对许多记录返回空数据（key不存在），但全量list端点的结果中字段齐全。先用list endpoint获取数据，不要依赖详情端点作为唯一数据源。

**⚠️ content_ja已在list结果中（7/3教训）：** list endpoint返回的数据已包含`content_ja`字段。不要另外curl GET /api/news/<key>去读取内容——不仅不必要，而且<key>详情端点经常返回空数据。在Python中直接用`r.get('content_ja', '')`即可获取全文，用于判断素材充实度和写第3层分析。这对保存delegate_task内的tool call配额很重要（50次上限）。

**⚠️ title_score（ts）可能全部为None（7/23发现）：** API 返回的 `title_score` 字段可能在某天全部为 None（ts=None），此时不可用 ts>=5.0 阈值分级。应对方案：
- 改用 `title_score` 字段作为替代（API 中另一字段，本日均为 5.0，同样偏高）
- 当日 ts=None 时，S级判定主要依赖 `cj_len>=500` 和「事件型/争议性/话题爆发属性」两个标准
- 同时 cj_len 也会偏高（因为 title_score 高分的条目通常 cj_len 也高），但事件性判断仍有效
- 在报告中标注「⚠️ ts字段本日为None，分级基于cj_len+事件性判断」

**⚠️ ts异常低值（ts≈0.1）也可能是评分器异常，先读内容再分级（8/11发现）：** 目标赛道内条目出现 ts=0.1 这类异常低分时，先读 content_ja 判断事件属性，不能只看 ts 归 C/B。8/11 案例：正源司阳子时隔6作再任日向坂center（ts=0.1，评分器异常/标题截断所致），内容464字证实为 center 发表+本人采访，按「事件型属性」升级至 A 级并在报告中注明原因；同批另一条 ts=0.1 的 Adobe 音乐节摊位（非目标赛道+软文性质）则正常归 C。区分标准：目标赛道+事件型（center发表/卒业/争议）→ 升级并注明；非目标+低质 → 正常 C。升级后务必在分级说明里写「ts=X 为评分器异常」，让下游 layer23/主进程知道这不是阈值放水。

**⚠️ 标题被提示词/模板文本污染（垃圾标题，8/1发现）：** title 字段可能是「标题生成/重写环节的提示词泄漏」而非真实标题。特征：标题看起来像提示词片段（「我的标题」「【标题规则（资讯体）】等，说明这些是字段」「生成的中文标题」「- the final」「字段，我还需要考虑：」等）。8/1案例：34条中10条(29.4%)被污染，其中一条 ts=5.0 但标题不可用（内容=和田海佑发言）。处理规则：
- 识别后归C级并标注「垃圾标题；内容=XXX（真实事件简述）」——**不能只看ts分级**（污染标题可能ts=5.0）
- content_ja 仍是真实新闻，需读内容确认事件；部分污染条有正常标题孪生条（同日同事件），直接用孪生条
- 报告中统计占比并建议知会抓取/生成侧修复
详见 `references/8-01-title-pollution-junk-entries.md`。

### 1c. 同事件条目聚类

先聚类再分级。同事件多条→保留1-2条最丰富的，其余合并跳过。**聚类时必须跨来源聚类**——同一事件可能来自エンタメ総合、モデルプレス、音楽ナタリー等不同来源，title各不相同。按事件主题（如"THE MUSIC DAY 42人シャッフル"、"東京台音楽祭"、"柏木由紀関連"）而非按来源分组聚类。具体方法：
1. 列出全量条目
2. 按事件主题做第一次聚类（多来源合并）
3. 在聚类组内选出内容最丰富/标题最好的1-2条保留
4. 标注聚类原因（事件名、交集条目数）

**注意「日期陷阱」：** 同事件素材可能昨天已入库已发布。检查前一天的已发布素材。

### 1d. 聚类后分级 — ⚠️ 定量阈值

**严格限制S级数量：总素材的10%-15%，超过即误判。** 52条素材中S级不应超过5-8条；150条素材中S级不应超过15-22条。7/14案例：Layer 1用lenient标准分出15S/52（29%），而Layer 23用正确阈值分出8S/52（15%），证实宽松阈值导致过量。

**S级定量门槛（必须同时满足至少2条）：**
- title_score >= 5.0（layer23已验证此阈值合理）
- content_ja_len >= 500字
- 事件型/争议性/话题爆发属性（并非纯日常/晒照/商务合作）
- 有完整叙事弧线（不是纯信息罗列）

**S级排除规则（以下即使分高也降1-2级）：**
- グラビア/セクシー女優纯颜值类（除非有特殊故事性如#4020东实果）
- 商务合作/宣传类（新剧上映纯信息、品牌合作等）
- 纯晒照/日常（全家福、迪士尼游记等）

**分级标准：**

| 级别 | 定义 | 定量参考 | 处理建议 |
|------|------|---------|---------|
| S级 | 强烈推荐 | ts>=5.0 + cj_len>=500 + 事件型属性，占总量≤15% | 优先写长文 |
| A级 | 可选 | ts>=3.0，有故事性或转换价值 | 可选写稿 |
| ✅ AKB大TOP | 必须入库 | 坂道系/AKB相关，ts≥3 | 写短news入库 |
| B级 | 轻量跑量 | ts<3.0，轻度素材 | 写500-600字短news |
| C级 | 跳过 | 低分无热点，或与已有同事件素材重复 | 跳过并说明原因 |

AKB大TOP分流：
- 事件型（育儿/争议/对话/爆料）→ 写全文
- 晒照型（全家福/迪士尼/日常照）→ summary bullet入库

### 1e. 输出格式

先按来源分组输出全量列表（标注每条的关键字段），再聚类分析（跨来源，标注哪些条目合并到哪组），再分级结果（S/A/AKB大TOP/B/C，每个级别内按优先级排序）。

**⚠️ 全量列表必须逐组核对「组内条数之和 == total」，否则会静默漏掉某个来源组（9/28 教训）：** 9/28 报告头部与分级结果都写「52条」且分级恰好覆盖 52 条，但 ①按来源分组的全量列表实际只有 **49 条** —— `fetch_by=なにわ男子` 那 3 条整组没有出现在列表里（只在分级表的 A级/C级 里以单条形式露头）。**根因：** 生成列表时手工按来源拼块，某个分组被跳过，而头部的 total 是直接读 API 的，两者不一致却没人发现——报告自洽地错着。**防范：** 输出前跑一次自动核对，`Counter(fetch_by)` 的每个组都要有对应小节，且各节条数之和 == total；组块直接由脚本按 fetch_by 生成（不要手写），条数不匹配就补齐后再交报告。主进程汇总时也应重跑这条核对（漏组会直接让存档的「全量素材一览」少条，且与 published 比对时对不上）。

**⚠️ key 必须写完整 40 字符，禁止截断（9/23 教训）：** 输出里的每条素材 key 都要写全（如 `ba9a938aa5b83e908f08df0fe8a52a424f8df716`），**不要只写前 12 位**（如 `ba9a938aa5`）。主进程要用完整 key 做三件事：① 拼存档的「全量素材一览」列表 ② 与 published 集比对发布数据 ③ 交给 layer23/layer4 做精确检索。截断 key 会逼主进程再拉一次全量 JSON 做映射（9/23 实测浪费一次 tool call），且存档列表无法与 published 集对齐。**子 agent 报告里的 key 截断是高频摩擦点，写到输出格式要求里当硬约束。**

**分级结果的丰富输出格式（参考6/28实践）：**
```markdown
### S级（强烈推荐）🔥

- **#key 标题** — 理由，优先级评分
```

这种列表形式比表格更易读，且不会被segment-send的分段打断。

### 1f. ⚠️ 输出时的数据范围标注（7/9追加）

Layer 1 的报告是整个 review pipeline 的基石，layer23 和主进程依赖 Layer 1 的分级结果做后续分析。如果 Layer 1 查询的日期范围不是「纯当日」，必须明确标注。

**报告开头必须包含：**
```markdown
**データ範囲：** 2026-07-09（当日のみ）
```
或：
```markdown
**データ範囲：** 2026-07-07〜2026-07-09（3日拡張）
```

**为什么重要：** 7/9 session 中 Layer 1 无 date_from 查询了全库3192条，分级出「能条爱未結婚」「長尾謙杜スキャンダル」等S级素材，但这些事件不在当天61条中。Layer 23 并行运行时查当日数据只找到完全不同的素材，主进程在汇总时不得不花额外时间手工核对和再合成。一个简单的 `データ範囲` 标注就能让主进程立即知道 Layer 1 的数据是否可靠。

## 参考文档

- `references/7-09-subagent-date-scope-pitfall.md` — Layer 1 子 agent 无 date_from 查全库的7/9教训

## 禁止操作

- 禁止调用 update.sh
- 禁止 PUT /api/news
- 禁止写稿/写 rewritten 内容
- 禁止标记 publish_xhs / preselected
