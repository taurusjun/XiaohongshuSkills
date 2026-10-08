# Telegram sendRichMessage API

## 发现（2026-06-26）

原本 segment-send.sh 用 sendMessage + `parse_mode=Markdown` 发送 review 内容，但 Telegram 的 MarkdownV1 不支持标题（`##`）和表格。

尝试过：
- `sendMessage` + `parse_mode=Markdown` → **不支持表格**
- `sendMessage` + `parse_mode=MarkdownV2` → 需要复杂转义，且表格依然不是真正的表格
- `sendMessage` + `parse_mode=HTML` → 不支持 `<table>` 标签（`Unsupported start tag`）
- `sendRichMessage` + `html` 字段 → 格式不对（"must be non-empty"）
- `sendRichMessage` + `markdown` 字段 → 格式不对（"must be non-empty"）
- `editMessageText` + `rich_message` 字段 → 格式不对（"must be non-empty"）

**最终正确方式：** `sendRichMessage` + `rich_message.markdown` 字段

## 正确用法

```python
import json, urllib.request

payload = json.dumps({
    'chat_id': '<CHAT_ID>',
    'rich_message': {
        'markdown': '## 标题\n\n**粗体**\n\n| 列1 | 列2 |\n| --- | --- |\n| a | b |'
    }
}).encode()

req = urllib.request.Request(
    f"https://api.telegram.org/bot<TOKEN>/sendRichMessage",
    data=payload,
    headers={'Content-Type': 'application/json'}
)
```

## 支持的功能

sendRichMessage + markdown 字段会由 Telegram 自动解析为 RichMessage 结构：
- `##` / `###` → `type: heading` (size 2/3)
- `**粗体**` → `type: bold` (嵌套在 paragraph 文本中)
- `*斜体*` → `type: italic`
- 表格 `| |` → `type: table` 带 `is_header`、`is_bordered`、`is_striped` 属性
  - 表头行（第一行）自动标记 `is_header: true`
  - 每列 `align: center`（表头）或 `left`（数据行）
- `- 列表` → `type: list`（子项为 `type: list_item`）
- `` `code` `` → `type: code`（内联）
- ```代码块``` → `type: code`（块级）
- `> 引用` → `type: blockquote`
- `---` 分隔线 → `type: divider`

## 表格渲染的限制

**核心约束（发现于6/26）：** Telegram 消息本质上不支持 HTML `<table>` 标签。
如果你用 `sendMessage` + `parse_mode=HTML`，`<table>` 内容会显示为纯文本。
唯一可渲染表格的 API 是 `sendRichMessage`。

Telegram RichMessage 表格的 `|` 符号在行首时解析为表格，偏移时会解析为纯文本。

## 分段发送时表格完整性问题（6/26经验）

大存档文件（>3500字符）会被拆成多段发送。如果表格在段边界被切开，
`sendRichMessage` 收到的是表格的一部分，**不会渲染为表格**。

**问题表现：** 表头正确渲染（在第一段的末尾），数据行显示为纯文本 `|`（在第二段开头）。

**修复方法（segment-send.py 中实现）：**
1. 分段逻辑检测到当前段末尾是 `|` 表格行 + 下一行也是 `|` 表格行时
2. 把当前段中从表格起点开始的所有 `|` 行整体后移到下一段
3. 当前段只发送表格前的非表格内容

**检测方法：** 不能依赖 `current.strip().split('\n')[-1]`（strip 会去掉尾部空行）。
正确做法：遍历当前段最后 N 行，找所有 `|` 开头的行。

## segment-send.py 实现

见 `scripts/segment-send.py`。核心逻辑：
1. 读取 Markdown 文件
2. 运行 fix_tables() 兜底修复非标准表格（混合行拆分、前导空格、粗体→标题）
3. 按行拆分为不超过 MAX_CHARS 的段落（3500字符，留余量）
4. 分段时加表格边界保护——表格整体后移，不在段边界切开
5. 每段通过 `sendRichMessage` + `rich_message.markdown` 发送
6. 使用 SOCKS5 代理（127.0.0.1:10090），失败则直连

## 兜底修复 fix_tables()

脚本内置的表格修复函数处理以下非标准格式：

1. **混合行拆分**：`S级 | ID | |----|------|` → 标准表头行 + 分隔行
   - 专门检测「不以 `|` 开头但包含 `|` + 分隔符混排」的行
2. **前导空格**：` | a | b |` → `| a | b |`（所有 `|` 前空格被移除）
3. **粗体→标题**：`**发布方式分析：**` 后接表格 → `## 发布方式分析：` 后接表格
4. **分隔行推理**：无分隔行的 `|` 行块尝试推断并插入

**注意：** 无分隔行的 `|` 块目前不被修复——因为这类格式不是有效 Markdown 表格，很难推断列数和对齐方式。

修复统计在 stderr 输出（`🔧 修复了 N 个表格格式问题: bold_to_heading: X, trim_spaces: Y, split_mixed: Z`）。
