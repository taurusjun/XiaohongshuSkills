# 容器 Fetch（独立 Chrome）+ 定时任务

> 配套 [container-env.md](container-env.md)。记录 fetch 的独立 Chrome 设计与 cron 时间口径。

> ⚠️ **Yahoo 必须直连，严禁代理（区域限制，代理会被 block）**。fetch Chrome `--no-proxy-server`；Python `_direct_session`（`trust_env=False`）。

## 1. fetch 独立 Chrome（与主 9222 隔离）
- **主 Chrome**：端口 `9222`（`CHROME_FORCE_HEADED=1` + Xvfb）—— 登录 / 发布 / 「查看 Chrome」用。
- **fetch 专用 Chrome**：端口 `9333`、独立 profile `/data/chrome-profiles/fetch`、**headless**、**用完即关**。
  - 参数：`--no-proxy-server --headless=new --blink-settings=imagesEnabled=false --disable-accelerated-2d-canvas --disable-gpu --disable-dev-shm-usage --window-size=1440,900 --lang=zh-CN`
  - 目的：直连 + 跳过图片渲染（省 CPU）+ 不污染主实例与登录态。
- 代码：
  - `scripts/chrome_launcher.py::launch_fetch_chrome / kill_fetch_chrome`
  - `scripts/yahoo_news_auto_sqlite.py`：main 里 `launch → 抓取 → finally kill`
  - `scripts/yahoo_common.py`：CDP 端口读 `XHS_FETCH_CDP_PORT`（默认 9222；fetch 脚本 `setdefault 9333`）
- ⚠️ **profile 属主必须是 user**：正常由 user 进程创建；若用 root 跑过测试会变 root 属主 → user 起的 Chrome 打不开端口（fetch 空跑）。修：`chown -R user:user /data/chrome-profiles/fetch`。
- headless 无窗口 → **noVNC 看不到 fetch 的页**（只有 CDP 页面）；主 Chrome 才有窗口。
- 环境变量（`ops/xhs.env`）：`XHS_FETCH_CDP_PORT=9333`、`XHS_FETCH_PROFILE=/data/chrome-profiles/fetch`；flags 可用 `XHS_FETCH_CHROME_FLAGS` 覆盖。

## 2. 容器 cron 时间表（= 生产 + 1h）
| 任务 | 容器 | 生产 |
|---|---|---|
| fetch | **01:30** | 00:30 |
| review | **02:30** | 01:30 |
| write | **04:00** | 03:00 |
| metrics | 每小时 **:37** | :00 |
| reflection | **周一 00:00** | 周日 23:00 |
| db-backup | 每小时 **:47** | :17 |
| 重启主 Chrome(9222) | 每小时 **:05** | 每小时 :05（2026-10-17 新增，见 §6）|

- 定义在 `ops/crontab`（entrypoint 安装到 user crontab）。
- 所有 job **直接跑命令，无 `wait_idle`、无随机 sleep**（对齐生产）。
- ⚠️ 上表「生产」列的 **review 01:30 / write 03:00 实际没有定时任务**：宿主 `~/Library/LaunchAgents/com.xhs.*.plist`（11 个）与 `crontab -l` 里都**没有** review/write（2026-10-17 核对），自动化只到 `fetch-runner 00:30`；这两步在生产是人工/Web UI 触发。
- ⚠️ crontab 里 **`%` 必须转义成 `\%`**（如 `$(date +\%F)`）：否则 cron 把 `%` 当换行 → 命令被截断、**静默不执行**。（fetch 曾因 `RANDOM%1800` 未转义而从未执行。）

## 3. fetch 执行链
```
cron → fetch_runner.py（读 webapp keywords）
     → POST /api/trigger-fetch   （webapp 内存锁 _fetch_running 防并发；已锁则 fetch_runner 静默 exit0）
       → 子进程 yahoo_news_auto_sqlite.py → launch 独立 Chrome(9333) → 抓 → kill
     → 轮询 task 状态 → 飞书通知
```
- webapp 锁是**内存态**（随进程，重启清零）；异常残留用 `POST /api/admin/reset-fetch-lock` 复位。

## 4. 主 Chrome / 媒体 tab / mcp
- `ops/cdp_keeper.py`（每 60s）：若 www 标签停在 `/explore`（视频流、持续烧 CPU）→ 自动导航到该账户 profile 页（读 `data/accounts_meta.json` 的 `profile_id`；无则 `about:blank`）。
- 宿主 `~/cdp-keeper.sh`（每 300s，LaunchAgent `com.xhs.cdp-keeper`）同样做「`/explore` → 创作者首页」+ **关掉多余的 `chrome://newtab/` · `about:blank`（保留 ≥1 页）**（2026-10-17 补）。两个必须项：plist 带 **`AbandonProcessGroup=true`**（否则 launchd 在本任务退出时**杀掉**它拉起的 Chrome：日志写 `alive=1` 但端口随即又关）、脚本带 **`CHROME_PROFILE_DIR="Profile 1"`**（否则 Chrome 开到 `Local State.last_used`，可能不是有登录态的那个 profile）。
- **mcp（fastmcp :5001）已从 supervisord 移除**（曾因 SOCKS 代理崩溃重启循环，持续烧一个核 → 风扇）。

## 5. 定时重启容器（宿主 LaunchAgent）

容器无法重启自身，故由**宿主机** launchd 触发（与 `com.xhs.db-backup-monthly` 同机制）。

- 计划：**每天 23:00** 重启 `xhs` 容器。
- 脚本：`ops/container_restart_daily.sh`（宿主运行）
  - 健康检查 = 容器 `Running=true` **且** `http://127.0.0.1:15000/` 返回 200（每次等最多 ~90s 就绪）。
  - **失败自动重试 3 次**；3 次均不通过 → 调 `ops/notify_feishu.py` **发飞书告警**。
- 告警器：`ops/notify_feishu.py`（**仅标准库**，宿主机无 `requests` 也可用；读 `scripts/.env` 的 `FEISHU_APP_ID/SECRET/OPERATOR_OPEN_ID`，直连 `open.feishu.cn`）。
- LaunchAgent：`ops/com.xhs.container-restart-daily.plist` → 安装到 `~/Library/LaunchAgents/` 并 `launchctl load -w`。
- 日志：`logs/container_restart.log`。

安装/更新：

```bash
cp ~/PG/xhs-docker-src/ops/com.xhs.container-restart-daily.plist ~/Library/LaunchAgents/
launchctl unload ~/Library/LaunchAgents/com.xhs.container-restart-daily.plist 2>/dev/null
launchctl load -w ~/Library/LaunchAgents/com.xhs.container-restart-daily.plist
```

手动测试：

```bash
bash ~/PG/xhs-docker-src/ops/container_restart_daily.sh     # 真重启+健康检查
python3 ~/PG/xhs-docker-src/ops/notify_feishu.py "测试告警"  # 只测告警通道
```

## 6. 定时重启宿主主 Chrome(9222)（宿主 LaunchAgent）

参考容器 `ops/restart_main_chrome.sh`，宿主侧有两处**必须**不同（2026-10-17 实测确认）：

- 计划：**每小时 :05**（与容器同点）。
- 脚本：`ops/restart_host_chrome.sh`（宿主运行）
  - `pkill -f "[r]emote-debugging-port=9222"` → 只动 9222，不影响代理 Chrome(9223)。
  - 随后**立刻** `launchctl kickstart -k gui/501/com.xhs.cdp-keeper`：
    - 容器 keeper 是 60s 巡检，宿主是 **300s** → 不主动 kickstart 会有最多 5 分钟空窗；
    - **必须由 GUI 域的 keeper 去拉 Chrome**：从脚本自身会话直接起 Chrome 拿不到 macOS Keychain 的「Chrome Safe Storage」密钥 → cookies 只能放内存（`Profile 1/Cookies` 表 **0 行**）→ 一重启就丢登录。实测 GUI 域拉起后 cookies **落盘**（16~19 行），重启后登录仍在 ✅。
  - **护栏**：`GET /api/active-tasks` 的 `active` / `fetch_running` / `publish_running` 任一为真 → **跳过本次**。
    - 容器 fetch 用独立的 9333 Chrome，而**宿主 fetch 用的就是 9222** → 宿主更需要这道护栏（否则会在抓取中途把 Chrome 杀掉）。
- LaunchAgent：`ops/com.xhs.restart-main-chrome.plist`（`StartCalendarInterval` → `Minute=5`）。
- 日志：`~/.hermes/logs/restart-main-chrome.log`。

安装/更新：

```bash
cp ~/PG/xhs-docker-src/ops/com.xhs.restart-main-chrome.plist ~/Library/LaunchAgents/
launchctl bootout gui/501/com.xhs.restart-main-chrome 2>/dev/null
launchctl bootstrap gui/501 ~/Library/LaunchAgents/com.xhs.restart-main-chrome.plist
```

手动测试：

```bash
bash ~/PG/xhs-docker-src/ops/restart_host_chrome.sh     # 真重启 9222，并等端口回来（有任务在跑则跳过）
```

> 计划文档提到的「升级为 **RSS 超阈值才重启**」（比固定每小时更精准）仍是待实现项。
