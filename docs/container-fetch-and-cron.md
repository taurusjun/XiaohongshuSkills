# 容器 Fetch（独立 Chrome）+ 定时任务

> 配套 [container-env.md](container-env.md)。记录 fetch 的独立 Chrome 设计与 cron 时间口径。

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

- 定义在 `ops/crontab`（entrypoint 安装到 user crontab）。
- 所有 job **直接跑命令，无 `wait_idle`、无随机 sleep**（对齐生产）。
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
- **mcp（fastmcp :5001）已从 supervisord 移除**（曾因 SOCKS 代理崩溃重启循环，持续烧一个核 → 风扇）。
