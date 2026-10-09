# 容器环境变量 · 网络 · LLM 配置

> 配套 [docker-containerization-plan.md](docker-containerization-plan.md)。**宿主差异只允许出现在 override/.env**。

## 1. 容器运行时 env（`ops/xhs.env`，可移植）

| 变量 | 值/说明 |
|---|---|
| `DISPLAY` | `:99`（Xvfb） |
| `CHROME_BIN` | `/usr/bin/google-chrome-stable` |
| `CHROME_FORCE_HEADED` | `1`（忽略 `--headless`，保持 headed） |
| `CHROME_EXTRA_FLAGS` | `--no-sandbox --disable-dev-shm-usage --disable-gpu --window-size=1440,900 --lang=zh-CN` |
| `XHS_PROFILES_BASE` | `/data/chrome-profiles`（Chrome profile 命名卷） |
| `SQLITE_PATH` | `/home/user/PG/XiaohongshuSkills/data/news_dev.db` |
| `XHS_WEBAPI_BASE` | `http://127.0.0.1:5000` |
| `XHS_TMP_DIR` | `…/tmp` |
| `USE_PROXY` | `0`（9222 发布直连） |
| `CDP_NO_LOGIN_FALLBACK` | `1`（登录失效也不要踢掉 9222） |
| `TZ` / `HOME` / `USER` | `Asia/Shanghai` / `/home/user` / `user` |

## 2. Mac/colima 专属（`docker-compose.override.mac.yml`）

```yaml
build.args.APT_MIRROR: mirrors.cloud.tencent.com          # 境内 apt 加速（base 默认官方源）
environment.PIP_INDEX_URL: https://pypi.tuna.tsinghua.edu.cn/simple
environment.HTTP_PROXY / HTTPS_PROXY: http://192.168.5.2:20809   # = VM 网关 → 宿主 Mac 的 127.0.0.1:20809
environment.ALL_PROXY: socks5://192.168.5.2:20809
environment.NO_PROXY / no_proxy: 127.0.0.1,localhost,api.deepseek.com,news.yahoo.co.jp,creator.xiaohongshu.com,www.xiaohongshu.com,open.feishu.cn
sysctls."net.ipv6.conf.all.disable_ipv6": "1"             # 容器仅 IPv4 路由；否则 pip 走 IPv6 报 Network unreachable
```

**⚠️ 代理关键点**：`scripts/.env` 里有 `HTTP_PROXY=http://127.0.0.1:20809`（宿主代理）。组件用 `python-dotenv`（`override=False`）加载它，**不会覆盖容器已有的 env**——所以上面的 override 值优先，避免容器把代理指向自身的 `127.0.0.1` 而失败。**主链路/境内服务走 `NO_PROXY` 直连。**

## 3. LLM（`agent/llm.py`）

| 变量 | 说明 |
|---|---|
| `LITELLM_URL` | `https://api.deepseek.com` |
| `LITELLM_MODEL` | 如 `deepseek-v4-flash` |
| `LITELLM_API_KEY` | 密钥（在 `scripts/.env`） |
| `LITELLM_MAX_TOKENS` | 默认输出预算（如 `3000`） |
| **`LITELLM_THINKING`** | **默认 `disabled`**。推理模型会把大量 token 花在 `reasoning_content`，导致正文被截断（`finish_reason=length`、`content` 为空）。关闭思考后 0.4s 返回。需要思考时才设 `enabled` |

- LLM 请求用 **no-proxy opener 直连**，不受 `HTTP_PROXY` 影响。
- **并非走 LiteLLM 网关**（变量名沿用 `LITELLM_*`），而是 `agent/llm.py` 直接打 DeepSeek 的 OpenAI 兼容接口。

**当前实际值（`scripts/.env`）**：

| 变量 | 当前值 |
|---|---|
| `LITELLM_URL` | `https://api.deepseek.com` |
| `LITELLM_MODEL` | **`deepseek-v4-flash`** |
| `LITELLM_API_KEY` | `sk-a61…`（在 `scripts/.env`） |
| `LITELLM_MAX_TOKENS` | `3000`（各调用会覆盖：review 20000 / compose 16000 / 评分 4000…） |
| `temperature` | `0.0` |
| `LITELLM_THINKING` | 未设 → 默认 **`disabled`** |

> **fetch 抓取同款**：`fetch_runner`/抓取链路也用 `deepseek-v4-flash`（日志 `模型=deepseek-v4-flash | 后端=sqlite`）。
> 换模型/端点：改 `scripts/.env` 的 `LITELLM_MODEL`/`LITELLM_URL`/`LITELLM_MAX_TOKENS`。

## 4. 交付（`services/delivery.py`）

- `XHS_DELIVERY_CHANNEL`：`feishu`（默认，本地存档 + 飞书推送）| `web`（仅本地）。
- **始终本地落盘** `data/reviews/<name>`；飞书为额外推送。飞书分段 ≤3500 字符。

## 5. 路径/连接统一来源（`services/paths.py`）

`SQLITE_PATH` · `XHS_API_BASE`/`XHS_WEBAPI_BASE` · `XHS_WORKSPACE` · `XHS_PROFILES_BASE` · `DB_BACKUP_DIR`。

## 6. 相关坑（已修）

- 容器 `websockets` 必须 **16.0**（17.x sync 客户端非 legacy 行为会让 `cdp_publish._send` 挂起）→ `requirements-docker.txt` 钉版。
- 容器重建后 hostname 变化 → Chrome `Singleton*` 陈旧锁 → entrypoint 启动前 `rm -f`。
- `ops/*` 改动**必须重建镜像**（entrypoint/supervisord 烘焙在镜像里）。

## 7. 宿主机(Mac)重启后的恢复 + 代理端口漂移（重要）

一键：`bash ops/recover-host.sh`。等价步骤与坑如下。

1. **colima 不会自动起** → `colima start`（可能几十秒）。
2. **镜像 `xhs:dev` 可能丢失**（重启后 `docker images` 只剩 `hello-world`）→ 必须重建；重建要能拉 `python:3.14-slim-trixie`，即 **dockerd 要走代理**（见第 3 条）。
3. **代理端口会漂移**：宿主 VeloceMac 的 Mixed 端口在 **20800–20820 之间变动**（`ai.hermes.gateway` + `com.xhs.gateway-watchdog` 只保证 `scripts/.env` 对齐）。**每次都要重新扫描确认**：
   ```bash
   for p in $(seq 20800 20820); do
     colima ssh -- sh -c "curl -s -m4 -x socks5h://192.168.5.2:$p -o/dev/null https://www.baidu.com" </dev/null && echo LIVE=$p && break
   done
   ```
   然后**同步三处**：
   - **VM dockerd**（拉镜像）：`/etc/systemd/system/docker.service.d/http-proxy.conf` → `HTTP(S)_PROXY=socks5://192.168.5.2:<PORT>`，再 `systemctl daemon-reload && systemctl restart docker`；
   - **容器 env**：`docker-compose.override.mac.yml` 的 `HTTP_PROXY/HTTPS_PROXY/ALL_PROXY`（`recover-host.sh` 会 sed 对齐到当前端口）；
   - **`scripts/.env`**：由 `com.xhs.gateway-watchdog`(每120s) 自动跟随，**无需手改**。
   > 注意：**HTTP 代理（20809）与 socks5（20808）可能不是同一端口**——探活要用 `socks5h://` 且逐个端口试。容器 env 里 HTTP_PROXY 用 `http://192.168.5.2:<PORT>`、ALL_PROXY 用 `socks5://...`。
4. **起容器必须带 override**：
   ```bash
   docker compose -f docker-compose.yml -f docker-compose.override.mac.yml up -d
   ```
   ❗ 漏掉 `-f docker-compose.override.mac.yml` → `data`/`.venv` 命名卷不挂载 → 容器内 DB 变 **0 字节**、报 **`no such table: news`**、`.venv` 里没有 `pytest`。**见到这三个现象，先查是不是少了 override（而不是数据丢了）。**
5. **数据/代码安全**：可变状态都在命名卷（`xhs_xhs-data`/`xhs_xhs-venv`/`xhs_xhs-profiles`/…）；生产仓库 `/Users/user/PG/XiaohongshuSkills` 与 worktree `/Users/user/PG/xhs-docker-src` 分离，重启不影响。核对：`docker inspect xhs --format '{{json .Mounts}}'` 应看到 4 个 `xhs_xhs-*` 卷挂到 `data/.venv/logs/tmp`。

## 8. 端口隔离（容器 vs 生产，硬约束）

容器内 `5000`(webapp)/`5001`(MCP)/`9222`(Chrome CDP) 只在**容器网络命名空间**内；对外**只重映射**到：

| 容器内 | 宿主发布（`127.0.0.1`） |
|---|---|
| 5000 webapp | **15000** |
| 5001 MCP | **15001** |
| 9222 CDP | **19222** |

生产在宿主用 `5000`(webapp)、`9222/9223`(Chrome CDP)——**两者不重叠**。
- `.env`：`XHS_UI_PORT=15000` / `XHS_MCP_PORT=15001` / `XHS_CDP_PORT=19222`。
- `docker-compose.yml` 的**默认值也必须是 15000/15001/19222**（`.env` 丢失时兜底）。
- 契约测试 `tests/test_compose_ports.py`：宿主端口 ∈ {15000,15001,19222}，且 ∉ {5000,5001,9222,9223}。
- ⚠️ **禁止**把容器写成直映 `5000:5000` / `9222:9222`——会撞生产。
