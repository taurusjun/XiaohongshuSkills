---
name: xhs-daily-material-review-layer4
category: creative
description: 每日素材review第4层：发布数据回顾。查已发布数据、分析反馈闭环、更新data-feedback-patterns.md（仅append）。禁止DB写入操作。
temperature: 0.3
---

# 第4层：发布数据回顾

**⚠️ 重要边界：本子 agent 只能做 查询 + 分析 + 输出。禁止DB写入（update.sh / PUT /api/news）。data-feedback-patterns.md **仅允许末尾append新条目和更新趋势表最后一行**，不修改已有内容或历史模式。**

## 任务

查询已发布的小红书数据，分析反馈闭环，更新 data-feedback-patterns.md。

## 步骤

### 4a. 查询已发布数据（必须全量分页）

```bash
for off in 0 250 500 750 1000; do
  curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?publish_xhs=published&sort_by=xhs_pub_time&sort_dir=DESC&limit=250&offset=$off" -o /tmp/pub_$off.json
done
```

**⚠️ 单页 `limit=50` 只能做抽查，不能做当天分层。** 库内 published 已 760+ 条，只拉 3 页会把最新发布的一批整段丢掉。必须按 offset 步进 250 拉取到空页，然后 `unique(id) 数` 与响应里的 `published` 计数比对，差额 = 漏掉的条数。

实测（9/21）：offset 0/250/500 只得 750 条，补 offset=750 后 761 条，才与 API `published=762` 对齐（差额 1 = pending/边界条）。**漏掉的那 11 条恰好包含当天 24-48h 与 <24h 层的全部新条** —— 分层表会直接错。

### 4a-2. 字段口径（list endpoint 实际字段名）

| 用途 | 字段名 | 备注 |
|---|---|---|
| 阅读 | `xhs_views` | — |
| 曝光 | `xhs_impression` | **单数，不是 `xhs_impressions`**；写错只会静默拿到 `None`，不报错 |
| 互动 | `xhs_likes` / `xhs_comments` / `xhs_saves` / `xhs_shares` | 四者之和 = 互动总数 |
| 点击率 | `xhs_click_rate` | 0~1 小数（0.284 = 28.4%） |
| 涨粉 | `xhs_fans_gained` / `xhs_watch_time` | 常为 0 |
| CTR | `xhs_ctr` | **该 key 在 API 返回中不存在** → 报告里直接写「CTR 数据缺位」，用 `xhs_click_rate` 作替代口径并注明 |

排查技巧：拿一条记录打印 `sorted(r.keys())`，再筛含 `xhs` / `view` / `imp` 的字段名。比照旧报告里的字段名硬编码可靠 —— 9/21 就因把 `xhs_impression` 写成复数，首轮输出全部 `imp=None`。

### 4a-3. 时间解析必须兼容三种格式

`xhs_pub_time` 实际有三类值，只支持 `%Y-%m-%d %H:%M` 会把 10 字符日期整批误判为「不可解析」：

1. 空字符串 / None —— 4-5 月期老条，归入「无 pub_time」，排除出分层
2. `YYYY-MM-DD`（10 字符纯日期）—— 按当日 00:00 解析
3. `YYYY-MM-DDTHH:MM`（T 分隔 16 字符）—— 需 `%Y-%m-%dT%H:%M`，**不能**套空格格式

```python
def parse_dt(p):
    if not p: return None
    p = str(p).strip()
    if len(p) >= 16:
        for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M'):
            try: return datetime.datetime.strptime(p[:16], fmt)
            except ValueError: pass
        return None
    if len(p) == 10:
        try: return datetime.datetime.strptime(p, '%Y-%m-%d')
        except ValueError: return None
    return None
```

在下「N 条 pub_time 不可解析」的结论之前，先把三种格式都试一遍 —— 9/19 窗口误报 18 条不可解析，其中 8 条只是 parser 少了一个分支（真正的不可解析只有 10 条）。

附带口径：`v > imp`（views 大于曝光）的条单独列出标记为口径异常，不可当作效率结论；`imp` 为 0/个位数的 <24h 新条属**中间态脏数据**（#124），只记录不下结论。

> 以上三步已封装为 `scripts/layer4_aggregate.py`：一条命令完成全量分页 + 字段体检 + 三段分层 + Top/最低 + 零曝光清单 + v>imp 异常，直接产出报告底稿。用法 `python3 layer4_aggregate.py 'YYYY-MM-DD HH:MM'`（**必须带引号传显式查询时间**；省略则用**本机时区**当前时间——本机为 CST/UTC+8 而 review 按 JST/UTC+9 运行，两者差 1h（10/5 机器 01:32 vs JST 02:30），会带来时距/分层微小偏差；且双参数被 shell 拆词时会静默落到机器当前时间，带引号可避免）。首次运行时若某字段名漂移，按 4a-2/4a-3 的内联命令兜底并顺手 patch 本 skill。

### 4a-4. ⚠️ API published 集 vs DB「pub_time 非空」的口径差 = 10 条未归位记录（9/29 实测）

**不要用只读副本的「`xhs_pub_time` 非空」条数当作分层基数** —— 它比 API published 集多。9/29 实测：

| 口径 | 条数 | >48h | 说明 |
|---|---|---|---|
| `publish_xhs='1'`（= API published 集） | **775**（API 计数 776，差 1 边界条） | 762 | ✅ 分层以此为准 |
| 其中 pub_time 非空 / 为空 | 767 / 8 | — | 8 条为 4-5 月期无 pub_time |
| `publish_xhs='0'` 但**已排定 pub_time** | **10** | 计入后 772 | ❌ 不计入已发布集 |
| 副本合计 pub_time 非空 | 777 | 772 | = 767 + 10 |

那 10 条是「pub_time 已排定但 publish_xhs 未归位」（9/22 12:01、9/06 12:04、8/08 11:43、7/22 20:05、7/13×3、6/23、6/17、2025/06/05，含 9/23 报告中追查过的 TGC 条 id 7896）。**副本周查「管道健康度」时会看到 777，若直接当分层基数就会得到 772/5/0 的错误分层**（子 agent 9/29 即如此报出）。正确处理：分层与 MAX_PUB 用 **API published 集（775/762）**；副本 777 只用于「按人物聚合发布史」的 OR 全文匹配。两者 MAX_PUB 应一致（9/29 均为 09-27 20:06／id 8020／key f109eccd8445）——若不一致，以 API 聚合为准并排查未归位条。

### 4b. ⚠️ 关键检查点

**每一条 published 数据，必须先检查这三个字段：**
1. `publish_mode` — rewritten/caption/normal？
2. `rewritten_title` — 实际发布的标题（≠原始title！原始title是feed抓取时的标题）
3. `rewritten_content` — 有改写内容还是空？

这些字段在 list endpoint 的结果中已包含（`curl .../api/news?publish_xhs=published&limit=50`）。**不要依赖 GET /api/news/<key> 详情端点**——它对许多记录返回空，而list endpoint的数据是完整的。直接解析 list endpoint 的返回即可。

### 4c. 数据分层

按 >48h（充分分析）/ 24-48h（初动参考）/ <24h（刚发不计）三段输出。

**⚠️ 未来 pub_time = 定时发布队列（8/5确认）：** 查询 published 数据时可能混入 xhs_pub_time 为**未来时间**的记录（如 8/5 查询时出现 08-05 09:33~20:10 的 pub_time）。这些是**定时发布队列**（口径见 data-feedback-patterns #112/#114），0阅读/0互动属预期——**必须排除出时间分层，不可下「冷门」结论**。解析时 `pub_dt > now` 即归为定时队列，单独一行说明。8/5 案例：50条 published 中 5 条未来 pub_time，全部按此处理。

**⚠️ 反向信号——发布管道停摆（9/10确认）：** 若三层分层出现「全部条目 >48h、24-48h=0、<24h=0、未来队列=0」且最新 xhs_pub_time 已是数天前（9/10：最新=09-06 08:59，停摆第4天），这不是数据异常而是**发布管道停摆**（队列为空、无新发布）。处理：分层表照常输出但加一行流程告警「⚠️ 发布管道自 <最新pub时间> 后停摆 N 天，队列为空」；此时旧数据仍可正常做 >48h 反馈闭环（9/10 全部50条>48h 照常分析并新增 #235/#236/#237），但「无 24-48h 初动参考、无 <24h 新条」本身就是当日最重要的发现——写进存档并把管道停摆写入趋势表说明列（9/10 趋势行已含「9/6 08:59后发布管道停摆第4天」）。

**⚠️ 「24-48h 连续为空」≠ 管道停摆（9/27 边界澄清）：** 9/27 实测 24-48h=0（**连续第2日**空）且 >48h=761，但未来队列=5 条（09:11/11:15/15:01/18:01/20:06）、<24h=1 → **不触发停摆告警**。判据始终是「未来队列为空 **且** <24h=0 **且** 最新 pub 已是数天前」，三者缺一即非停摆；单看 24-48h 空会误报。9/27 的正确输出 = 分层表照常输出 + 一行过程记录「9/25 全天 0 发布，形成 44.5h 空窗（超 #322 的 31.7h）」，**不是**停摆告警行。反过来说，「24-48h 连续空 + 队列活跃」本身是有信息量的新形态（发布节奏呈「恢复→长空窗→再恢复」），值得写进模式与趋势行。

**✅ 9/30 首次正式触发停摆告警（边界规则实证，#343）：** MAX_PUB=2026-09-27 20:06（id=8020）距查询 **54.4h（2.27天）**，9/28+9/29 全天 0 发布、未来队列=0、<24h=0 —— 三判据齐全 → **告警成立**。对照 9/27（同一条 MAX_PUB，但未来队列 5 条活跃）→ **未触发**。**判据链固化（按此顺序自查）：① 先看未来队列是否非空——非空即直接排除停摆，不必再看 <24h；② 队列空时再核 <24h==0；③ 再核 MAX_PUB 距查询是否已达「数天」（≥48h，9/30 为 54.4h）。三者全中才写告警行。** 告警行的写法：分层表照常输出 + 单列「⚠️ 发布管道停摆告警」小节，写明 MAX_PUB、停摆时长、全天 0 发布的日期，并把「积压率」（今日入库数 vs 近 N 日发布数）一并报出，供写稿/入库前决策。

**每条>48h数据输出反馈闭环：当初判断 → 实际数据 → 结论。**

**⚠️ 效率约束（7/6教训）：** 作为delegate_task子agent，你只有600秒运行时间和有限tool call配额。50条published数据的逐个分析非常耗配额。以下优化策略必须使用：
1. **批量时间分层** — 一次Python脚本同时完成时间分层和基本统计，减少反复curl次数
2. **聚焦最佳和异常的5-7条** — 不必每条>48h都输出完整反馈闭环，聚焦阅读Top 3 + 阅读最低3条 + 有特殊模式的条目。其余可用汇总表带过
3. **data-feedback-patterns.md修改合并到最后一步** — 先完成所有分析，最后一个tool call同时完成append和趋势表更新
4. **优先从list endpoiint数据判断** — list endpoint已包含全部必要字段（rewritten_title/publish_mode/xhs_views等），不需要另curl详情端点

### 4d. 更新 data-feedback-patterns.md

**这是强制步骤，不是可选项。** 主 skill 的 4d 要求必须执行。

必须在 `~/.hermes/skills/creative/xhs-daily-material-review/references/data-feedback-patterns.md` 末尾 append：
- 至少一条新模式（带日期标签和案例）
- 跨会话趋势表最后一行更新（日期+新增模式+来源数据）
- 各字段格式与已有记录一致

**⚠️ 趋势表必须保持单一累积表（8/4发现）：** 「跨会话趋势表」应始终是文件末尾**唯一一张表**，每次 append 一行新日期（表头：`| 日期 | 新增模式 | 来源数据 | 说明 |`），**不要新建「## 跨会话趋势表（YYYY-MM-DD 更新）」节**。8/3 与 8/4 各自新建了一节，文件末尾出现两个趋势表节、且一个带表头一个不带——跨会话可读性下降。若发现已有多个趋势表节，合并到最后一个（保留表头行）。

**新模式块推荐格式（8/10起）：** 节头 `### YYYY-MM-DD 发布数据review新增模式`（日期标签）+ bullet `- **#NNN 标题**：描述（含案例数据）`。审计脚本（xhs-review-self-audit Step3）已适配此格式——日期节头不会被误判为模式编号、bullet 风格也能统计到。不要用「### NNN.」行首格式写新模式（两种格式并存会降低可读性，且旧审计写法会误判）。

**权限说明：** 本文件只允许 append 新条目和更新趋势表最后一行。禁止修改已有模式条目或历史趋势行。

### 4d-2. 精确写入与校验（定点插入，禁止整文重写）

cron 环境下 `execute_code` / `from hermes_tools` 被 BLOCK，`write_file` 整文重写有覆盖旧模式的风险。9/21 验证过的稳妥做法：

1. `cp` 备份原文件到 `/tmp/dfp_backup_$(date +%s).md`
2. 用 `write_file` 把插入脚本写到 `/tmp/update_patterns.py`（**脚本不是目标文件**，写它没有风险），再 `terminal` 跑 —— 避免 `python3 -c` 里长中文 + 三引号的转义地狱；报告里附上脚本路径便于复核
   - **⚠️ /tmp 在多个 delegate_task 子 agent 间共享（10/4 实测）**：固定名 `/tmp/update_patterns.py` 会被**同批并行的 sibling 子 agent** 覆盖——本会话 `write_file` 即告警「modified by sibling subagent 'sa-1-…' but this agent never read it」。稳妥做法：脚本名带唯一后缀，如 `/tmp/update_patterns_l4_$(date +%m%d).py`（历史运行留下的 `update_patterns_l4_1001.py` 正是此因）。**写完必须 `read_file`/`grep` 复核脚本里自己的模式编号（如 `grep -c '#357'`）确实在**，再 `terminal` 跑；否则可能跑的是 sibling 的脚本、写错日期/编号。
3. 脚本内两步定点插入：
   - **新模式节**：先 `assert c.count(ANCHOR) == 1`（ANCHOR = `## 跨会话趋势表`），再 `c.replace(ANCHOR, BLOCK + ANCHOR, 1)`
   - **趋势行**：`lines = c.split('\n')` → `last = max(i for i, l in enumerate(lines) if l.startswith('|'))` → `lines.insert(last + 1, ROW)` → 结尾若缺 `\n` 补上
4. 写完必须过四道校验，并把**实测值**写进报告（不是「应该没问题」）：
   - `wc -l` 行数增量 == 预期（`1 空行 + 1 节头 + 1 空行 + N 条 bullet + 1 空行` + 1 行趋势）
   - `grep -c '^## '` 必须 == 1 → 趋势表节唯一，没有误建重复节
   - `grep -c '^| 2026-'` 趋势行数 == 昨日 + 1
   - `diff 备份 现文件` **只允许出现 `a`（纯插入）hunk，出现任何 `c` / `d` hunk 就是改动了已有内容，必须回滚重做** —— 这是「未改动任何已有模式/历史趋势行」的硬证据，比自述可信
5. `sed -n '1,12p'` 抽检文件头部旧模式（如 #47-#51）仍在，把「旧模式真丢失」与「子 agent 误判旧模式丢失」区分开
6. 顺手确认趋势表节内是否有历史遗留的游离 bullet（例如夹在数据行之间的 `- **#145 ...**` 条目）—— 不要去清理它（属已有内容），但 `last '|' 行` 的判定必须仍然正确命中表格最后一行

**⚠️ 子agent常见自我报告错误（7/17经验）：** 读取 data-feedback-patterns.md 后，子agent可能错误断定「旧模式 #1-#50 已被覆盖丢失」，但实际上旧模式仍然在文件前部完整存在。这是因为子agent读文件时只看到了新追加的尾部内容，错误推测前部已丢失。
- 读取文件后先确认总行数（`wc -l`），再确认新模式行号之前的内容是否完整（9/2：子 agent 自报 1143 行 vs 实测 1142，行数自报可能 off-by-one——以 wc -l 实测为准）
- 不要仅凭「我看到新模式在行10」就断定「旧模式已丢失」——需要先确认行1-9的内容
- 写文件时用 `patch`（行级追加）而非 `write_file`（全文件替换），避免误覆盖
- 如果使用 `write_file`，必须在内容中保留所有旧模式（在前面）再追加新模式（在后面）
- 向主进程报告时，确认实际的行号和内容后再下结论

### 4e. 输出格式

```
## 发布数据回顾（YYYY-MM-DD）

### >48h 充分分析
| 标题(实际发布) | publish_mode | 阅读 | 互动 | 当初判断 | 结论 |
|---|---|---|---|---|---|

### 24-48h 初动参考
...

### <24h 刚发不计
...
```

## 禁止操作

- 禁止调用 update.sh
- 禁止 PUT /api/news
- 禁止修改已有数据记录（news DB 记录）
- 禁止标记 publish_xhs / preselected
- ✅ **允许：** data-feedback-patterns.md 末尾 append 新模式 + 更新趋势表最后一行
