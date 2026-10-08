---
name: xhs-review-self-audit
category: writing
description: 每日素材review完成后的自查审计。保证第1-4层全部执行到位、数据已verify、模式文件已更新。由 review cron 的最后一步加载执行。
triggers:
  - xhs-daily-material-review cron job的最后一步加载本skill执行audit
  - 在完成每日素材review后（写入存档后、输出最终响应前）加载执行
temperature: 0.1
---

# 素材Review 自查审计

## 什么时候用

在完成每日素材review后（写入存档文件后、输出最终响应前），加载本skill执行audit。

**注意：** 存档文件必须已写入。审计时会先检查存档是否存在，如果不存在则提示先写入存档。**禁止在审计中补写存档**——审计是核查工具，不是写入工具。

## 审计清单

### 第1层 全量扫描

- [ ] 拉取了全部当日素材（query.sh / curl），确认总数
- [ ] 按来源/关键字分组输出全量列表
- [ ] 做了同事件聚类（同事件多条→保留1-2条，其余合并跳过）
- [ ] 逐条标注了判定（保留/跳过）和原因
- [ ] 分级结果：S/A/B/C 级别都有明确判断标准

### 第2层 价值建议

- [ ] S级素材给出了标题方向、写法建议、素材充实度
- [ ] A级素材核心条目给出了写法方向

### 第3层 跨时间关联

- [ ] 查询了DB现有记录（同人物/同关键词）
- [ ] 第3层中**没有「待补充」「待查询」等占位文字** — 必须查到结果后写"有/无关联"而非"待查"
- [ ] 第3层查DB时拉的是全量（limit=200）而不是限定日期范围——日期限定会漏掉前一天的已入库素材

### 第4层 发布数据回顾

- [ ] 查了 `publish_xhs=published` 的数据
- [ ] 按 >48h（充分分析）/ 24-48h（初动参考）/ <24h（刚发不计）三段时间分层输出
- [ ] ⚠️ **每条published数据都检查了 rewritten_title 和 publish_mode** — title字段是原始feed标题，实际发布可能用的是rewritten_title
- [ ] 如果note_id为空但xhs_pub_time有值，同样正常分析（发布数据通过pipeline回写存储，不依赖note_id）
- [ ] 对48h+数据输出了反馈闭环（当初判断→实际数据→结论）
- [ ] 反馈闭环中列出了明确的模式验证结论（✅ 验证 / ⚠️ 部分验证 / ❌ 否定 / 🔥 新发现）

### data-feedback-patterns.md 更新

- [ ] 至少追加了一条新的模式（带日期标签和案例）
- [ ] 跨会话趋势表最后一行已更新（日期+新增模式+来源数据）
- [ ] 各字段格式与已有记录一致（没有多余的空格或错位）

### 存档文件

- [ ] 存档文件已写入 `~/.hermes/daily-reviews/YYYY-MM-DD.md`
- [ ] 第4层已经附加到存档文件中（不是单独写另一个文件）
- [ ] 第4层中任意一条published数据，如果指明了rewritten_title，分析结论应与原始title区分开

## 执行方式

audit 分四步。**先验数据，再验存档，再验模式文件，再验第3层实际操作。**

**⚠️ 重要：** 所有审计步骤必须使用 plain `terminal()` 工具 + 内联 Python（`curl | python3 -c "..."`）实现。`execute_code` / `from hermes_tools import` 在 cron 模式下会被BLOCK（无用户批准）。审计脚本必须可在纯 shell 环境中运行。

### Step 0: 获取今天的日期

```bash
echo $(date '+%Y-%m-%d')
```

### Step 1: 数据层审计 — 验证第4层是否查了 rewritten_title

⚠️ **这是6/25 session被用户反复纠正的核心问题。** 每次查published数据必须先查rewritten_title/publish_mode再分析，不能凭原始title下结论。

**cron模式验证路径：**

```bash
# 拉最新3条published数据，展示原始title vs rewritten_title
curl -s --noproxy "*" "http://127.0.0.1:5000/api/news?publish_xhs=published&limit=3&sort_by=xhs_pub_time&sort_dir=DESC" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for r in d['rows'][:3]:
    print(f'title:  {r.get(\"title\",\"\")[:40]}')
    print(f'rewritten: {r.get(\"rewritten_title\",\"\")[:40]}')
    print(f'mode:     {r.get(\"publish_mode\",\"\")}')
    print(f'pub:      {r.get(\"xhs_pub_time\",\"\")}')
    print(f'views:    {r.get(\"xhs_views\",0)}')
    print()
"
```

**⚠️ 9/27 实测：本步的 `limit=3&sort_by=xhs_pub_time&sort_dir=DESC` 可能返回「定时队列」条目**（未来 `xhs_pub_time`、`xhs_views=0`，如 09-27 11:15 / 15:01 / 18:01 三条排在最前），因为 published 接口的 DESC 排序不可靠 + 队列条可能排在最前。**这不影响本步判定**（队列条同样具备 `title ≠ rewritten_title` 的区分证据，反而更醒目），但**看到 3 条 views 全为 0 不要误判为「发布数据缺失/管道挂了」**——先看 `xhs_pub_time` 是否未来时间；要取真实最新已发布条，用 offset 分页聚合后客户端重排（见 layer4 skill 4a）。

然后检查存档文件的第4层是否区分了原始title和rewritten_title。

**两种可接受的格式（推荐用表格式，Step 2 grep直接匹配）：**
1. **表格式**（推荐，Step 2的"实际发布标题"grep会通过）：
   ```
   | 条目 | 模式 | 实际发布标题 | 原始标题 | 阅读 | 互动 |
   |------|------|-------------|---------|------|------|
   | fc9c... | rewritten | 岚解散才一个月 | 相叶雅纪最爱岚 | 5217 | 32 |
   ```
   表头必须含「实际发布标题」字样。

2. **内联描述式**（分析正确但Step 2可能false negative，需在Step 2后手动忽略）：
   ```
   - **相叶雅纪×二宫 (rewritten, 实际: "岚解散才一个月")** — 5,217阅读/32赞
   ```

**判断标准：**
- 第4层包含「实际发布标题」列（表格式）→ ✅ PASS（Step 2 自动通过）
- 第4层没有「实际发布标题」列但每行都明确了 mode + rewritten_title（内联式）→ ✅ PASS（Step 2 的 false negative 可忽略）
- 第4层引用的标题明显是原始title，无 rewritten_title/mode/publish_mode 区分 → ❌ FAIL

### Step 2: 存档文件完整性审计

**cron模式（用 terminal，不用 execute_code）：**

```bash
ARCHIVE=~/.hermes/daily-reviews/$(date '+%Y-%m-%d').md
if [ -f "$ARCHIVE" ]; then
    echo "✅ 存档存在: $ARCHIVE"
    wc -c "$ARCHIVE" | awk '{print "  文件大小: " $1 " bytes"}'

    # Check each layer — use multiple pattern variants per layer to handle
    # archive format drift (7/14 session used "全量扫描" not "全量素材一览")
    for entry in "全量素材一览|全量扫描:第1层" "价值建议|第2层:第2层" "跨时间关联|第3层:第3层" "发布数据回顾|第4层:第4层"; do
        pat=$(echo "$entry" | cut -d: -f1)
        label=$(echo "$entry" | cut -d: -f2)
        # Try each pattern separated by |
        found=false
        for p in $(echo "$pat" | tr '|' ' '); do
            grep -q "$p" "$ARCHIVE" && { echo "✅ $label ✓"; found=true; break; }
        done
        $found || echo "❌ $label 缺失（未匹配: $pat）"
    done

    echo ""
    echo "--- 反馈闭环检测 ---"
    grep -q "反馈闭环" "$ARCHIVE" && echo "✅ 反馈闭环 ✓" || {
        # Fallback: check for feedback-closure column markers in >48h analysis tables
        if grep -q "当初判断" "$ARCHIVE" && grep -q "结论" "$ARCHIVE"; then
            echo "✅ 反馈闭环 ✓（检测到「当初判断→结论」表格列，等同反馈闭环）"
        else
            echo "⚠️ 反馈闭环 not found as heading (check table has '当初判断'→'结论')"
        fi
    }

    echo ""
    echo "--- 实际数据(阅读/互动)检测 ---"
    grep -q "阅读" "$ARCHIVE" && echo "✅ 实际数据 ✓" || echo "❌ 实际数据 缺失"

    echo ""
    echo "--- 改写区分检测（表格式:实际发布标题 | 内联式:rewritten/原始）---"
    if grep -q "实际发布标题" "$ARCHIVE" || grep -qi "rewritten" "$ARCHIVE" || grep -q "原始title" "$ARCHIVE"; then
        echo "✅ 改写标题 ✓（检测到 rewritten 区分证据）"
    else
        echo "❌ 改写标题 缺失（未检测到 rewritten 区分）"
    fi

    # Final judgement
    FAILED=false
    for pat in "全量素材一览" "价值建议" "跨时间关联" "发布数据回顾"; do
        grep -q "$pat" "$ARCHIVE" || { FAILED=true; echo "❌ 缺失: $pat"; }
    done
    $FAILED && echo "❌ AUDIT FAIL" || echo "✅ AUDIT STEP 2 PASS"
else
    echo "❌ 存档不存在: $ARCHIVE"; exit 1
fi
```

（注意：改写区分的检测使用 or 逻辑——只要表格式「实际发布标题」或内联式「rewritten/原始title」任一匹配，就算通过。）

### Step 3: data-feedback-patterns.md 更新审计

**cron模式（用 terminal，不用 execute_code）：**

```bash
PATTERNS=~/.hermes/skills/creative/xhs-daily-material-review/references/data-feedback-patterns.md
# ⚠️ 9/24 修正：趋势表的「日期」列（第一列）用的是完整格式 `| 2026-MM-DD |`，
# 而本 skill 旧写法用短日期 `$(date '+%-m/%-d')`（如 9/24）去 grep —— 这是 false-negative 陷阱：
# 9/24 实测 `grep -c '9/24'` 恰好命中 1 次，但命中位置是某行「说明」列里的自由文本，
# 不是日期列。换一天（该日说明列若不提短日期）就会误报「❌ 趋势表缺失」。
# 正确做法：优先匹配完整日期，短日期仅作兜底（两者任一命中即通过）。
if [ -f "$PATTERNS" ]; then
    FULL=$(date '+%Y-%m-%d'); SHORT=$(date '+%-m/%-d')
    if grep -q "^| $FULL " "$PATTERNS"; then
        echo "✅ 趋势表OK: $FULL（完整格式，命中日期列）"
    elif grep -q "$SHORT" "$PATTERNS"; then
        echo "✅ 趋势表OK: $SHORT（短格式兜底命中，建议核对是否为日期列而非说明列文本）"
    else
        echo "❌ 趋势表缺失: $FULL / $SHORT"
    fi
    # ⚠️ 8/4修正：旧写法 grep -o '#[0-9][0-9]*' 会误匹配正文中的 #5002 等非模式编号（显示最高模式=5002 但实际最新是 #118）
    # ⚠️ 8/10修正：层4子agent可能把新模式写成两种格式，都会让 '^### [0-9]+' 误判最高编号：
    #   (a) 日期节头「### 2026-08-10 发布数据review新增模式」→ 被当成模式编号 2026（8/10实测）；
    #   (b) bullet「- **#133 标题**」→ 行首锚定统计不到（新模式已转bullet风格）。
    # 正确做法：行首模式用 '^### [0-9]+\.'（带句点，排除日期行）+ bullet '#NNN' 两路统计取最大：
    TOP1=$(grep -oE '^### [0-9]+\.' "$PATTERNS" | grep -oE '[0-9]+' | sort -n | tail -1)
    TOP2=$(grep -oE '\*\*#[0-9]+' "$PATTERNS" | grep -oE '[0-9]+' | sort -n | tail -1)
    echo "最高模式编号: $(printf '%s\n%s\n' "${TOP1:-0}" "${TOP2:-0}" | sort -n | tail -1)（行首=${TOP1:-无} / bullet=${TOP2:-无}）"
    # ⚠️ 8/7新增：趋势表必须保持文件末尾唯一一张表（8/4规范）。8/7 layer4 子 agent 合并了9个重复趋势表节
    # （文件 1037→926 行），审计应确认：节数=1、旧模式仍在、行数缩水是合并去重而非数据丢失。
    echo "趋势表节数: $(grep -c '^## 跨会话趋势表' "$PATTERNS")"   # 应为 1
    wc -l "$PATTERNS" | awk '{print "文件行数: " $1}'                # 大幅缩水=合并去重，属正常维护
    grep -c '^### 51' "$PATTERNS" >/dev/null && echo "✅ 旧模式抽查OK" || echo "⚠️ 旧模式#51未找到（检查grep写法：模式格式是「### 51.」而非「* **#51」）"
fi
```

注意：**趋势表「日期」列用完整格式 `| 2026-MM-DD |`**（9/24 实测确认，旧版 skill 曾误记「用短日期如 6/26」——短日期只出现在「来源数据/说明」列的自由文本里，如「7/6发布数据review」）。审计 grep 请以 `^| $FULL ` 为准，短日期仅兜底。注意macOS的grep不支持`-P`（Perl兼容正则），用`grep -E`代替；统计最高模式编号用行首锚定 `grep -oE '^### [0-9]+'`（8/4修正：`#[0-9]*` 会误匹配正文中的 #5002 等 token）。

**⚠️ 旧模式抽查基准是 #51，不要额外探测 #1（8/11 教训）：** data-feedback-patterns.md 的编号模式节（`### N.` 行首格式）从 **#51 开始**——#1-#50 只作为历史趋势表行与正文引用存在，文件里没有 `### 1.` ~ `### 50.` 的独立节。审计 Step 3 脚本里 `grep -c '^### 51'` 就是旧模式抽查的正确基准。若自行加探测 `grep '^### 1\.'` 会得到「未找到」的假警报，浪费排查时间——那不是数据丢失，是文件本来就这种结构（8/11 实测：`### N.` 最小=51，最早节是 `### 51.`）。

### Step 4: 第3层实操审计 — 验证是否真的查了DB

**⚠️ 存档第3层措辞必须含审计信号词（8/15实测）**：Step 4 用 grep 在存档第3层匹配 `关联类型/时间线补充/人物呼应/新角度/纯重复/无关联/旧记录` 等信号词。如果存档第3层表格用了自由措辞（如「旧文多」「原事件」「同源」）而没用标准信号词，即使实操确实查了DB（本次写稿阶段执行了26次API search），grep 计数仍不足导致 FAIL。**修复：第3层表格加「关联类型」列，每行用标准词标注（新角度/时间线补充/人物呼应/纯重复/无关联）。** 8/15补跑存档第3层原表格只有「第3层结论」列→Step 4 FAIL；补「关联类型」列后 8/10 信号 PASS。

**cron模式（用 terminal + sed/grep）：**

```bash
ARCHIVE=~/.hermes/daily-reviews/$(date '+%Y-%m-%d').md
SIGNALS=("旧记录" "ID " "已入库" "同人物" "关联类型" "时间线补充" "人物呼应" "新角度" "纯重复" "无关联" "DB命中" "条(")
FOUND=0
# sed range: use content patterns that match actual archive headings.
# Archive uses "四、价值建议（第2层）+ 跨时间关联（第3层）" and "五、发布数据回顾（2026-07-03）"
# As a safer fallback, also try just searching the whole file if sed range returns nothing.
# ⚠️ 9/2实测：sed 区间提取（/价值建议.*跨时间/,/发布数据回顾/p）在 macOS BSD sed 上对含中文/全角符号的存档头
# 可能匹配失败 → section_content 为空；且 `count=$(grep -c ...)` 在无匹配时可能输出空串，导致
# `[ "$count" -gt 0 ]` 报 "[: : integer expression expected"，全部信号误判 FAIL。
# 修复：直接整文件信号计数 + 空值防御（c=${c:-0}）。9/2 用此稳健版实测 9/10 信号 PASS。
for sig in "${SIGNALS[@]}"; do
    c=$(grep -c "$sig" "$ARCHIVE" 2>/dev/null || true)
    c=${c:-0}
    if [ "$c" -gt 0 ] 2>/dev/null; then FOUND=$((FOUND + 1)); fi
done
echo "第3层DB查询信号: ${FOUND}/${#SIGNALS[@]}"
[ "$FOUND" -ge 2 ] && echo "✅ PASS" || echo "❌ FAIL: 无DB查询痕迹"
```

## 完整执行顺序

```
review正文完成
  → write_file 写入存档（第1步）
  → [AUDIT] Step 1: 数据层审计（verify rewritten_title 区分）
  → [AUDIT] Step 2: 存档文件审计（verify 各层都存在）
  → [AUDIT] Step 3: 模式文件审计（verify data-feedback-patterns.md 已更新）
  → [AUDIT] Step 4: 第3层实操审计（verify 真的有去查DB）
  → 如果任何一步 FAIL：修复后重跑 audit（最多3轮）
  → 全部 PASS → segment-send 输出到用户
  → 标注存档路径
```

## 注意事项

- **cron模式禁止 execute_code** — 所有审计步骤必须用 `terminal()` + 内联 Python/Python3 完成的 shell 命令实现。`from hermes_tools import` 在 cron 模式下会被 BLOCK。审计脚本中所有 `execute_code` 示例代码应替换为等价的 shell 脚本。
- **禁止将审计结果写成一个独立文件** — audit结果直接影响是否输出最终响应
- **定时任务没有用户在旁纠正** — audit不通过就不要输出，因为没有第二轮机会
- **Step 1 的数据层审计不能自动pass/fail**，agent需要自己判断第4层分析中是否真的区分了原始title和rewritten_title。如果没区分，必须先补写第4层。
- **Step 2 「实际数据」子项 grep 的是字面「阅读」（9/12教训）** — 若存档第4层反馈表列头写「实际数据」而数值为裸数字（如 `3699→39311→97990@156.4h`），此子项会误报「❌ 实际数据 缺失」。这是**子项噪音，不影响 Step 2 总判定**（总判定只看 全量素材一览/价值建议/跨时间关联/发布数据回顾 四项是否齐全）。消除噪音的办法在存档侧：第4层表格列头写「实际数据（阅读/互动）」或数值旁标「阅读」。9/12 实测改列头后通过。
- **Step 4 是专门堵"第3层写了标题但没实际查DB"的坑**。如果第3层只有「第3层：待查询」或只写了个人物名但没有「旧记录」「ID」「关联类型」等实操痕迹，audit会直接fail。
- 如果audit fail，补写缺失部分后需要**同时更新存档文件**（write_file）和 **data-feedback-patterns.md**（如果第4层是新补的）。只修一个不修另一个等于没修。
- **Step 2 的改写标题检测使用 or 逻辑**（表格式"实际发布标题"或内联式"rewritten/原始title"）。如果存档使用内联描述式区分了原始title和rewritten_title，但Step 2仍标记"改写标题 缺失"，请检查存档中是否出现了"rewritten"或"原始title"字符串。如果确认存在但grep仍然不匹配（如第4层用「rewritten_title字段」而非"rewritten"），请手动跳过此单项。
