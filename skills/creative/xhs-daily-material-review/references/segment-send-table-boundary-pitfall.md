# segment-send 表格边界陷阱（6/26经验）

## 问题症状

通过 `segment-send.py` 发送的存档，部分表格显示为纯文本 `|` 管道符，但 API 返回的 JSON 中 `type: table` 正确。

## 根因

**根本原因不是表格格式，而是分段边界切开了表格。**

当存档文件（~15KB）被分段发送（每段 ~3800 chars）时，分段控制逻辑在行级切分。
若切分点恰好落在表格中间（表头在段A、数据行在段B），`sendRichMessage` 收到仅半张表，
无法渲染为 `type: table` block，退化为纯文本。

## 排查路径

1. 最初以为是 Markdown 格式问题（`**粗体**` vs `## 标题`、前导空格、缺分隔行）
2. 加了 `fix_tables()` 修复格式后问题仍在
3. 检查 API JSON 返回 → `type: table` 正确，说明格式没问题
4. 检查分段边界 → 段尾是 `|---|...`（分隔行），段首是 `| 1 | 板野友美...`（数据行）
5. **关键发现：** `current.strip().split('\n')[-1]` 去掉了尾部空行，
   `|---|...` 所在行被空行覆盖，检测不到表格边界

## 修复方法

### 修复1：表格边界检测（用扫全段代替取最后一行）

```python
# 错误做法：strip() 去掉了尾部空行
current_last_line = current.strip().split('\n')[-1]
current_is_table = current_last_line.strip().startswith('|')

# 正确做法：从下往上扫整个段，找最后一条 | 行
current_lines_list = current.split('\n')
current_last_table_line = -1
for j in range(len(current_lines_list) - 1, -1, -1):
    cl = current_lines_list[j].strip()
    if cl.startswith('|'):
        current_last_table_line = j
        break
current_has_table_end = current_last_table_line >= 0
```

### 修复2：混合行检测（不以 | 开头的表格行）

存档中可能出现 `S级（强烈推荐）🔥 | ID | 标题 | |----|------|------|` 这种混合行。
它不以 `|` 开头，但包含文本+分隔符混排。

检测方法：不以 `|` 开头、包含 `|`、且有一个分隔符部分（`---`）。

```python
# 对每个 | 分割的部分
for part in stripped.split('|'):
    if re.fullmatch(r':?-{3,}:?', part.strip()):
        sep_in_line = True
        break
```

### 修复3：表格整体后移

当检测到表格在边界时，从当前段中找出**第一个 | 行之前的非 | 行**，表格部分整体移到下一段：

```python
table_first_row = -1
for j in range(len(current_lines_list) - 1, -1, -1):
    cl = current_lines_list[j].strip()
    if not cl.startswith('|') and not cl.startswith('|---'):
        table_first_row = j + 1
        break
    if j == 0:
        table_first_row = 0  # 整段都是表格

if table_first_row > 0:
    # 表格前的内容先发
    before_table = '\n'.join(current_lines_list[:table_first_row])
    table_part = '\n'.join(current_lines_list[table_first_row:])
    send_msg(before_table)
    current = table_part + '\n' + next_line  # 表格+下一行合并
```

## 预防

最优解是在写存档时避免表格跨段：
- 单个表格不要超过 25 行
- 大表用列表代替
- 写存档时预留分段意识（3800 char / 段）
