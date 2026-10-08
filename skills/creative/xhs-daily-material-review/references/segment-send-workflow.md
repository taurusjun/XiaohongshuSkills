# segment-send 脚本参考

## 位置
`scripts/segment-send.py` — 在 skill 目录下，与 skill 一起被加载。

注意：还有一个独立副本在 `~/.hermes/scripts/segment-send.py`，两者功能一致，但 skill 版本自带完整注释 + 参数说明。

## 功能
将 review 存档文件分段发送到 Telegram，使用 **sendRichMessage API**（`rich_message.markdown`），原生支持 Markdown 表格渲染。

**不支持 sendMessage 的 parse_mode**——那些只支持纯文本/HTML，不支持 `<table>`。

## 触发时机

**单点触发：** 脚本只在「每日素材 review 流程的 Step 5」被调用。

完整流程中的位置：

```
Step 1: 第1层 — 全量扫描 + 聚类 + 分级（layer1 skill）
Step 2: 第2层 — 价值建议 + 第3层 — 跨时间关联（layer23 skill）
Step 3: 第4层 — 发布数据回顾 + 模式更新（layer4 skill）
Step 4: write_file 存档 → ~/.hermes/daily-reviews/YYYY-MM-DD.md
Step 5: → python3 scripts/segment-send.py <存档文件路径>    ← 这里调
Step 6: 自查审计（xhs-review-self-audit skill）
```

## 调用方式

### 典型用法（cron prompt 中用绝对路径）

```bash
python3 ~/.hermes/skills/creative/xhs-daily-material-review/scripts/segment-send.py \
  ~/.hermes/daily-reviews/$(TZ=Asia/Tokyo date '+%Y-%m-%d').md
```

### 指定 chat_id（覆盖自动检测）

```bash
python3 scripts/segment-send.py output.md 8066707199
```

- `chat_id` 选填，不传时自动从 `~/.hermes/cron/jobs.json` 提取第一个 job 的 `origin.chat_id`
- 用户希望明确指定时，固定 chat_id = `8066707199`

### 指定最大字符数

```bash
python3 scripts/segment-send.py output.md 8066707199 3800
```

- 默认 3500，加 <chat_id> 参数时必须传
- 最大不应超过 3900（Telegram 限制 4000）

## 参数一览

| 参数 | 位置 | 必填 | 默认值 | 说明 |
|------|------|------|--------|------|
| `<file_path>` | 第1个 | 是 | — | 要发送的Markdown文件绝对路径 |
| `<chat_id>` | 第2个 | 否 | 从 cron/jobs.json 自动检测 | Telegram chat ID 数字 |
| `<max_chars>` | 第3个 | 否 | 3500 | 每段最大字符数（上限 3900） |

## 表格兜底修复（fix_tables）

脚本在发送前会对内容做一轮非标准 Markdown 表格修复。修复统计打印到 stderr。

### 修复类型

| 类型 | 触发条件 | 处理方式 |
|------|---------|---------|
| `bold_to_heading` | 表格前一行是 `**粗体标题**` | 改为 `## 粗体标题` |
| `trim_spaces` | `\|` 前有前导空格 | 去掉空格 |
| `split_mixed` | 表头行和分隔行在同一行（如 `S级 \| ID \| \|----\|------\|`） | 拆为标准两行 |

**注意：** 无分隔行的 `|` 格式目前不被处理——这种格式不是有效 Markdown 表格，先不转换。

### 表格边界保护（table boundary protection）

分段时如果检测到当前段末尾有 `|` 开头的表格行，且下一行也是 `|` 开头的表格行，
则把当前段中的表格部分（从第一个 `|` 行开始）整体后移到下一段。

**触发条件：** 当前段最后5行中存在 `|` 开头行 + 下一行也是 `|` 开头行。

**处理：** 从后往前找第一个非 `|` 行作为表格起点 → 起点前的文字先发 → 表格部分留着和下一段合并。

**6/26经验：** `current.strip().split('\n')[-1]` 不可靠（strip 会去掉尾部空行）。正确做法是遍历最后 N 行找所有 `|` 开头的行。参见脚本中的 `current_last_table_line` 检测逻辑。

### 匹配行检测

混合行修复特别注意「不以 `|` 开头但包含分隔符」的模式，如：
```
S级（强烈推荐）🔥 | ID | 标题 | 理由 | |----|------|------|
```
通过检测 `|` 分割的 parts 中是否同时包含纯文本和 `:---` 模式来判断。

## 实现要点

### API 选择（6/26经验：必须用 sendRichMessage，不存在HTML表格这条路）

**核心约束：Telegram Bot API 不支持在消息中渲染 HTML `<table>` 标签。** 
- `sendMessage` + `parse_mode=HTML` — `<table>` 标签的内容作为纯文本显示，不渲染为表格
- `sendMessage` + `parse_mode=Markdown` — 不支持 `##` 标题和 `|` 表格
- `sendMessage` + `parse_mode=MarkdownV2` — 所有特殊字符必须转义，一个漏转就 400 Bad Request
- `sendMessage` + `entities=` — 不支持标题和表格结构

**唯一可渲染表格的方式：`sendRichMessage`**（Bot API 10.0+）
- `sendRichMessage` + `rich_message.markdown` ✅ — Telegram 自动解析 Markdown 表格
  - 解析为 `type: table`, `is_header: true`, `bordered: true`, `striped: true`
  - 同时支持 `##` 标题、`**粗体**`、`*斜体*`、`- 列表`、`` `代码` ``、```代码块```等全部标准 Markdown
  - 不需要任何手动转换——直接传原始 Markdown 文本
  - 注意：`sendMessage` 和 `sendRichMessage` 是**不同的 API 方法**，有不同的参数结构

**关键教训（6/26）：** 不要尝试用 HTML `<table>` 糊弄。这条路不存在。直接传 Markdown 给 sendRichMessage 就行了。

### 分段策略
- 按行读取，逐行追加到当前段
- 达到 `MAX_CHARS` 时发送当前段
- 段间 `sleep(1)` 避免 rate limit
- 表格边界保护优先于字符数限制

### 网络（7/14修正：实际代理端口=20808）

- 优先走**直连**（手动 unset 所有 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY 环境变量）
- 失败时 fallback SOCKS5 代理 `socks5://127.0.0.1:20808`
- **修正原因：** 本环境设置的全局代理端口为 `20808`（SOCKS5），而非此前写入的 `10090`。7/14发现3008端口连接成功、10090 Connection Refused。segment-send.py 脚本中的硬编码端口已同步修正为 `20808`。
- **⚠️ 持续风险（7/20验证，7/21再验）：实际运行中的 `$http_proxy` 环境变量使用的端口可能与脚本硬编码值不同**。7/20时 `$http_proxy=socks5h://127.0.0.1:20809` 而脚本 hardcode 为 `20808`；7/21验证：`$http_proxy` 仍为 `socks5h://127.0.0.1:20809`，脚本 hardcode `20808` 再次返回 Connection Refused。**当前实际代理端口 = 20809，不是 20808。** 两者差距1，说明系统维护后端口有偏移但未同步更新脚本。代理端口可能在系统维护后被修改而不被通知。
- **应对：** 当 segment-send.py 报告 Connection Refused 时，先用 `echo $http_proxy` 或 `echo $HTTP_PROXY` 检查实际代理端口，然后改用手动内联 Python 发送（unset proxies + 正确端口代理），参考 `references/segment-send-timeout-fallback.md` 中的内联发送代码模板。
- 注意：`urllib.request.ProxyHandler` 会读取环境变量中的代理设置，需要显式 `os.environ.pop()` 清除才能跳过代理

## 配置来源
- bot_token：从 `~/.hermes/config.yaml` 的 `telegram.bot_token` 正则提取
- chat_id：优先级 ① 命令行参数 > ② cron/jobs.json 自动检测
- proxy：hardcoded `socks5://127.0.0.1:10090`

## 注意事项
- 脚本只读文件，不修改任何数据
- 发送失败时打印 stderr 但不中断流程
- 不要用 `~/` 缩写路径——用完整展开路径或 `$HOME`
- 在 cron prompt 里调用时用绝对路径引用脚本
- 表格边界保护是兜底方案——最优解是在写存档时直接输出标准格式，不把表格跨段
