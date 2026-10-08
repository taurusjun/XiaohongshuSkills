# Docker 容器化方案（Mac POC → Linux 云）

> **状态：现行方案（未实施）** · 编写于 2026-10-08
> 取代并延伸 [docker-migration-plan.md](docker-migration-plan.md)（原版面向腾讯云 Rocky Linux）。
> 本版把落地路径改成两步：**① 先在 `192.168.0.70`（macOS 生产机）用 colima 容器化跑通；② 镜像原样迁到 Linux 云。**
> 铁律：所有改动都在远端仓库执行（见根目录 `CLAUDE.md`）；严禁对 `*.db` 做文本/字节操作。

---

## 0. 背景与决策

### 0.1 为什么改口径

原 `docker-migration-plan.md` 直接以「腾讯云 Rocky Linux（4 核 / 7.5G / 178G）」为迁移目标。实际推进时决定**先在现有生产机 70 上把容器化跑通**，降低一次性迁移风险，同时**保证镜像能一字不改地迁到 Linux 云**。这带来一条贯穿全案的硬约束（见 §2 可移植契约）。

### 0.2 已确认决策（2026-10-08）

| 项 | 决定 |
|---|---|
| 落地机器 | `192.168.0.70` = `userdeMacBook-Pro.local`（macOS 15.7.9） |
| 容器运行时 | **colima**（轻量、纯命令行、对 host 网络与文件共享支持好、免费） |
| 迁移目标 | Linux 云（原 Rocky 9.8）；**镜像必须可原样迁移**，宿主差异只进 override/.env |
| 扫码 POC 落点 | **直接在容器里验证** |
| 技能解耦形态 | **三种都做**：纯 CLI + 提示词文件、HTTP REST、MCP 工具 |
| 第③层「大脑」 | **项目内置编排器 + 外部 MCP 客户端**（共用同一套能力） |
| 第④层交付 | **web UI 审批台 + 飞书**（弃用 Telegram `segment-send`） |
| 重构 | 分阶段进行 |

---

## 1. 目标机资源评估（70）

实测 2026-10-08：

| 项 | 实测值 | 结论 |
|---|---|---|
| 主机 | `userdeMacBook-Pro.local` · MacBookPro15,2 · x86_64 | — |
| OS | macOS 15.7.9（Darwin 24.6.0） | ⚠️ 见下方坑 A |
| CPU | 8 逻辑核 / 4 物理核 | ✅ 比原目标机（4 核）宽 |
| 内存 | 16 GB，系统空闲约 58%（≈9 GB） | ✅ 够，但已常驻 Chrome×2 + Hermes gateway |
| 磁盘 | 数据卷 233G，**可用 128G** | ✅ |
| 虚拟化 | `kern.hv_support: 1` | ✅ |
| 现有负载 | `com.xhs.*`（5000/5001/9222/fetch/metrics/reflection/backup）+ `ai.hermes.gateway` | ⚠️ 生产在跑，POC 必须避端口 |
| Docker | **未安装**（colima/docker 均无） | 见 §10 阻塞 |

**建议分配**：`colima start --cpu 4 --memory 8 --disk 60`（Intel Mac 上 colima 走 QEMU 而非 vz，性能略低，POC 可接受）。

**保真度坑（Mac 特有，迁云后自动消失，但 POC 阶段要知道）**：

- **A. macOS 上 Docker 只能跑 Linux 虚拟机**：容器里的 Chrome/Xvfb 在 VM 内，不是宿主 GUI。
- **B. 文件 I/O**：项目库 394MB（WAL）+ Hermes `state.db`（**28GB**）走 macOS↔VM 的 VirtioFS，小写入慢 → 见 §2 规则 5。
- **C. Chrome cookie 跨平台加密**：Mac profile 拷进 Linux 容器**大概率仍需重新扫码一次**（原方案已预期，正好由扫码 UI 覆盖）。
- **D. 出口 IP 不变**（仍在家宽），但迁云后 IP 变机房 → 原方案风险 #1 仍适用，迁云时再处理。

---

## 2. 可移植契约（硬约束）

**核心认知**：容器里的 OS 是 Linux（`python:3.14-slim-trixie`），与宿主是 macOS 还是 Rocky Linux **无关**。所以「镜像可移植」的真正含义是：

> **宿主差异只能出现在 `compose override` 和 `.env` 里，绝不能进 `Dockerfile` / 镜像 / 项目代码。**

### 2.1 什么放哪

| 内容 | 放哪 | 迁云时 |
|---|---|---|
| 系统依赖、Python 3.14、Chrome、ffmpeg、noto 字体、代码、supervisord、crontab | **镜像 / Dockerfile** | 不动 |
| 宿主绝对路径、代理、监听端口 | **`.env`** | 改值 |
| 卷类型、网络模式、`platform` | **`docker-compose.override.<host>.yml`** | 换 override |
| 密钥 `.env`、DB、Chrome profile、`~/.hermes` | **挂载 / `env_file`**（`.dockerignore` 排除） | 搬数据 |
| macOS 专有调用（`qlmanage`/`shasum`/homebrew 路径） | **在 §7 代码改造里铲掉** | 已解决 |

### 2.2 五条落地规则

1. **容器路径固定**：无论宿主，项目一律挂到 `/home/user/PG/XiaohongshuSkills`（`HOME=/home/user`），代码里 `~/PG/XiaohongshuSkills` 硬编码不用改。宿主侧走变量：Mac `${XHS_PROJECT_DIR}=~/PG/XiaohongshuSkills`，云上 `/srv/xhs/project`。
2. **网络统一用 bridge，放弃 `network_mode: host`**。原方案用 host 是为让 hermes 直连 `127.0.0.1:9222/5000`，但 host 模式在 macOS 语义不一致（colima 例外、Docker Desktop 需 4.34+），是可移植性最大的雷。改为：base compose 用 user-defined bridge + 发布端口（Linux 与 colima 行为一致，colima 会自动把发布端口转发到 Mac 的 localhost）；**hermes 容器内加 socat 回环转发**（`127.0.0.1:5000→xhs:5000`、`127.0.0.1:9222→xhs:9222`），skill 里的硬编码 `127.0.0.1` 照样可用，且两端行为完全相同。
3. **基础镜像 pin digest**，禁止运行期 `apt upgrade`（原风险 #15），保证两端现建产出一致。
4. **镜像交付**：云上**从 git 现建**（该服务器已有 `taurusjun` deploy key，SSH 22/443 已验证可用），或推 **GHCR**；两种都从**同一个 Dockerfile** 构建，不手工传镜像。
5. **卷**：默认 **bind mount + 变量化宿主路径**（迁云直接换变量）；**只有 Mac 上若文件 I/O 实测太慢，才在 `override.mac.yml` 里临时切 named volume**（数据搬迁靠 `VACUUM INTO` 快照，不依赖卷可移植）。

### 2.3 迁云动作（镜像不重做）

```
1. 改 .env        宿主路径 / 代理（家宽→机房，原方案风险 #1）
2. 换 override    mac → linux（或 base 直接通用）
3. 搬数据         VACUUM INTO 快照 → 云上 restore（原方案 §七.3）
→ docker compose up -d
```

---

## 3. 目标架构：能力 / 大脑解耦

### 3.1 分层

```
       ┌─ 外部 MCP 客户端（Claude Code/自建 agent）─┐
大脑 ──┤                                            ├── MCP server ──┐
       └─ 项目内置编排器（LiteLLM + cron）──────────┘                │
                                                                    ▼
                                              ┌──── services/（无 LLM，单一写方）────┐
   CLI ──────────────────────────────────────▶│ write / query / schedule / precheck / │
   REST (web/app.py) ─────────────────────────▶│ fetch / publish / login …             │
                                              └───────────────────────────────────────┘
                                                                    │
                              提示词知识：skills/**/SKILL.md + references/（进 git）
                              交付：web UI 审批台 + feishu_bot.py
```

### 3.2 仓库目录（目标）

```
XiaohongshuSkills/
  skills/            # ← 从 ~/.hermes/skills 迁入，纯提示词/知识，纳入 git
  services/          # ← 新增：确定性动作，无 LLM，单一写方
  cli/               # ← 薄壳入口
  web/app.py         # ← REST
  mcp_servers/       # ← MCP
  agent/             # ← 可选：替代 Hermes cron+delegate_task 的编排器
  ops/               # ← Dockerfile/compose/supervisord/crontab/备份脚本
```

### 3.3 入库与历史查询（**是能力，不经大脑**）

外部 MCP 客户端写一篇稿的序列：

```
1. xhs_get_news(key)                       # 读 content_ja 进 LLM 上下文
2. [LLM 在客户端侧写正文]                   # 第③层，与容器无关
3. xhs_update_news(key, {rewritten_title, rewritten_content,
                        publish_mode:'rewritten', preselected:1, publish_xhs:0})
4. xhs_score_dim(key, dim, 1.0)            # 评分落盘
```

历史查询工具：`xhs_list_news` / `xhs_search_news`（均支持 `fields` 裁剪与分页）/ `xhs_get_news` / `xhs_metrics_history` / `get_topic_performance`。

**两个必须先处理的现状问题**（纳入 P4）：

- **P4-A 写路径收敛**：现 `mcp_servers/xhs_operations_server.py` **直连 SQLite**（`from scripts.sqlite_db import query_news/update_news/_connect`），不走 webapp REST；加上 webapp/metrics/fetcher/Hermes/skill 里的 `sqlite3 UPDATE`，写方一大把。容器化前必须收敛为**单一写方 + `PRAGMA busy_timeout=30000`**。
- **P4-B MCP 端点与 DB 卷**：stdio MCP（`.mcp.json`）只能连本机 DB 文件；若 `data/` 放 named volume，宿主 stdio MCP 够不着 → 外部客户端应连**容器内 SSE 端点 `:5001`**。
- **P4-C 字段白名单**：需实测 `xhs_update_news` 是否放行 `rewritten_title/rewritten_content`（当前 docstring 未列出）。
- **P4-D 上下文体积**：列表类工具默认只回轻字段，禁止默认带 `content_ja` 全文。

---

## 4. 容器拓扑与 macOS 适配

```
┌─ 容器 xhs ─────────────────────────┐   ┌─ 容器 hermes ──────────────────┐
│ supervisord (PID 1)                │   │ s6-overlay /init (PID 1)        │
│  ├─ xvfb      :99 1440x900x24      │   │  └─ hermes gateway             │
│  ├─ cdp-keeper 循环60s 9222/9223   │   │      └─ cron(jobs.json)         │
│  ├─ webapp    Flask :5000          │   │  └─ socat 127.0.0.1:5000→xhs    │
│  ├─ mcp       fastmcp :5001        │   │      socat 127.0.0.1:9222→xhs   │
│  └─ cron      5 任务               │   │ network: xhs-net (bridge)       │
│ network: xhs-net (bridge)          │   └─────────────────────────────────┘
│ ports: 127.0.0.1:{5000,5001,9222}  │                    │
└────────────────────────────────────┘                    │
        └────────── 共享挂载（可移植契约：容器路径固定）────┘
             ${XHS_PROJECT_DIR} → /home/user/PG/XiaohongshuSkills
             ~/.hermes          → /opt/data (hermes)
```

> ⚠️ 发布 9222 = 宿主上任何进程都能驱动已登录的 Chrome。只绑 `127.0.0.1`。

**xhs 侧必须单容器**（已核实四条硬约束：内存任务表 `web/app.py:58-63`、PID 文件锁 `run_lock.py`、硬编码 localhost `fetch_runner.py:23`、`_is_local_host` `cdp_publish.py:176`），详见原方案 §一。

---

## 5. 扫码登录 → Admin UI（POC 核心）

**复用现成实现**（不要新写）：

| 组件 | 位置 |
|---|---|
| `get_login_qrcode(wait_seconds)` | `scripts/cdp_publish.py:1247`（headless/Xvfb 都能出码） |
| `_locate_login_qrcode()` / `_capture_clip_png_base64()` | `:1191` / `:1164` |
| `check_login()` / `clear_cookies()` / `open_login_page()` | `:1003` / `:1123` / `:1147` |
| `_set/_clear_login_cache` | `:454` / `:468` |
| `list_accounts` | `scripts/account_manager.py:120` |
| Blueprint 骨架 / `runTask()` | `web/wechat_views.py:17` / `web/app.py:1643` |

**新增 `web/login_views.py`（Blueprint）**：`/api/login/accounts`、`/api/login/account`、`/api/login/status`、`/api/login/status/refresh`、`/api/login/qrcode`、`/api/login/qrcode/<job_id>`、`/api/login/qrcode/<job_id>/probe`、`/api/login/relogin`、`/api/login/switch-account`。

**`cdp_publish.py` 4 处改动**：新增 `probe_login_state()`（零导航查 `web_session` cookie，避免 `check_login()` 导航让正在扫的码失效）+ CLI `login-probe` + 全局 `--no-login-cache` + 保持 `headless=False` 强制逻辑。

**容器内验证（P3）**：`docker compose up` → 顶栏 `🔐 登录` → 获取二维码 → 手机扫 → 2s 内变绿自动关闭 → `docker compose restart` 后仍为绿。

**关键行为**：绝不自动轮询刷新二维码（会让用户正在扫的码失效）；`tmp/login_status_cache.json` 启动时 `rm -f`；与发布互斥（`single_instance` exit 3 → UI 提示"发布进行中"）。

---

## 6. 技能解耦设计

一个 skill = 四层（以 `xhs-write-publish-flow` 为例：`SKILL.md` 1423 行 + `references/` ~140 篇）：

| 层 | 内容 | 能否脱离 LLM 自动跑 |
|---|---|---|
| ① 知识/提示词 | SKILL.md + references + glossary | 纯文本 |
| ② 确定性工具 | `write-api.py` / `pub_time_plan.py` / `batch_precheck.py` / `dump_ja.py` … | ✅ |
| ③ LLM 推理 | 写正文 / 5 维评分 / 聚类 / 推荐排序 | ❌ 必须 LLM |
| ④ 副作用/交付 | 落库 / 通知 / 定时 | ✅（要有接口） |

**三张皮一个芯**：底层同一组 `services/` 函数，上面包 CLI / REST / MCP。例：

```
services/schedule_service.py  plan_pub_time(keys, date, jitter) -> [...]
  ├─ CLI : python -m cli.schedule pub_time --keys K1,.. --apply
  ├─ REST: POST /api/schedule/pub-time
  └─ MCP : tool schedule_pub_time(keys, date)
```

**大脑**：外部 MCP 客户端 + 项目内置编排器（`agent/`，用 `yahoo_common.py` 现成 LiteLLM 配置）。Hermes 降级为可选调度器。

**交付**：弃 Telegram `segment-send`，改 web UI 审批台 + `feishu_bot.py`。

**迁入 git**：`~/.hermes/skills/{creative,social-media,writing,...}` 相关 skill → 仓库 `skills/`，有版本、可 review、不再散落。

---

## 7. 代码改造清单

沿用原方案 **§五 19 处 + §三.3 的 A–D 4 处**（此处不重复，见 `docker-migration-plan.md`）。**归属说明**：

- 其中 `qlmanage→rsvg-convert`、`shasum→sha1sum`、去 `/opt/homebrew`、`VACUUM INTO` 等改动，**本身就是为「可移植」服务的**（把 macOS 专有依赖铲掉），P1 一并做。
- `web/app.py:3414` 的 `debug=False, use_reloader=False, threaded=True` 是**安全必改**（否则 0.0.0.0 暴露 Werkzeug 调试器 = RCE）。
- `sqlite_db.py:38` 加 `PRAGMA busy_timeout=30000`（多写方）。

---

## 8. 重构计划（P6）

现状规模（LOC）：

| 文件 | 行数 |
|---|---|
| `scripts/cdp_publish.py` | 6002（最脆弱） |
| `scripts/gallery_fetch.py` | 3539 |
| `web/app.py` | 3471 |
| `scripts/format_engine.py` | 2867 |
| `scripts/yahoo_common.py` | 2353 |

原则：**先建立测试护栏，再拆**；每步全局 grep 三步验证（原规则 4）；不新增宿主相关硬编码，统一走 `services/` 配置层。顺序建议：`cdp_publish.py`（按职责切 selector / login / publish / feed）→ `web/app.py`（Blueprint 化）→ `gallery_fetch.py`。

---

## 9. 路线图

| 阶段 | 内容 | 验证 |
|---|---|---|
| **P0 环境** | colima + docker 安装、`colima start` | `docker run --rm hello-world` |
| **P1 容器化 xhs** | `Dockerfile` / `.dockerignore` / `requirements-docker.txt` / `docker-compose.yml` / `override.mac.yml` / `.env.example` / `ops/*` / `web/login_views.py`；§7 全部改动 | build 通过；`fc-list :lang=zh` 非空；supervisor 5 进程 RUNNING |
| **P2 Xvfb+Chrome** | 容器内 headed Chrome | `curl 127.0.0.1:9222/json/version` UA 不含 "Headless" |
| **P3 扫码 POC** | 容器内扫码 → admin UI | 扫成功 + 重启仍绿 |
| **P4 能力服务化** | `services/` + CLI + REST + MCP；skills 迁入 git；P4-A~D | 三张皮调同一 service，结果一致 |
| **P5 编排器 + 交付** | `agent/runner`（LiteLLM）替代 Hermes cron；交付改飞书/web | 手动触发一次写稿全链路 |
| **P6 重构** | 见 §8 | 每步有测试/TODO 收敛 |
| **P7 迁云** | 换 .env/override + 搬数据 | 原方案 §八 步 15.5 回滚演练 |

---

## 10. 环境现状（P0 已完成，2026-10-08）

### 10.1 踩坑：Homebrew 在这台 Intel Mac 上不可用

- 系统代理 `127.0.0.1:20809`（HTTP/HTTPS/SOCKS 都支持）；**ssh 会话默认不带代理 env**，下载 GitHub 需显式导出。
- **Homebrew 处于 Tier 3（不受支持）**：colima 仅 `sonoma` bottle，`lima / qemu / docker / docker-compose / go` **全无 bottle**（qemu 依赖的 glib/jpeg-turbo/libpng/libslirp/snappy 也无）→ `brew install` 只能源码编译，不可行。

### 10.2 解法：全预编译二进制（绕开 brew）+ VZ（免 QEMU）

| 组件 | 版本 | 来源 | 位置 |
|---|---|---|---|
| colima | v0.10.3 | `abiosoft/colima` release (Darwin-x86_64) | `/usr/local/bin/colima` |
| lima | 2.2.1 | `lima-vm/lima` release (Darwin-x86_64) | `~/.local/{bin,share,libexec}` |
| docker CLI | 29.8.2 | download.docker.com static (mac/x86_64) | `/usr/local/bin/docker` |
| docker compose | v5.6.0 | `docker/compose` release | `~/.docker/cli-plugins/docker-compose` |

- 无免密 sudo → lima 装 `~/.local`，靠 PATH 解析前缀（**不用** `/usr/local/bin/limactl` 符号链接，避免前缀错位）。
- **`colima start --vm-type vz` 在 Intel 上成功**：`colima is running using macOS Virtualization.Framework`（arch x86_64, mountType virtiofs）→ **不需要 QEMU**。依据：Lima ≥1.0 在 macOS≥13.5 默认 VZ，Intel 跑 x86_64 guest 属原生架构。
- VM 规格：**Ubuntu 24.04.4 / 4 CPU / 8GB / 60GB**；Docker Engine 29.5.2（宿主 CLI 29.8.2）。
- 复现命令：

```bash
# 下载需带代理
export HTTPS_PROXY=http://127.0.0.1:20809 HTTP_PROXY=http://127.0.0.1:20809 ALL_PROXY=socks5://127.0.0.1:20809
export PATH=\"$HOME/.local/bin:/usr/local/bin:$PATH\"
colima start --vm-type vz --cpu 4 --memory 8 --disk 60
docker run --rm hello-world     # EXIT=0 ✓
```

- 说明：colima 会把容器/VM 里的 `127.0.0.1` 代理自动改写为 VM 网关 `192.168.5.2`；Docker Hub 拉取实测直连可用。
- 安装脚本将沉淀为 `ops/install-colima-macos.sh`（P1 交付物）。

---

## 11. 交付物清单

**xhs 侧新增**：`Dockerfile` · `.dockerignore` · `requirements-docker.txt` · `docker-compose.yml` · `docker-compose.override.mac.yml` · `.env.example` · `ops/{supervisord.conf, crontab, cdp_keeper.py, wait_idle.sh, backup_db.sh}` · `web/login_views.py` · `services/` · `cli/` · `agent/` · `skills/`（迁入）

**宿主侧新增**：`/srv/xhs/bin/backup_db_monthly.sh`（改造后的月度 git 分片 push，宿主 cron 触发，凭据不进容器）

**Hermes 侧新增**：`ops/Dockerfile.hermes`（上游镜像 + python3.14 + sqlite3 薄封装）· 上游 `docker-compose.yml` 增加项目挂载

**删除/不迁移**：`~/gateway-watchdog.sh` · `~/cdp-keeper.sh`（被 `ops/cdp_keeper.py` 取代）· `com.xhs.caffeinate.plist`

---

## 12. 实施进展（滚动更新）

### 2026-10-08 · P0–P3 完成

- **P0 环境** ✅ colima v0.10.3 + lima 2.2.1 + docker 29.8.2 + compose v5.6.0（绕开 Homebrew Tier 3；`colima start --vm-type vz` 免 QEMU）。VM：Ubuntu 24.04 / 4C8G。
- **P1 容器化** ✅ Dockerfile / compose(base+mac override) / requirements-docker.txt / .dockerignore / ops/*；可移植改造已应用；镜像 `xhs:dev`。
- **P2 Xvfb+Chrome** ✅ 容器内 headed Chrome（UA `X11; Linux x86_64`），profile 持久化 `/data/chrome-profiles`。
- **P3 扫码出码** ✅ webapp `/api/login/qrcode` 端到端返回真实二维码（128×128）。

**P3 关键发现（小红书创作平台登录页已改版）**：

1. `creator.xiaohongshu.com/login` **默认短信登录**；页面里唯一的 img 是 64×64 的**切换图标**（`img.css-wemwzq`）。
2. **必须点击 `.css-wemwzq`** 才切到二维码视图；切换后新增一张 ~160×160、`src=data:image/png;base64` 的 img（真实二维码，native 128×128）。
3. 故 `get_login_qrcode` 已改：`_ensure_qrcode_login_mode()` 点切换 → `_locate_login_qrcode()` 取**面积最大**的 img/canvas → 直接复用其 base64 src（不再截屏）。
4. `customer.xiaohongshu.com/api/cas/customer/web/qr-code?service=https://creator.xiaohongshu.com` 是 CAS 接口，但需 `qr_code_id`（先创建再轮询）；当前 DOM 方案即可，无需该接口。

**环境坑（已修）**：

- 容器装了 `websockets 17.2`（requirements 原写 `>=12.0`），其 sync 客户端非 legacy 行为会让 `cdp_publish._send` 的 `recv` 挂起 → `Page.navigate` 超时。**已钉 `websockets==16.0`**（与生产 venv 一致）。
- 容器只有 IPv4 路由，但 DNS 返回 AAAA → pip `Network is unreachable`。已在 `override.mac.yml` 加 `net.ipv6.conf.all.disable_ipv6=1`。

**P3 补充（实测）**：

- 登录后**没有 `web_session` cookie**；会话标识为 `access-token-creator.xiaohongshu.com` / `x-user-id-creator.xiaohongshu.com` / `customer-sso-sid`。`probe_login_state` 已按此集合判定（零导航）。
- **扫码登录成功并持久化**：手机扫码后 `login-probe → true`；**整容器重建（新 hostname）后仍为 true**（Chrome profile 命名卷有效）。
- 教训：`ops/*` 改完**必须重建镜像**（entrypoint 等烘焙在镜像里），否则重建容器仍用旧配置。

**隔离现状**：生产 `dev2` 未动；工作分支 `docker-containerization`（worktree `~/PG/xhs-docker-src`）；宿主端口映射 15000/15001/19222（避开生产的 5000/5001/9222）；DB 用快照命名卷。
