# 自动发布链路

> 调查时间 2026-10-05 · 远端分支 `dev2` · 结论均有代码行号支撑（文件路径相对 `/Users/user/PG/XiaohongshuSkills`）

## 总览

```
Web UI 按钮 (web/app.py:1534 runTask)
  └─ POST /api/trigger-publish                    web/app.py:174
       ├─ _publish_running 全局互斥锁（同时只允许一个发布任务）
       ├─ 分支A：有 publish_method='export' 的稿
       │    └─ xhs_publish_story.py <key> --export（串行，每篇 timeout 120s）→ 只填充，人工点发布
       └─ 分支B：普通发布
            └─ yahoo_news_publish.py --auto --force --reuse-existing-tab [--post-time]
                 └─ 子进程 publish_pipeline.py --title --content --images …（timeout 180s，单实例锁）
                      └─ XiaohongshuPublisher (cdp_publish.py) → CDP :9222 → Chrome → 创作者中心
```

**没有任何定时任务会触发发布。** 已核对 `~/Library/LaunchAgents/com.xhs.*.plist` 全部 10 个任务，无 publish 相关；`agent_runner.py` 主循环也只到「选稿 + 飞书通知」（Phase 3.5 把 `publish_xhs` 置 1），不调发布。发布永远由人触发：Web UI 按钮 / MCP 工具 / 手敲命令。

MCP 侧对应工具：`mcp_servers/xhs_operations_server.py:527` `xhs_trigger_publish`（内部就是 POST 这个接口）。

## 分层细节

### 1. 接口层 `web/app.py:174-230`

- 先取 `_publish_lock`，若 `_publish_running` 为真直接返回 `{"locked": true}`
- 分配 task id（`_task_counter` + 锁保护）→ 起 daemon 线程 → 立即返回 `{"task_id": tid}`
- 前端轮询 `/api/task/<tid>` 取状态与日志；`/api/task/<tid>/stop` 实际执行 `proc.kill()`
- 分支判定：`get_pending_export()` 返回非空 → 走分支 A

### 2. 任务执行器 `web/app.py:88` `_run_task`

- `subprocess.Popen(cmd, cwd=<项目>/scripts, env=…)`
- 逐行读 stdout：写内存 `_tasks[tid]` + 追加落盘 `data/logs/task_YYYY-MM-DD.log`
- `proc.wait(timeout=7200)`（2 小时），结束后调用 `on_done` 释放锁

### 3. 发布主脚本 `scripts/yahoo_news_publish.py`

`--auto` 是「无人值守」的开关——`main()` 中直接 `choice = "y"`，跳过逐条交互确认。

- **前置检查**：`check_proxy()` / `check_chrome_cdp()`，CDP 不通直接返回
- **取稿**：`get_pending_pages()` → `get_pending_publish(50)`，条件为 `publish_xhs=1` 且 `xhs_pub_time` 为空，单次最多 50 条
- **正文组装**（按 `publish_mode`，`main()` 内）：
  | 模式 | 正文来源 | 标题来源 |
  |---|---|---|
  | `normal` | 引流摘要 + 新闻要点 + 我的解读 | `title` |
  | `rewritten` | `rewritten_content`（过 `_strip_markdown`） | `rewritten_title` |
  | `free` | `publish_free_text` | `title` |
  | `caption` | 视频短配文 `video_caption` | `title` |
- 末尾拼 `#tag`，最多 10 个（展开+必选+补足已在生成阶段完成）
- **跳过条件**：正文为空（`free`/`caption` 模式）、正文 > 1000 字（`_xhs_char_units`，中文/中文标点按 2、英文数字按 1）
- **媒体**：封面 + 图集（`get_page_media_blocks`）合并去重，最多 18 张；**有视频则忽略图集走视频模式**
- **排期**：优先该文章自己的 `xhs_pub_time`，否则命令行 `--post-time`
- **节流**：每条之间 `time.sleep(3)`

`publish_to_xhs()`（`:413`）负责调用 pipeline：
- 远程图片 URL 先下到 `/tmp/xhs_pub_<basename>`，再以本地路径传 `--images`
- 子进程 `publish_pipeline.py`，`timeout=180`
- **成功判定 = 子进程 `returncode == 0`**，随后用正则从 stdout 抓 `xiaohongshu\.com/explore/([a-f0-9]{24})` 得到 note_id
- 成功且拿到 note_id → `update_news(key, {"xhs_note_id": …, "xhs_title": full_title})`；然后 `mark_as_published()` 记录发布时间
- **降级路径**：stderr 含 `All image downloads failed` 且是图文 → `fetch_article_image()` 重抓封面再发一次

### 4. 编排层 `scripts/publish_pipeline.py`

- 进程级单实例锁 `single_instance("post_to_xhs_publish")`（与接口层 `_publish_running` 构成双保险）
- Step 1 `ensure_chrome()`：本地模式拉起带 `--remote-debugging-port=9222` 的 Chrome（headless/headed、按账号隔离 profile）
- Step 2 `connect()` + `check_login()`：未登录且 headless 时**自动重启为有窗口模式**打开登录页扫码；远程模式或 `CDP_NO_LOGIN_FALLBACK` 时打印 `NOT_LOGGED_IN` 退出
- Step 3 媒体准备：URL → `ImageDownloader`；本地路径走 `_verify_local_files_exist`
- Step 4 `publisher.publish(...)` 填表 → `_select_topics()` 写话题标签 → 打印 `FILL_STATUS: READY_TO_PUBLISH`
- Step 5 **默认就点发布**（仅 `--preview` 时跳过）→ `_click_publish()` → 打印 `PUBLISH_STATUS: PUBLISHED`
- 末尾 `publisher.disconnect()` + 临时文件清理

### 5. 浏览器自动化 `scripts/cdp_publish.py`（5835 行）

`XiaohongshuPublisher`（`:330`）全程走 CDP WebSocket + `Runtime.evaluate` 注入 JS，**不使用 Selenium/Playwright**。

`publish()`（`:5018`）六步：

1. 先发 `Page.handleJavaScriptDialog` 吞掉上次残留的 beforeunload 弹窗，再 navigate 到 `creator.xiaohongshu.com/publish/publish?source=official`
2. `_click_image_text_tab()`（`:4475`）按文字「上传图文」点 tab
3. `_upload_images()`（`:4530`）用 `DOM.setFileInputFiles` 塞 `.upload-input`，再 `_wait_for_uploaded_images()`（`:4140`）轮询 `.img-preview-area .pr` 数量确认真的传完
4. `_fill_title()`（`:4669`）：**用原生 value setter + 派发 input/change** 绕过 Vue 受控组件，并先 `removeAttribute('maxlength')`
5. `_fill_content()`（`:4697`）：依次探测 TipTap / ProseMirror / Quill（`_find_content_editor_selector()` `:4181`），清空后逐行建 `<p>`（空行插 `<br>`）
6. `_set_schedule_post_time()`（`:4769`）：定时发布时点 `.post-time-wrapper .d-switch` 并填日期

`_click_publish()`（`:4961`）的实战要点：
- `_wait_for_publish_button_ready()`（`:4365`）等按钮可用（视频模式还要等转码）
- **注入 JS 覆盖 `document.visibilityState` / `hidden`**，让 Vue 的点击 handler 在无显示器 / VNC 断开时也能触发
- `scrollIntoView` 后取 `xhs-publish-btn` 矩形中心，用 `Input.dispatchMouseEvent` 发**真实鼠标事件**（不是 `el.click()`）
- 等 5s 后从页面抓 `a[href*="xiaohongshu.com/explore"]` 或 24 位 hex，作为 note_link 返回

### 抗改版设计

- 所有选择器集中在 `SELECTORS` 字典（`:126`）。小红书改版时只改这里（`SKILL.md` 也是这么写的）
- 编辑器多选择器探测：`div.tiptap.ProseMirror` → `div.ProseMirror[contenteditable]` → `div.ql-editor`
- `--timing-jitter`（默认 0.25，上限 `MAX_TIMING_JITTER_RATIO=0.7`）给每步 sleep 加随机抖动，模拟人类节奏以降低风控概率
- 登录状态本地缓存 12 小时（`DEFAULT_LOGIN_CACHE_TTL_HOURS`）

## 关键参数速查

| 项 | 值 | 位置 |
|---|---|---|
| 单次取稿上限 | 50 条 | `get_pending_publish(50)` |
| 正文上限 | 1000 字（中文按 2 计） | `yahoo_news_publish.py` main |
| 标题截断 | 20 单位 | `_xhs_title_truncate` |
| 配图上限 | 18 张 | `publish_to_xhs` |
| 单篇发布子进程超时 | 180s | `publish_to_xhs` |
| 整个任务超时 | 7200s | `_run_task` |
| CDP 端口 | 9222 | `publish_pipeline.py` |
| 定时发布窗口 | 未来 14 天内 | `validate_schedule_post_time` |

## 「自动」的边界

1. **分支 A（`publish_method='export'`，长文）是半自动**：跑完打印「✅ 长文发布准备完成，请在浏览器中微调后手动点击发布」，**不点发布按钮**。
2. **分支 B 才是端到端自动**：选稿 → 填表 → 点发布 → 回写 note_id 全自动。
3. **上游只自动到选稿**：`agent_runner` 的 Phase 3.5 用 LLM 选稿并标记 `publish_xhs=1`，然后发飞书通知，**发布动作等人触发**。
