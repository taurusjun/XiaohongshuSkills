# Docker 迁移方案：macOS → Linux 全量部署

> **状态：方案文档，尚未实施** · 编写于 2026-10-06
> 目标：把 `XiaohongshuSkills` + `Hermes` 从 macOS 迁到独立 Linux 服务器的 Docker，用 Xvfb 跑 Chrome，保留 admin UI，并新增扫码登录界面。

## Context

项目现在跑在一台 macOS 上（通过 VNC 操作）。目标是用 **Xvfb 虚拟显示**在容器里跑 Chrome，**新增扫码登录界面**（二维码传回 admin UI，手机扫码），之后正常发布。

### 迁移范围比预想的大：必须包含 Hermes

调查发现**写稿不是本项目做的，是 Hermes 在定时做**：

```
~/.hermes/cron/jobs.json
  每日3点写稿      0 3 * * *   enabled   ← 读当日 review → 写稿 → 直接 UPDATE news 入库 → 推荐5篇设待发布
  每日素材review   30 1 * * *  enabled
  Telegram health check  每10m  enabled
  UUMit 巡航 ×4    已禁用（保留）
```

且 **Hermes 与本项目是同机同路径强耦合**（技能里硬编码）：

| 引用 | 处数 | 用途 |
|---|---|---|
| `~/PG/XiaohongshuSkills/data/` | 37 | **直接 sqlite3 读写 `news_dev.db`**（技能明确写「优先用 sqlite3 直接写 DB，不走 API」） |
| `~/PG/XiaohongshuSkills/scripts/` | 8 | `cdp_publish.py` / `wechat_publisher.py` / `yahoo_news_auto.py` |
| `~/PG/XiaohongshuSkills/.venv/` | 3 | 用项目的 venv 跑脚本（`cd 项目 && .venv/bin/python scripts/xxx.py`） |
| `~/PG/XiaohongshuSkills/config/` | 2 | `wechat_conf.json` / token 缓存 |
| `127.0.0.1:9222` · `127.0.0.1:5000` | — | 技能直连 CDP 和 webapp API |

**结论：两者必须在同一台机器上、看到同一份文件、同一批端口。** 已确认：**Hermes 也迁，独立容器，全部一起迁**（UUMit/Telegram 都跟走）。

### 三个必须先接受的现实

| # | 现实 | 影响 |
|---|---|---|
| 1 | **Chrome cookie 跨平台加密不兼容**（macOS Keychain vs Linux 固定密钥） | 拷贝 profile **大概率仍需重新扫码一次**。正好由新做的扫码 UI 覆盖 |
| 2 | **出口 IP 从家宽变机房**，小红书强关联「设备+IP」 | **最大风险**，见风险 #1 |
| 3 | `ffmpeg`/`node`/`yt-dlp` **在 macOS 上没装**，`unified_media_downloader.py:195/209/280` 的裸 `subprocess.run(["ffmpeg",...])` 从未执行过 | Linux 装了依赖后**第一次真正运行**，需单独验证 |

---

## 〇、目标服务器实测（迁移目标机）

> 实测时间 2026-10-06 · 腾讯云 · root 登录

### 规格与现状

| 项 | 实测值 | 结论 |
|---|---|---|
| 主机 / 架构 | VM-0-10-rockylinux · x86_64 | ✅ |
| **OS** | **Rocky Linux 9.8**（RHEL 9 系，**非 Debian**） | ⚠️ 容器内是 Debian 镜像，**基本无影响**；宿主运维命令是 `dnf`/`systemctl` |
| CPU / 内存 | 4 核 / 7.5G（可用 **6.0G**） | ✅ Chrome+Xvfb(~1.5G) + Hermes 有余量 |
| 磁盘 | 178G，已用 47G，**可用 124G** | ✅ 分层备份最坏 14G 无压力 |
| Docker | **29.5.3 + Compose v5.1.4 已装** | ✅ |
| **SELinux** | **Disabled** | ✅ Rocky 上最烦的 bind mount relabel 坑不存在 |
| 时区 | CST | ✅ 与 Mac 一致 |
| 端口 5000/5001/9222 | **全空闲** | ✅ 无冲突 |
| 现有容器 | 0 个 | ✅ 干净起步 |
| 现有 nginx | 80 + 443 在跑 | ✅ 可给 admin UI 做反代 + TLS + 认证 |

### 出口网络矩阵（**这是最关键的实测**）

| 目标 | 结果 | 影响 |
|---|---|---|
| `creator.xiaohongshu.com` | **200** ✅ | 发布链路直连，**不需要代理** |
| `api.deepseek.com` | **401**（需 key，正常）✅ | 写稿 LLM 直连 |
| `news.yahoo.co.jp` | **200** ✅ | 新闻抓取直连 |
| `github.com`（HTTPS） | ✗ | 但见下 |
| `github.com`（**SSH 22 / 443**） | **已认证成功**（`taurusjun`）✅ | 月度备份 push 可直连，且**密钥已在服务器上** |
| `instagram.com` | **000** ✗ | 图集下载会失败 |
| `youtube.com` | **000** ✗ | 视频下载会失败 |
| `google.com` | **000** ✗ | `deep-translator` 翻译会失败 |
| `x.com` | **000** ✗ | Twitter 相关功能会失败 |

**结论：三条主链路（写稿 / 抓取 / 发布）全部直连可用；境外媒体与翻译需要代理。**

### ⚠️ 必须先解决的缺口：这台机器没有代理

实测**未发现任何代理进程或监听端口**（clash / mihomo / v2ray / xray / sing-box / privoxy / squid 都没有，7890/1080/8118 等常见端口也没开）。

受影响功能：**Instagram 图集下载 · YouTube 视频下载 · Google 翻译 · Twitter**。

三种落地方式（择一）：
1. **在这台服务器上装一个代理客户端**（mihomo/Clash），监听 `127.0.0.1:7890`，容器 `HTTP_PROXY` 指过去
2. **指向别处的代理**（家里的 Mac、另一台有代理的机器），需保证稳定可达
3. **暂时不用这些功能** —— 主链路不受影响，只是图集/视频/翻译降级

> 这也回答了原方案里一直"待定"的代理问题：**主链路直连，代理只为境外媒体/翻译服务**。

### 其他注意事项

1. **这是台共享机器** —— 已跑着 node×2、python3×6、nginx×4、postgres、redis、AdminLoop×2，以及 `/opt/dietitian_app`（另一个项目，当前无容器在跑）。
   - 内存余量够，但注意峰值
   - **别动现有 nginx 配置**，只加一个 server block
   - 端口要避开已占用的（80/443/3000/5004/8080/8090/8192-8194/8882/9980/9981/50003）
2. **root + 密码登录** —— 迁移时建议换成密钥登录 + 专用用户；生产服务不建议跑在 root 密码登录的机器上
3. **firewalld active** —— 5000/5001/9222 不在白名单。我们本来就只绑 `127.0.0.1`（由宿主 nginx 反代），**不受影响**；但若将来要直接暴露，记得开规则

---

## 一、容器拓扑

```
┌─ 容器 xhs ─────────────────────────┐   ┌─ 容器 hermes ──────────────────┐
│ supervisord (PID 1)                │   │ s6-overlay /init (PID 1，上游)  │
│  ├─ xvfb      :99 1440x900x24      │   │  └─ hermes gateway             │
│  ├─ cdp-keeper 循环60s 9222/9223   │   │      └─ cron(自带 jobs.json)    │
│  ├─ webapp    Flask :5000          │   │                                │
│  ├─ mcp       fastmcp :5001        │   │ network_mode: host             │
│  └─ cron      5 个任务              │   │ volume: ~/.hermes → /opt/data  │
│ network: bridge                     │   └────────────────────────────────┘
│ publish: 127.0.0.1:{5000,5001,9222} │                    │
└─────────────────────────────────────┘                    │
        │                                                  │
        └────────── 共享挂载（同一个宿主目录）───────────────┘
             /srv/xhs/project  →  两个容器内都是
             /home/user/PG/XiaohongshuSkills
```

### 为什么 xhs 侧必须单容器

四条硬约束（已核实）：

| 约束 | 位置 | 拆容器的后果 |
|---|---|---|
| 任务状态纯内存 | `web/app.py:58-63`，`_run_task` 存 `proc` 句柄供 `/api/task/<tid>/stop` kill | 查不到任务、停不掉进程 |
| PID 文件锁 | `scripts/run_lock.py` 用 `tempfile.gettempdir()` + `os.kill(pid,0)` | 跨容器 PID 无意义 → 发布单实例锁失效 |
| 硬编码 localhost | `scripts/fetch_runner.py:23` `http://127.0.0.1:5000` | 要改代码 |
| **`_is_local_host`** | `cdp_publish.py:176`，只有 host ∈ {127.0.0.1,localhost,::1} 才走 `ensure_chrome`/`restart_chrome` | **扫码/切账号功能报废** |

### 关键杠杆

`chrome_launcher.py:299 ensure_chrome` 第一行是 `if is_port_open(port): return True` —— **只要 9222 已被占，调用方传的 `--headless` 被完全忽略**。由 cdp-keeper 用 Xvfb+headed 先占住 9222，`yahoo_news_publish.py` 一路传下来的 `--headless` 自动失效。不改发布链路就能保持 headed。

---

## 二、Hermes 迁移设计

### 2.1 共享挂载：路径必须与 Mac 完全一致

技能里全是 `~/PG/XiaohongshuSkills/...` 的硬编码路径。做法：宿主目录 `/srv/xhs/project` **bind mount 到两个容器的 `/home/user/PG/XiaohongshuSkills`**，路径零改动。

### 2.2 ⚠️ 最大的技术坑：venv 的绝对路径

技能执行的是 `cd ~/PG/XiaohongshuSkills && .venv/bin/python scripts/xxx.py`。而 **Python venv 内嵌绝对路径**（`pyvenv.cfg` 的 `home = /usr/local/bin`、脚本 shebang），所以：

- 官方 `python:3.14-slim-trixie` 镜像 → Python 在 `/usr/local/bin/python3.14`
- Hermes 上游镜像 `debian:13.4` + apt → Python 在 `/usr/bin/python3`（且是 3.13）

**两边基础 Python 路径不同 → 共享的 `.venv` 在 Hermes 容器里跑不起来。**

**解法**：**hermes 镜像基于上游镜像做一层薄封装**，补上同路径的 Python 3.14：

```dockerfile
# ops/Dockerfile.hermes
FROM hermes-agent:upstream          # 上游 ~/.hermes/hermes-agent/Dockerfile 构建产物
USER root
# 上游已内置 uv（astral），用它装一份 3.14 到 /usr/local
RUN uv python install 3.14 --install-dir /usr/local \
 && ln -sf /usr/local/bin/python3.14 /usr/local/bin/python3 \
 && apt-get update && apt-get install -y --no-install-recommends sqlite3 \
 && rm -rf /var/lib/apt/lists/*
```

- glibc 一致：上游是 `debian:13.4`(trixie)，xhs 侧用 `python:3.14-slim-trixie`（同为 trixie）→ venv 里的编译产物（numpy/scipy/pandas）通用 ✓
- 只需保证 `/usr/local/bin/python3.14` 存在且是 3.14

**venv 的创建位置**：必须在最终路径下建（不能建在宿主别的路径再挂进来）。做法：首次启动后由 xhs 容器执行一次性引导：
```bash
docker exec xhs bash -lc 'cd /home/user/PG/XiaohongshuSkills && python -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-docker.txt'
```
venv 落在共享卷里，两个容器共用。

### 2.3 Hermes 的卷与网络

| 项 | 值 |
|---|---|
| 卷 | `~/.hermes` → `/opt/data`（上游约定，含 `.env`(21KB) / `config.yaml` / `skills/` / `cron/jobs.json` / `daily-reviews/`(117条) / `workspace/`） |
| 卷 | `/srv/xhs/project` → `/home/user/PG/XiaohongshuSkills` |
| 网络 | **`network_mode: host`**（上游默认；Telegram/UUMit webhook 需要，且技能要连 `127.0.0.1:9222`/`:5000`） |
| UID | `HERMES_UID`/`HERMES_GID` 对齐宿主，否则 `~/.hermes` 卷属主错乱 |
| 额外依赖 | `sqlite3` CLI（技能直接用） |

**因此 xhs 容器必须把端口发布到宿主**（否则 host 网络下的 hermes 连不上）：
```yaml
ports:
  - "127.0.0.1:5000:5000"   # admin UI
  - "127.0.0.1:5001:5001"   # MCP
  - "127.0.0.1:9222:9222"   # CDP —— hermes 的 xhs-cdp-search / xhs-keyword-fetch 技能要用
```
⚠️ 发布 9222 = 宿主上任何进程都能驱动已登录的 Chrome。只绑 `127.0.0.1`，别开 `0.0.0.0`。

### 2.4 Hermes 侧要做的改动

**几乎为零**（这正是"路径保持一致"的价值）。需要确认/调整的只有：

1. `~/.hermes/.env` 里若有指向 Mac 的路径 → 新容器 HOME 一致就无需改
2. `~/.hermes/hermes-agent/docker-compose.yml` 的 `volumes` 增加项目挂载：
   ```yaml
   volumes:
     - ~/.hermes:/opt/data
     - /srv/xhs/project:/home/user/PG/XiaohongshuSkills
   ```
3. Hermes 自带的 cron 随 `~/.hermes` 卷一起走，**不需要迁到 Linux cron**
4. 时区：Hermes 的写稿任务用 `$(TZ=Asia/Tokyo date ...)` 取 JST 日期 —— 容器 TZ 设 `Asia/Shanghai` 不影响

### 2.5 Hermes 与本项目的并发写库

Hermes 每日 03:00 写稿会**大量 UPDATE news**，而 xhs 侧 webapp / metrics / 抓取也在写同一个 SQLite 文件。

- 两者同宿主同卷 → 文件锁有效 ✓（这正是不能拆到两台机器的原因）
- 仍需 `PRAGMA busy_timeout=30000`（见 §五 改动 16）
- 建议错峰：Hermes 03:00 写稿期间，避免同时跑 metrics/抓取

---

## 三、扫码登录 → Admin UI

### 3.1 复用清单（**不要新写**）

| 现成实现 | 位置 | 说明 |
|---|---|---|
| `get_login_qrcode(wait_seconds)` | `scripts/cdp_publish.py:1247` | 返回 `{logged_in, qrcode_base64, qrcode_data_url, hint_text, ...}`。**headless/Xvfb 都能用**（CDP `Page.captureScreenshot` + clip，不依赖真实显示器） |
| `_locate_login_qrcode()` / `_capture_clip_png_base64()` | `:1191` / `:1164` | canvas `toDataURL` 优先，否则按 rect 裁剪 |
| `check_login()` / `clear_cookies()` / `open_login_page()` | `:1003` / `:1123` / `:1147` | 权威判定 / 重登 |
| `_set_login_cache` / `_clear_login_cache` | `:454` / `:468` | key = `127.0.0.1:9222:<account>:creator` |
| `_ensure_page_active()` | `:629` | 无显示器时的渲染唤醒，Xvfb 下继续需要 |
| Blueprint 模式 / `runTask()` 轮询骨架 | `web/wechat_views.py:17` / `web/app.py:1643` | 照抄 |
| `list_accounts` | `scripts/account_manager.py:120` | 纯文件读，不碰 Chrome |

### 3.2 新增 `web/login_views.py`（Blueprint）

进程内状态（与 `_tasks` 同样的单进程假设），**不持久化登录态**（它本来就在 Chrome profile 里）。

| 方法 | 路径 | 实现 |
|---|---|---|
| GET | `/api/login/accounts` | `account_manager.list_accounts` |
| POST | `/api/login/account` | 写 `tmp/current_account.txt`；账号变了则异步 `restart_chrome(9222, headless=False, account=X)` |
| GET | `/api/login/status` | **快路径**：直读 `tmp/login_status_cache.json`，不导航 |
| POST | `/api/login/status/refresh` | 后台跑 `check-login --no-login-cache` |
| POST | `/api/login/qrcode` | spawn `get-login-qrcode --wait-seconds 25 --account X`，**自己 Popen 收 stdout**（base64 会撑爆 `_tasks`），解析 `GET_LOGIN_QRCODE_RESULT:` 后的 JSON |
| GET | `/api/login/qrcode/<job_id>` | `{status, qrcode_data_url, hint_text, age_seconds}` |
| POST | `/api/login/qrcode/<job_id>/probe` | spawn 新命令 `login-probe` |
| POST | `/api/login/relogin` / `/switch-account` | 对应 CLI |

注册：`web/app.py:50` 旁加 `from web.login_views import login_bp` + `app.register_blueprint(login_bp)`。

### 3.3 `cdp_publish.py` 的 4 处最小改动

**A — 新增 `probe_login_state()`（插在 `check_login` 前，约 `:1000`）** ← 方案关键
展示二维码期间 UI 每 2s 要知道"扫上了没"，而 `check_login()` **会导航 → 让正在扫的码失效**。需要零导航探针：

```python
def probe_login_state(self) -> bool:
    """不导航的登录探测：直接查 .xiaohongshu.com 的 web_session cookie。"""
    self._send("Network.enable")
    cookies = self._send("Network.getCookies", {"urls": [
        "https://creator.xiaohongshu.com", "https://www.xiaohongshu.com"]}).get("cookies", [])
    ok = any(c.get("name") == "web_session" and c.get("value") for c in cookies)
    if ok:
        self._set_login_cache("creator", True)   # 复用 :454
        self._set_login_cache("home", True)
    return ok
```

**B — 新增 CLI `login-probe`**（`:5707` 附近）+ subparser 注册，输出 `LOGIN_PROBE: {"logged_in": true}`。

**C — 新增全局 `--no-login-cache`**（`:5323` 附近）；`:5684` 构造 publisher 后加 `publisher.login_cache_ttl_seconds = 0`。

**D — `login`/`re-login`/`switch-account` 的 `headless=False` 强制与 `restart_chrome` 分支保持不动**（`:5674`/`:5966/5974/5984`）。容器内 CDP_HOST 仍是 `127.0.0.1` → `_is_local_host` 为 True → 账号切换能工作。

### 3.4 UI 三处插入（`web/app.py`，复用现有 `.modal`/`.btn-red`/`.btn-gray`）

1. **顶栏按钮**（`:1078` 后）：`🔐 登录` + 状态圆点（绿=已登录/橙=未登录/红=缓存过期），加载时 + 每 30s 刷新
2. **弹窗**（`:1266` `#taskModal` 后）：账号下拉 + 二维码区 + `获取二维码`/`我已扫码，检查`/`强制重新登录`/`关闭` + 可折叠日志
3. **JS**（`:1643` `runTask` 前）：每 2s 轮询二维码 job + 同时打 `probe`，`logged_in` 一为 true 就变绿并自动关弹窗

### 3.5 关键行为

- **二维码过期**：`get_login_qrcode` 每次都会重新导航（= 强制刷新）。**绝不自动轮询刷新** —— 会让用户正在扫的码失效。UI 本地倒计时 120s 后置灰提示手动刷新
- **扫码确认**：主路径 `probe_login_state()`（~50ms 零导航）；兜底 `check-login`（导航，权威）；再兜底：任务日志出现 `NOT_LOGGED_IN`（`publish_pipeline.py:884`）自动弹登录窗
- **持久化**：就是 `<XHS_PROFILES_BASE>/<account>/` 挂卷。但 `tmp/login_status_cache.json` 是加速缓存不是真相 → **容器启动脚本 `rm -f` 它**，避免"缓存说已登录、cookie 已丢"
- **多账号**：一账号一 profile 目录；同一时刻 9222 只能跑一个，切账号 = `restart_chrome`（**前提是补上 `kill_chrome` 的 Linux 兜底**，见 §五 改动 4）
- **与发布互斥**：`cdp_publish.py:5999` 的 `single_instance` 会让发布中的扫码请求 `exit 3` → `login_views.py` 捕获并回 `{"locked": true}`，UI 提示而不是显示空白二维码

---

## 四、Dockerfile 与依赖（xhs 侧）

**基础镜像 `python:3.14-slim-trixie`**（对齐生产 3.14.4；`sqlite_db.py:34` 的 `_ConnectionContext` 就是为 3.14 事务语义写的）。trixie 的 ffmpeg 自带 libass，且 glibc 与 hermes 上游镜像（debian:13.4）一致 —— **这是 venv 能共享的前提**。

系统包：`xvfb x11-utils xauth` · **`fonts-noto-cjk` + `fonts-noto-color-emoji`（缺了页面全是豆腐块）** · `ffmpeg` · `librsvg2-bin`（`rsvg-convert` 替代 `qlmanage`）· `nodejs` · `sqlite3` · `git` · `supervisor` · `cron` · `tini` · `netcat-openbsd`

Chrome 用 **apt 源装 `google-chrome-stable`**（不用 Debian chromium —— 版本落后且 build flags 不同，指纹差异更大）。**非 root 用户 `xhs`(uid 1000)**。

### 4.1 新增 `requirements-docker.txt`（必需）

```
Flask>=3.1.0  beautifulsoup4>=4.12.0  Markdown>=3.5  numpy>=1.26
openpyxl>=3.1  tweepy>=4.14  urllib3>=2.0  websockets>=12.0
```

这 8 个包现在**全靠 `.venv` 里碰巧存在**。`lxml` 不需要（bs4 全走 `html.parser`）。

> ⚠️ **`.venv` 不能拷进镜像** —— 它被 hermes 污染了（163 个包，含 `atroposlib @ git+...NousResearch`、`datasets`、`playwright`）。必须从 requirements 干净重建。

### 4.2 新增 `.dockerignore`（安全关键）

`.venv/` `.git/` `scripts/.env` `config/wechat_conf.json` `config/accounts.json` `data/` `tmp/` `logs/` `*.db*`

---

## 五、代码改造清单（19 处 + 4 处）

| # | 位置 | 改成 |
|---|---|---|
| 1 | `chrome_launcher.py:36` | 首行 `if os.environ.get("CHROME_BIN"): return ...` |
| 2 | `chrome_launcher.py:150` 后 | `cmd += shlex.split(os.environ.get("CHROME_EXTRA_FLAGS",""))` |
| 3 | `chrome_launcher.py:153` | `if headless and os.environ.get("CHROME_FORCE_HEADED") != "1":` |
| 4 | `chrome_launcher.py:262` 前 | POSIX 兜底 `pkill -f "remote-debugging-port={port}"`（否则 `restart_chrome` 拉不起第二个实例） |
| 5 | `chrome_launcher.py:341` | profile 路径改读 `XHS_PROFILES_BASE` |
| 6 | `chrome_launcher.py:353` | `--headless=new` 受 `PROXY_CHROME_HEADLESS` 控制；**补 `PROXY_URL` 为空时不传 `--proxy-server` 的保护** |
| 7 | `account_manager.py:28` | `PROFILES_BASE` 改读 `XHS_PROFILES_BASE` |
| 8 | **`format_engine.py:313`** | `qlmanage` → 按 `rsvg-convert`/`inkscape`/`cairosvg` 顺序 `shutil.which` 探测。**唯一必须改的 macOS 专有调用** |
| 9 | `metrics_collector.py:19` | `DOWNLOAD_DIR` 改读 `XHS_TMP_DIR` |
| 10 | `unified_media_downloader.py:40` | 缓存目录改读 `MEDIA_CACHE_DIR` |
| 11 | `unified_media_downloader.py:47,975` | 去掉 `/opt/homebrew/bin/yt-dlp` 与 `/Users/user/...` |
| 12 | `en_tweet_gen.py:13` | DB 路径改读 `SQLITE_PATH` |
| 13 | `cron_b_filter.sh:4` / `cron_b_run.sh:6` | `DB="${SQLITE_PATH:-...}"` |
| 14 | `commit_db.sh` | `shasum` → 探测 `sha1sum`；`BACKUP_DIR` 默认 `/backup`；**并改用 `VACUUM INTO`**（见 §七.3） |
| 15 | **`web/app.py:3414`** | **`debug=False, use_reloader=False, threaded=True`** —— `debug=True` 起 reloader（双进程丢内存任务）且 0.0.0.0 暴露 Werkzeug 调试器 = RCE |
| 16 | `sqlite_db.py:38` | 加 `PRAGMA busy_timeout=30000`（**现在多了 Hermes 这个写方**） |
| 17 | `.mcp.json` | `cwd` → `/home/user/PG/XiaohongshuSkills` |
| 18 | `start_webapp.sh:3` | 删掉 `caffeinate` |
| 19 | `yahoo_common.py:114` | 把 `LITELLM_URL` 的硬编码兜底 `https://litellm-prod.toolsfdg.net` 改成显式报错 —— 否则配置缺失会**静默转去第三方 litellm 代理** |
| A-D | `cdp_publish.py` | 见 §三.3 |

**脚本处置**：`~/cdp-keeper.sh` → `ops/cdp_keeper.py`（复用 `ensure_chrome`/`ensure_proxy_chrome`，语义一致：端口已开直接返回、**绝不主动重启 9222**）· `~/gateway-watchdog.sh` **删除**（它治的是 macOS GUI 代理端口漂移，Linux 上不存在；且耦合 hermes plist）· `com.xhs.caffeinate` **删除**

---

## 六、定时任务映射

### 6.1 xhs 侧（原 10 个 launchd）

`ops/crontab`（Debian cron，以 `xhs` 运行，**首行显式声明 env**，cron 不继承 docker env）：

| launchd | 周期 | 容器方案 |
|---|---|---|
| `webapp` / `mcp-sse` / `cdp-keeper` | KeepAlive / 5min | supervisord `autorestart=true`（keeper 收到 60s） |
| `caffeinate` / `gateway-watchdog` | — | **删除** |
| `fetch-runner` | 每天 00:30 | `30 0 * * *`（加 `sleep $((RANDOM%1800))` 打散） |
| `metrics-collector` | 每小时 :00 | **改 `7 * * * *`**（整点太规律） |
| `reflection-runner` | 周日 23:00 | `0 23 * * 0` |
| `db-backup` | 每小时 :17 | `17 * * * *` |
| `db-backup-monthly` | 每月1日 03:17 | **移到宿主 cron**（见 §七.3） |

**互斥**：`metrics_collector.py:33` 直接 `PUT /json/new` 开 tab，**绕过 `_publish_lock`**，与发布并发会抢焦点。cron 包一层 `ops/wait_idle.sh`（复用 `~/gateway-watchdog.sh` 里现成的 `webapp_idle()`：`curl /api/active-tasks` 判 `publish_running`）。

### 6.2 Hermes 侧

**不需要迁到 Linux cron** —— 它有自己的调度器（`~/.hermes/cron/jobs.json` + `.tick.lock`），随 `~/.hermes` 卷一起走。7 个任务：写稿(03:00) / 素材review(01:30) / Telegram 健康检查(每10m) / UUMit ×4(当前 disabled)。

⚠️ Hermes 写稿 03:00 会大量写库，xhs 侧的 metrics(每小时:07) 和 fetch(00:30) 要避开或加 `wait_idle`。

---

## 七、卷与持久化

### 7.1 挂载表

| 宿主 | 容器 | 内容 | 丢了的后果 |
|---|---|---|---|
| `/srv/xhs/project` | **两个容器都是** `/home/user/PG/XiaohongshuSkills` | 项目代码 + **`.venv`（共享）** + `data/`(394MB DB) + `config/` + `tmp/` | **全部业务数据** |
| `/srv/xhs/profiles` | xhs: `/data/chrome-profiles` | Chrome `--user-data-dir`（cookies + Local Storage） | **登录态全丢** |
| `~/.hermes` | hermes: `/opt/data` | Hermes 的 `.env`/`config.yaml`/`skills/`/`cron/`/`daily-reviews/`/`workspace/` | **写稿链路全停** |
| `/srv/xhs/cache/{images,media}` | xhs: `/cache/*` | 图集/媒体缓存 | 重下即可 |
| `/srv/xhs/backup` | xhs: `/backup` | 分层备份 | — |
| `/tmp` | xhs: tmpfs 1g | `run_lock` 锁文件、metrics xlsx | 重启即清 |

**注意**：项目目录是**共享 bind mount**（不是烧进镜像）—— 这是路径一致的必要代价。代码更新 = 宿主 `git pull`，两个容器同时生效。

### 7.2 关键 env（xhs 容器）

`DISPLAY=:99` · `CHROME_BIN` · `CHROME_FORCE_HEADED=1` · `CHROME_EXTRA_FLAGS=--no-sandbox --disable-dev-shm-usage --disable-gpu --window-size=1440,900 --lang=zh-CN` · `XHS_PROFILES_BASE` · `SQLITE_PATH` · `USE_PROXY=0`（9222 直连）· **`NO_PROXY` 必须含 `127.0.0.1,localhost`** · `CDP_NO_LOGIN_FALLBACK=1`（**必须保留**，防登录失效时踢掉 9222）· `TZ=Asia/Shanghai` · `shm_size: "1g"` · `stop_grace_period: 60s`

**代理（据 §〇 实测确定）**：

| 用途 | 出口 | 配置 |
|---|---|---|
| 小红书发布（9222 Chrome） | **直连** | `USE_PROXY=0` → Chrome 自动加 `--no-proxy-server` |
| 写稿 LLM（DeepSeek） | **直连** | 不要给 Python 设全局 `HTTP_PROXY`，否则绕远 |
| 新闻抓取（Yahoo） | **直连** | 同上 |
| Instagram / YouTube / Google 翻译 / x.com | **需代理** | 见下 |

⚠️ 这台服务器**当前没有代理**（§〇）。两种处理：
- **装了代理** → 设 `HTTP_PROXY=http://127.0.0.1:7890`（或指向别处），并**务必把 `api.deepseek.com`、`creator.xiaohongshu.com`、`news.yahoo.co.jp` 加进 `NO_PROXY`**，让主链路走直连
- **暂时不装** → 不设 `HTTP_PROXY`；图集/视频/翻译功能降级，但**写稿+抓取+发布不受影响**（记得给 `unified_media_downloader` 的失败路径补 try/except，别让媒体下载失败拖垮整条流水线）

### 7.3 SQLite 备份

**现状**：`commit_db.sh`（每小时 :17，dump/restore + 校验 + 滚动保留 current/prev）· `commit_db_monthly.sh`（每月 1 日，切 90MB 分片 force push 到 GitHub `db-backup` 分支）

**⚠️ 必须先修一个隐患**：`commit_db.sh` 里有一句

```bash
if [ "$OLD_HASH" != "$NEW_HASH" ]; then cp "$TMP" "$DB"; fi   # 直接覆盖正在被多进程打开的 DB
```

DB 是 WAL 模式。`cp` 覆盖文件后，**其他进程手里还是旧 inode 的 fd**，会继续往已被替换的文件里写 → **数据分叉**。macOS 上只有 webapp 一个写方时风险尚可；迁到容器后 **Hermes 每天 03:00 也在写**，撞上的概率明显上升。另外这个 `.dump | sqlite3` 压缩**实际没起到压缩作用**（DB 394MB、备份也 394MB —— 里面全是真实数据没有可回收碎片），却付出了全量重写的代价。

**改法**：改用 SQLite 原生的 `VACUUM INTO`（3.27+），**不碰线上文件**、对并发写安全：

```bash
sqlite3 "$DB" "VACUUM INTO '$TMP'"          # 安全快照
[ "$(sqlite3 "$TMP" 'PRAGMA integrity_check;')" = "ok" ] || exit 1
[ "$(sqlite3 "$TMP" 'SELECT COUNT(*) FROM news;')" -gt 0 ] || exit 1
mv "$TMP" "$FINAL"                           # 校验通过才原子改名
```

**分层保留**（`ops/backup_db.sh`，cron 每小时跑；**硬链接去重**，同 inode 不占额外空间）：

| 层 | 保留 | 生成方式 |
|---|---|---|
| `hourly/` | 24 份 | 每次跑都生成 |
| `daily/` | 7 份 | 当天第一份跑时 `cp -l` 硬链接过去 |
| `weekly/` | 4 份 | 周一第一份跑时 `cp -l` |

**磁盘估算**：DB 394MB。最坏情况 35 份互不重叠 ≈ **13.8GB**。缓解：`daily/`+`weekly/` 是冷数据，加 `zstd` 压缩（文本型数据通常压到 1/3），可降到 **~4GB**；`hourly/` 保留原始以便快速回滚。部署前确认 `/srv/xhs/backup` 所在盘有足够空间。

**环境变量**：`DB_BACKUP_DIR=/backup`、`SQLITE_PATH`。注意 `shasum` 是 macOS/perl 的，Debian 只有 `sha1sum` —— 若保留 hash 比对逻辑需探测。

**异地备份：保留现有 GitHub 分片 push，但移到宿主 cron**
`commit_db_monthly.sh` 需要 `git` + `.git` + push 权限，**把 GitHub 写权限放进一个跑着浏览器自动化、暴露 5000/9222 的容器里是不必要的风险扩散**。做法：
- 脚本改读宿主路径 `/srv/xhs/project/data/news_dev.db`，在**宿主上的一个裸 git 仓库**里操作（`git init --bare`），或直接在 `/srv/xhs/project` 里跑
- 由**宿主 cron** 每月 1 日触发，凭据（deploy key / PAT）留在宿主，不进容器
- 保留原有的「孤立 commit + 分片」设计（绕开 GitHub 100MB 限制，历史永远 1 条）

**回滚演练**（迁移后必做一次）：从 `hourly/` 取一份 → 停容器 → 替换 `data/news_dev.db` → 删掉 `-wal`/`-shm` → 起容器 → 验证 `SELECT COUNT(*) FROM news` 与备份一致。

---

## 八、迁移步骤

| 步 | 动作 | 验证 |
|---|---|---|
| 0 | **宿主准备**（迁移目标机，Rocky 9.8）：Docker/compose **已装** ✓；建 `/srv/xhs/{project,data,tmp,config,logs,profiles,backup,cache/{images,media}}`；`useradd -u 1000 xhs`；`/srv/xhs/project` = git clone 项目。**注意这是共享机器，别动现有 nginx 配置** | `docker run --rm hello-world` · `getenforce`=Disabled · 端口 5000/5001/9222 空闲 |
| 0.5 | **决定代理方案**（见 §〇 出口矩阵）：主链路（小红书/DeepSeek/Yahoo）直连可用；Instagram/YouTube/Google 需代理。装了代理则设 `HTTP_PROXY` 并把三个主链路域名加进 `NO_PROXY`；不装则接受媒体/翻译降级 | `curl -o /dev/null -w "%{http_code}" https://www.instagram.com` 按预期返回 |
| 1 | 新增 `Dockerfile`/`.dockerignore`/`requirements-docker.txt`/`ops/*`/`web/login_views.py`；应用 §五 全部改动 | `git diff` 逐条核对 |
| 2 | 构建 xhs 镜像；**构建 hermes 镜像（上游 + python3.14 薄封装）** | 两个镜像都 build 通过 |
| 3 | **镜像自检** | `google-chrome-stable --version` · **`ffmpeg -filters \| grep -c subtitles` ≥1（确认 libass）** · `node -v` · **`fc-list :lang=zh` 非空** · **`/usr/local/bin/python3.14 -V` 在两个镜像里都有** |
| 4 | 迁数据：`data/`、`config/`、Chrome profile → `/srv/xhs/`；`~/.hermes` 保持在原位（或迁到 `/srv/hermes`） | — |
| 5 | **一次性引导 venv**（在最终路径下建） | `.venv/bin/python -c "import flask,bs4,numpy,pandas,scipy"` |
| 6 | `docker compose up -d`（先只起 xhs） | `supervisorctl status` 5 个 RUNNING |
| 7 | 验证 Xvfb+Chrome | `DISPLAY=:99 xdpyinfo` · `curl 127.0.0.1:9222/json/version` 且 **UA 不含 "Headless"** |
| 8 | **扫码登录**（核心诉求） | 点 🔐 → 获取二维码 → 手机扫 → 2s 内变绿自动关闭 |
| 9 | **验证登录态持久化** | `docker compose restart` → 仍为绿；再点获取二维码应返回 `logged_in: true` |
| 10 | 小规模抓取 | UI 勾 1 关键词 max=1 → `select count(*) from news` 增长 |
| 11 | **发布 dry-run** | `publish_pipeline.py ... --preview` → 跑到填表完成停住，不点发布 |
| 12 | **真实发布一条** | `FILL_STATUS: READY_TO_PUBLISH` → `PUBLISH_STATUS: PUBLISHED` → 手机 App 确认 |
| 13 | **起 hermes 容器** | `docker logs hermes` 无错 · 手动触发一次写稿 skill 验证能读到 DB、能调 `.venv/bin/python` |
| 14 | **验证 Hermes 写稿** | 手动跑一次 `每日3点写稿` 的 prompt（或等 03:00）→ DB 里出现新稿（`preselected=1, publish_xhs=0`） |
| 15 | 逐条启用 xhs 侧 cron | 先手动跑通每个脚本再放开 |
| 15.5 | **备份验证 + 回滚演练**（必做） | 手动跑 `ops/backup_db.sh` → `/backup/hourly/` 出现文件且 `integrity_check=ok` → **按 §七.3 的回滚步骤实操一次** |
| 16 | **切换**：Mac 上 `launchctl bootout` 全部 `com.xhs.*` + 停 hermes gateway | **保留 macOS 环境与 profile 至少 1 周**作回滚 |
| 17 | 观察窗口 | 头 48h 只抓取不发帖，之后每天 1 条缓慢爬坡 |

---

## 九、风险清单（按封号风险降序）

| # | 风险 | 缓解 |
|---|---|---|
| **1** | **出口 IP 变更**（家宽→机房）—— 小红书强关联「设备+IP」，**最大风险** | ① 优先让 9222 走与 Mac 相同出口（住宅代理），`USE_PROXY=1`；② 走机房 IP 则**登录后 48h 只浏览不发帖**，之后每天 1 条爬坡；③ 保留 Mac 环境随时回切 |
| **2** | **设备指纹变更**（macOS→Linux Chrome） | 锁 Chrome 大版本 · 装齐 `fonts-noto-cjk`+`fonts-liberation` · **不要**用 `--user-agent` 伪装 macOS（UA 与 `navigator.platform` 不一致更可疑） · **保持 headed(Xvfb)** |
| **3** | **行为规律化**（7×24 + 整点 cron + Hermes 固定 03:00） | metrics `:00`→`:07` · fetch 加随机 sleep · Hermes 的 03:00 可加 jitter · **保留 `--timing-jitter 0.25` 别关** |
| **4** | **12h 登录缓存掩盖失效**（只缓存正结果，可能带失效会话去发帖） | 发布路径加 `--no-login-cache`；TTL 降到 1-2h |
| **5** | **两个容器同时写 SQLite**（Hermes 写稿 + xhs 全链路） | 同宿主同卷（锁有效）+ `busy_timeout=30000` + 错峰 |
| **6** | **9222 发布到宿主**（host 网络下的 hermes 需要） | 只绑 `127.0.0.1`，绝不开 `0.0.0.0`；宿主防火墙确认 |
| **7** | `--no-sandbox` 改变渲染进程行为 | 优先试 `security_opt: [seccomp=unconfined]` + 非 root（保留 sandbox）；不行再退 `--no-sandbox` |
| **8** | 并发抢 9222 焦点（metrics 绕过发布锁；hermes 也会连 9222） | `wait_idle.sh`；hermes 的 CDP 技能与发布错峰 |
| **9** | **备份脚本 `cp` 覆盖正在使用的 DB** → WAL 下多写方数据分叉（**存量隐患**） | 改用 `VACUUM INTO`（见 §七.3）；备份期间避免并发写 |
| **9.5** | 容器被 kill 导致 DB 损坏 | `stop_grace_period: 60s`；用 `compose stop` 不用 `kill` |
| **10** | 密钥进镜像（`.env` 6 组 + `wechat_conf.json` + hermes 的 `.env` 21KB） | `.dockerignore` 排除；只走 `env_file` + bind mount |
| **11** | `debug=True` 的 Werkzeug 调试器 = RCE | 改 `debug=False` |
| **12** | 扫码 job 与发布 job 锁冲突（exit 3） | UI 捕获并提示"发布进行中" |
| **13** | **ffmpeg 路径从"从未跑通"变成"真的执行"** | 视频发布路径单独端到端测试；`_trim_black_start`/`_burn_bilingual_subtitles` 补 try/except |
| **14** | **venv 跨容器兼容**（最大技术坑） | hermes 镜像补 `/usr/local/bin/python3.14`；两个镜像同 glibc；**先在 hermes 容器里验证 `.venv/bin/python -c "import pandas"`** |
| **15** | Chrome 版本漂移 | 镜像打 tag，不做运行期 `apt upgrade` |
| **16** | admin UI 直接暴露公网（无任何认证） | `ports` 只绑 127.0.0.1 + 反代加认证 |
| **17** | 时区错位 | compose `TZ` + crontab 显式 `TZ`；注意 Hermes 写稿用 JST |
| **18** | **目标服务器没有代理** → Instagram/YouTube/Google 翻译/x.com 全部不可达（§〇 实测） | 主链路不受影响；要么装代理，要么接受媒体/翻译降级。**降级时必须给媒体下载失败路径补 try/except**，否则图集下载失败会拖垮整条流水线 |
| **19** | **目标机器是共享服务器**（已有 node/python/nginx/postgres/redis 等多个服务 + `/opt/dietitian_app`） | 别动现有 nginx 配置（只加 server block）；端口避开已用；注意内存峰值（Chrome 常驻 ~1.5G，现有已用 1.5G，总 7.5G） |

---

## 十、交付物清单

**xhs 侧新增**：`Dockerfile` · `.dockerignore` · `requirements-docker.txt` · `docker-compose.yml` · `ops/{supervisord.conf, crontab, cdp_keeper.py, wait_idle.sh, backup_db.sh}` · `web/login_views.py`

**宿主侧新增**：`/srv/xhs/bin/backup_db_monthly.sh`（改造后的月度 git 分片 push，宿主 cron 触发，凭据不进容器）

**Hermes 侧新增**：`ops/Dockerfile.hermes`（上游镜像 + python3.14 + sqlite3 薄封装）· 修改上游 `docker-compose.yml` 增加项目挂载

**修改**：`scripts/{chrome_launcher.py, account_manager.py, cdp_publish.py, format_engine.py, metrics_collector.py, unified_media_downloader.py, en_tweet_gen.py, sqlite_db.py, yahoo_common.py, commit_db.sh, cron_b_filter.sh, cron_b_run.sh}` · `web/app.py` · `.mcp.json` · `start_webapp.sh`

**删除/不迁移**：`~/gateway-watchdog.sh` · `~/cdp-keeper.sh`（被 `ops/cdp_keeper.py` 取代） · `com.xhs.caffeinate.plist`

**实施顺序**：先 §八 的 0-9 步（**把扫码登录跑通**，核心诉求 + 验证整条 CDP 链路）→ 10-12（发布链路）→ 13-14（**Hermes 接入**）→ 15-17（切换）
