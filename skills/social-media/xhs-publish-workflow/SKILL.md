---
name: xhs-publish-workflow
description: 小红书发布工作流 — 从 DB 读取待发布队列统一处理，自动回写 note_id 和发布状态
---

# 小红书发布工作流

## 触发条件

用户要求将内容发布到小红书时，使用此工作流。

## 核心原则

0. **🔴 绝对禁止擅自启动发布** — 任何时候不得 POST /api/trigger-publish 除非用户明确说"发"或"可以发"。必须先查待发布队列展示给用户，等用户指令。

1. **永不关闭/重启用户已登录的 Chrome**
2. **走 REST API 发布** — 不直接调 `yahoo_news_publish.py`。所有发布通过 `POST /api/trigger-publish` 触发
3. **三步分离** — 改写→入库→设Flag→问询→发，每一步之间等用户确认。**绝不能在写入内容时顺手设 publish_xhs/publish_mode**
4. **发布前必须在 DB 中设好** `publish_xhs=1`（以及相关字段），API 内部自动读取并发布
5. **发布后自动回写** `xhs_note_id`、`xhs_title`、`publish_time`、`xhs_pub_time`，无需手动更新 DB
6. **先预览后入库** — 将改写内容展示给用户 review 后再写入 DB

---

## REST API 总览

Base URL: `http://127.0.0.1:5000`

### 获取文章列表

```
GET /api/news
```

| 参数 | 类型 | 说明 |
|------|------|------|
| publish_xhs | string | `pending` / `published` / `unpublished` |
| status | string | `active`（默认）/ `discarded` / `archived` |
| search | string | 关键词搜索 |
| page | int | 页码，默认 0 |
| page_size | int | 每页条数 |
| sort | string | 排序字段 |
| sort_dir | string | ASC / DESC |

### 获取单篇文章

```
GET /api/news/<key>
```
→ `{ "key": "...", "title": "...", "tags": "AKB48,乃木坂46", "publish_xhs": 0, ... }`

`tags` 是逗号分隔字符串，需自行 split。

### 更新文章字段

```
PUT /api/news/<key>
Content-Type: application/json
```

可更新字段（按分类）：

- **内容**: title, content, comment, summary, content_ja, rewritten_title, rewritten_content
- **元信息**: category, tags, status, format, story_type, is_long_form, format_suitability
- **图片/视频**: image_url, video_path, video_caption, gallery_images, gallery_url, publish_images, publish_video
- **发布控制**: publish_xhs, publish_time, xhs_pub_time, publish_mode, publish_method, publish_free_text, preselected
- **发布结果**: xhs_note_id, xhs_title
- **指标**: xhs_views, xhs_likes, xhs_saves, xhs_comments, xhs_shares, xhs_fans_gained, xhs_impression, xhs_click_rate, xhs_watch_time, xhs_danmaku
- **评分**: title_score, content_score

`tags` 可传数组或逗号字符串；gallery_images / publish_images 可传数组。

**标记待发布：**
```json
{"publish_xhs": 1}
```

**取消发布：**
```json
{"publish_xhs": 0}
```

---

## 查询发布队列（⚠️ 关键：API的publish_xhs参数不准确）

**前端判断逻辑：**
- **已发布** = `publish_xhs=1` **AND** `publish_time` 不为空
- **待发布** = `publish_xhs=1` **AND** `publish_time` 为空

⚠️ API的 `GET /api/news?publish_xhs=published` 只查 `publish_xhs=1`，**不区分 publish_time**，所以不准。必须用 sqlite3 直接查 publish_time。

### 查询待发布队列（发布前必须执行）

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT key, title, publish_mode, channel FROM news \
   WHERE publish_xhs=1 AND (publish_time IS NULL OR publish_time='') \
   ORDER BY updated_at DESC"
```

### 查询已发布记录（发布后可验证）

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT key, substr(title,1,40), substr(publish_time,1,16) FROM news \
   WHERE publish_xhs=1 AND publish_time IS NOT NULL AND publish_time != '' \
   ORDER BY publish_time DESC LIMIT 20"
```

### 统计数量

```bash
# 已发布
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT count(*) FROM news WHERE publish_xhs=1 AND publish_time IS NOT NULL AND publish_time != ''"

# 待发布
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT count(*) FROM news WHERE publish_xhs=1 AND (publish_time IS NULL OR publish_time = '')"
```

---

## 发布节奏盘点 & 「下一篇发什么」（用户问「我几天没发了」/「推荐几篇」）

**触发：** 「我有几天没发了」「这些天发什么好」「从这些天的文章里推荐5篇」「排期怎么安排」。

**先出数据再给结论**（这个用户对「先摆数据后给结论」敏感，不要先抛推荐）。

### 1. 已发布全量 + 断更天数

```bash
# published 端点 limit 上限 500，761 条要翻页；按 key 去重
ALL_PROXY="" curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?publish_xhs=published&limit=500" -o /tmp/pub1.json
ALL_PROXY="" curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?publish_xhs=published&limit=500&offset=300" -o /tmp/pub2.json
```

- **阅读量字段是 `xhs_views`**（不是 `xhs_read` / `xhs_view`）；实际发布时间字段是 `xhs_pub_time`。
- 最新真实发布 = `max(xhs_pub_time WHERE xhs_pub_time <= now)`，距今天数 = (now − 该时间)/86400。**必须先按 `<= now` 过滤**：未来定时条同样带 `publish_xhs=1` + `publish_time` 非空 + **未来的** `xhs_pub_time`，裸 `max(xhs_pub_time)` 会取到未来时间、算出负数「距今天数」或误报「刚发过」（10/6 实测：全库 max=10/6 22:23 是未来定时条，真实上次发布是 10/5 22:09，相差一个交易日）。查「本月每日实发量」同理加 `AND xhs_pub_time <= now`。
- ⚠️ **`xhs_pub_time` 已排定但 `publish_xhs` 未归位的条属于定时队列，不算已发布**（判定口径与上文「已发布 = publish_xhs=1 AND publish_time 非空」一致）。
- 输出「本月每日发布量」→ 直接暴露断更空档，这是用户问这句话时真正想看的东西。

**9/21 实测口径：** 最新发布 09-20 11:35（距现在 31.8h ≈ 1.3 天）；当日 0 条、定时队列 **0 条**；9月空档 9/7–9/11（5天）、9/14、9/17–9/18、9/21；实发 1~3 条/日 vs 待发候选 **55 篇** → 结论是**积压**，不是缺内容。

### 2. 待发候选池

```
近三日 · preselected=1 AND rewritten_content 非空 AND publish_xhs=0
```

按 `score_dims` 里的 `|X.X分|` 解析排序；`news-pass` 与 `|akb-top-bullet` 单列。

### 3. 按人物历史数据选篇（推荐必须挂数据）

对候选涉及的人物逐个 `search=<人名>&publish_xhs=published`，汇总 **已发条数 / 中位 xhs_views / 最高 xhs_views / 最近3条**。

**9/21 实测数据（可直接当基线复用）：**

| 人物/主题 | 已发条数 | 中位 | 最高 | 近期 |
|---|---|---|---|---|
| 板野友美 | 41 | 564 | **120,012** | 9/12=6,283、9/15=1,090 |
| 柏木由纪 | 32 | 491 | 13,068 | 8月 778~1,064 |
| 乃木坂（含成员） | 112 | 320 | 13,181 | 9/16=11,248 |
| 樱坂46 | 33 | 367 | 4,464 | 9/20=3,516 |
| 日向坂46 | 39 | 249 | 2,026 | 8/18=607 |
| 秋元康 | 21 | 299 | 5,165 | 8/19=1,156 |
| STU48 | 5 | 347 | 1,245 | 7/10=393 |
| 金村美玖 | 5 | 99 | 1,331 | 7/28=64 |
| 若月佑美 | 3 | 320 | 1,021 | 8/2=**27**（走低） |
| 反町隆史 | 0 | — | — | 新选题无先例 |

### 4. 推荐 = 人物历史数据 × 时效 × 分数，并给排期

- **时效性素材（台风/当天事件）排第一**——过夜就打对折，必须说明「今天就得发」。
- 人物历史数据最强的排第二（板野友美这类流量池）。
- 分数最高但本人近期走低的，标注为**质量牌而非流量牌**，别让用户误判预期（若月佑美 8.9 分但 8/2 只有 27）。
- **同人物 / 同题材不要挤在同一天**——9/12 单日发过两条板野友美（其中一条 6,283），但再堆会同题自我分流。
- 给「备选补位」1~2 条，不要只给 5 条就断。
- **5 篇的构成（10/7 立规）**：**4 篇「历史数据推荐」+ 1 篇「随机探索」**。第 5 篇从剩余候选里真随机抽（`random.sample`，不看分数/data），标注「随机探索」——防止推荐被既有流量池锁死。
- **预发布时间**：这 5 篇排到**明天**的 09:00/12:00/15:00/18:00/20:00，每篇随机前后偏移几分钟、**禁整点**。用独立脚本生成并落库：
  ```bash
  cd ~/.hermes/skills/creative/xhs-write-publish-flow/scripts
  python3 pub_time_plan.py --keys <K1>,<K2>,<K3>,<K4>,<K5> --apply   # 顺序对应 9/12/15/18/20
  ```
  只写 `xhs_pub_time`+`publish_xhs=1`（保留 `publish_method`/`publish_mode`/`related_keys`），**绝不触发发布**。完整 spec 见 `xhs-write-publish-flow/references/publish-recommendation-step.md`。

命令级脚本与完整输出格式见 `references/publish-cadence-audit.md`。

---

## 待发布队列字数上限：900字左右（用户口径，10/5 确立）

**用户指令：「待发布超过900字的都需要改成900字左右」。** 凡挂入待发布队列（`publish_xhs=1`）的稿子，正文一律压到 **约900字（落 880–910）**。

这是**发布口径**，与写稿阶段「story 正文不设上限、以密度为准」不冲突——写稿时不压，**挂队列前**再压。设置待发布（`publish_xhs=1`）后紧跟这一刀，别等用户来提。

### 要点

1. **范围**：`publish_xhs=1 AND (publish_time IS NULL OR publish_time='')` 里**所有** >900 的都要过，含 900±10 边缘（905、939 也算）。目标全部落进 880–910。
2. **压缩方式＝编辑性压缩，不是重写**：保留开场钩子/核心引语/时间线/关键数字/具体收束；砍冗余修饰、重复情绪句、次要配角发言。只改字数，不改标题、不换叙事线。改成「评论腔」＝压过头，回退。
3. **必须逐篇重跑闭环**：`normalize-dunhao.py` → `batch_precheck.py`（story ≥800字 + `##`≥2 + 假名≤5 + 顿号行0）→ `renwei-pre-commit.py`（exit 0/2）→ `sqlite3 UPDATE` 写回（**保留 `publish_xhs=1` + `publish_mode='rewritten'` + `related_keys`**）→ `score_dims` 覆盖落盘 → 复核 `len(rc)-rc.count('\n')`。
4. **深访压到900会跌破密度30%**（ja 3k+ → 22–27%）：用户指定字数优先于密度红线，**不得加字回去**；在 `score_dims` 理由里写明「密度XX.X%（用户指定900字优先）」。
5. **坑**：把 DB 的 `rewritten_content` 当 draft 用 `split('\n')[1:]` 计字，会静默吃掉第一段（DB 正文首行不是标题行）——10/5 实测 `a1dbaa1d3a84` 被少算 130 字。压稿文件首行必须补 `## 标题`，否则 precheck 报「缺首行」。

完整命令级流程、10/5 台账模板与全部坑见 `references/trim-publish-queue-to-900.md`。

---

## 发布前：批量下载图集（gallery-download）

**触发**：待发布队列定稿后、准备配图时。

```bash
cd ~/.hermes/workspace
./gallery-download.py                 # 待发布队列（publish_xhs=pending）全部，已有缓存的跳过
./gallery-download.py --force         # 强制重下（含已有缓存）
./gallery-download.py --keys K1,K2    # 指定 key（逗号/空格分隔）
./gallery-download.py --no-wait       # 只触发，不等结果
./gallery-download.py --timeout 300   # 单篇等待上限秒（默认 180）
./gallery-download.py --json          # 机器可读输出
```

- **底层**：`POST /api/gallery-download/<key>`（body `{"gallery_url":""}`，留空则用 DB 的 `gallery_url`，没有则从原文链接检测）→ 轮询 `GET /api/gallery-status/<key>` 直到 `done`/`error`。
- **落盘**：`~/.cache/xhs_images/<key>/`。**只下载到本地，不做 Cloudinary 上传**（用户明确要求）。发布流水线读 `image_url` + `publish_images` 两个字段取图。
- **退出码**：0=全部成功，1=有失败，2=队列/服务错误。默认跳过已有缓存的（幂等，可反复跑）。
- **常见失败** `error: 未检测到图集链接`：源文（多为 Yahoo 转载页）没有图集页，只有封面 `image_url` —— 属预期，该篇配图走封面即可，不影响其余篇目。

---

## 发布流程（完整分步）

### Phase 1: 改写内容 → TG 预览

```
Step 1: 生成改写后的标题 + 正文
        → 保持 raw markdown（保留 ## 小标题）
        → **不要在正文中包含任何 tags**
        → 写入 /tmp/ 文件
Step 2: 用 MEDIA: 语法发送到 Telegram 给用户 review
        → 用户确认内容 OK
```

### Phase 2: 写入 DB（只写内容，不设发布标记）

```
Step 3: 写入 DB 的 rewritten_content 和 rewritten_title
        → **只更新 rewritten_title，绝不更新 title**（title 保留源新闻标题）
        → **不要设置 publish_xhs**
        → **不要设置 publish_mode**
        → **不要设置 publish_time / xhs_pub_time**
Step 4: 告知用户已入库
```

### Phase 3: 用户确认后设置发布参数

```
Step 5: 用户指定时间（或不指定=立即）后
        → 设置 publish_xhs = 1
        → 设置 publish_mode = 'rewritten'
        → 设置 xhs_pub_time = 用户指定的时间（可选，空=立即）
        → **不要碰 publish_time** — 系统自动填写
Step 6: 向用户确认是否可以发布
```

### Phase 4: 触发发布

```
Step 7: 用户说"发"后
        → POST /api/trigger-publish（不传post_time=立即，传了=定时）
Step 8: 轮询 GET /api/task/<task_id> 直到 status=="done"
```

### 使用 Python 写入（推荐，避免 update.sh 转义问题）

```python
import requests
r = requests.put('http://127.0.0.1:5000/api/news/<key>',
    json={'rewritten_title': '标题', 'rewritten_content': '正文'})
print(r.json())
```

update.sh 对含特殊字符的长文本有转义问题，建议长文本用 Python + requests 写入。

### 触发发布

```bash
# 立即发布
curl -X POST http://127.0.0.1:5000/api/trigger-publish \
  -H "Content-Type: application/json" -d '{}'

# 定时发布
curl -X POST http://127.0.0.1:5000/api/trigger-publish \
  -H "Content-Type: application/json" \
  -d '{"post_time": "2026-06-10 18:00"}'

# 查看任务状态
curl -s "http://127.0.0.1:5000/api/task/0" | python3 -m json.tool
```

**前置条件**：文章必须满足 `publish_xhs=1` AND `publish_time=NULL` AND `status='active'`

**内部逻辑**：
- 若有 `publish_method='export'` 的文章 → 走长文导出流程（需手动在浏览器确认）
- 否则 → 自动发布 `publish_method='post'`（或 NULL）的待发文章

---

## 任务管理

### 查询任务状态

```
GET /api/task/<task_id>
```
→ `{"status": "running"|"done"|"error: ...", "log": "..."}`

### 查询所有运行中任务

```
GET /api/active-tasks
```
→ `{"active": [{"task_id": "5", "status": "running", "log": "..."}], "fetch_running": bool, "publish_running": bool}`

### 强制终止任务

```
POST /api/task/<task_id>/stop
```
→ `{"ok": true}`

直接 SIGKILL 子进程，任务状态变为 `error: 用户终止`。

### 终止发布流程

```
1. GET  /api/active-tasks               → 拿到 task_id
2. POST /api/task/<task_id>/stop        → 强杀
```

### 已知问题：话题标签（topic tag）选择CDP超时

发布流水线在 `Step 4.1: Selecting N topic tag(s)` 阶段经常超时：
```
Error during form fill: Timed out waiting for CDP response to Runtime.evaluate after 15.0s.
```

**实际情况：** 图片已上传、标题已填入、正文已填入，只有话题标签选择未完成。pipeline退出后编辑页面内容依然保留（不关闭浏览器标签页），可以手动在浏览器中补充标签后点发布。

**应对策略：**
- 如果发布失败在 topic tag 步骤，不要立即重试 `POST /api/trigger-publish` → 会重新导航刷新页面，导致已填入的内容丢失，甚至登出session
- 正确做法：保留当前浏览器页面不动，手动在浏览器里选完标签后点"发布笔记"
- 如果必须重试：先保存 rewritten_content 到 DB，再触发发布（pipeline会重新填表）
- ⚠️ 连续重试会导致Chrome session过期（登录态丢失），需要重新扫码登录

### Publish lock 卡死处理

如果 `POST /api/trigger-publish` 返回 `{"locked": true}`，但进程已死：

1. 检查 `ps aux | grep -i publish` 确认没有实际在运行的进程
2. 清除残留文件：`rm -f /tmp/publish_xhs_1_keys.txt /tmp/publish_xhs_1_left.txt`
3. 重启 Flask:
   ```bash
   kill -TERM $(lsof -ti :5000) 2>/dev/null; sleep 2
   cd ~/PG/XiaohongshuSkills && .venv/bin/python web/app.py &
   ```
4. **不要用 sudo pkill 或 sudo 命令** — 用户极其反感

⚠️ `_publish_running` 是 Python 进程内全局变量，kill + 重启是唯一可靠解法。

### 发布取消流程

1. **立即** `PUT {"publish_xhs": 0}` 取消标记
2. 检查是否已有发布进程在运行：`ps aux | grep -i publish | grep -v grep`
3. 如果有 → `kill <pid>` 杀掉子进程
4. 查询 `GET /api/active-tasks` 确认已停止

⚠️ 仅设 `publish_xhs=0` 可能来不及阻止已在运行的发布进程——必须同时 kill 进程。

⚠️ `publish_xhs=1` 是实际触发发布的标记。`preselected=1` 只做标记，不影响发布队列。

---

## 发布模式说明

| 模式 | 字段 | 说明 |
|------|------|------|
| `normal`（默认） | summary + content + tags | 新闻转小红书，管道自动拼接 |
| `rewritten` | rewritten_content | 已手动改写的长文/故事 |
| `free` | publish_free_text | 完全自定义正文 |
| `caption` | video_caption | 视频配文 |

## 图文 900 字 vs 长文模式（`publish_method`）— 2026-10-05 立规

**图文笔记正文 ~900 字（硬上限 ~1000），超了会被截断；长文模式字数不限、需手动在浏览器确认。**

| 字段 | 图文（默认） | 长文 |
|------|------|------|
| `publish_method` | `'post'` | **`'export'`** |
| 正文字数 | **≤950**（目标 900±50） | **不限**（实写 1,000–2,200） |
| 触发流程 | 自动发布 | 长文导出流程（**需手动在浏览器确认**） |
| 配额 | 主体 | **无配额**——按分流判据，够格就走长文 |

**分流判据（机械，不靠感觉）：** 密度红线 `正文 ÷ content_ja ≥ 30%` ⇒ **900 字能达标 ⟺ 原文 ≤ 3000 字**。

- `format=news` → 图文，≤900
- `story` 且 content_ja **≤3000** → 图文，**900±50**
- `story` 且 content_ja **>3000** → **长文 `export`**，按 原文×0.31–0.33 写足，**禁止压到 900**
- 人物弧跨年（偶像生涯史／家族口述／争议深度稿）→ 即使原文略低于 3000 也优先 `export`
- 事件报道/名单/公告型且原文>3000 → 仍图文 900，密度不足注「豁免」

**设置方法（与内容分两步，遵守本文「关键约束」）：**
```python
# 内容已入库后，单独设发布参数
conn.execute("UPDATE news SET publish_xhs=1, publish_mode='rewritten', publish_method='export' WHERE key=?", (full40,))
```
长文稿同样 `publish_xhs=1`；`score_dims` 理由里写明「长文模式export不压字数」。

**历史数据：** 图文 `post` 771条均阅读 1,591／长文 `export` 19条均阅读 **1,776**（最高 12,983）——长文不吃亏，别为统一字数牺牲好长文。

详细判据/实作案例见 `xhs-write-publish-flow/references/longform-vs-900-routing.md`。

---

## 关键约束（违反即错误）

- **NEVER** 更新 `title` 字段 — 只更新 `rewritten_title`。`title` 保留源新闻标题
- **NEVER** 设置 `publish_xhs`/`publish_mode` 时和写入内容在同一个 UPDATE 中 — 必须分两步
- **NEVER** 在 `rewritten_content` 中包含 `#标签` — pipeline 从 DB tags 字段自动拼接
- **NEVER** 去掉 `rewritten_content` 中的 `##` markdown 标题
  - 补注（9/21）：`##` 是 **story 体裁**的结构要求。`news`（`format=news` / `is_long_form=0`）的正文本来就不带 `##`——结构由写稿阶段按 DB `format`+`is_long_form` 决定（见 `xhs-write-publish-flow`）。**发布阶段不要自行增删结构**：既不要删 story 的 `##`，也不要给 news 补 `##` 来「显得像长文」。
- **NEVER** 先写入 DB 再展示给用户 — 先预览，确认后再入库
- **NEVER** 手动设置 `publish_time` — 系统发布完成后自动填写
- **NEVER** 用 `sudo` 做任何操作

---

## 验证流程（写入后必须做）

```bash
# 查单条确认
curl -s "http://127.0.0.1:5000/api/news?search=<key>&limit=5" | \
  python3 -c "import sys,json; d=json.load(sys.stdin); r=d['rows'][0]; print(f'publish_xhs={r.get(\"publish_xhs\")}, mode={r.get(\"publish_mode\")}, rewritten={r.get(\"rewritten_title\")[:30] if r.get(\"rewritten_title\") else \"(空)\"}')"
```

---

## 关键字抓取

### 获取 keywords（先合并再传入）

```
Step 1: GET /api/keywords           → 预置词（来自 DB config 表）
Step 2: GET /api/custom-keywords    → 自定义词（来自 data/custom_keywords.json）
Step 3: 合并后传入 POST /api/trigger-fetch
```

### 触发抓取

```
POST /api/trigger-fetch
Content-Type: application/json

{
  "mode": "keywords",
  "keywords": [
    {"keyword": "AKB48", "max": 10},
    {"keyword": "乃木坂", "max": 5}
  ]
}
```

注意事项：
- keywords 为空会 fallback 到单词 `{"keyword":"AKB","max":5}`，不要传空数组
- 抓取完成后自动执行去重（dedup_today_candidates）
- 写入数据库时 `publish_xhs` 默认为 0，不会自动入发布队列

### 并发保护

抓取和发布各有独立的全局锁，同一时间只能运行一个任务。被拒绝时返回：
```json
{"locked": true, "msg": "已有抓取任务在运行，请等待完成"}
```

---

## 标签

```
GET /api/all-tags
```
→ `{"tags": ["AKB48", "乃木坂46", ...]}` — 全库出现 ≥2 次的标签

---

## 数据采集

发布后拉取 metrics：

```bash
cd ~/PG/XiaohongshuSkills && .venv/bin/python scripts/metrics_collector.py
```
