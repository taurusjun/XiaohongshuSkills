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

## 4. 交付（`services/delivery.py`）

- `XHS_DELIVERY_CHANNEL`：`feishu`（默认，本地存档 + 飞书推送）| `web`（仅本地）。
- **始终本地落盘** `data/reviews/<name>`；飞书为额外推送。飞书分段 ≤3500 字符。

## 5. 路径/连接统一来源（`services/paths.py`）

`SQLITE_PATH` · `XHS_API_BASE`/`XHS_WEBAPI_BASE` · `XHS_WORKSPACE` · `XHS_PROFILES_BASE` · `DB_BACKUP_DIR`。

## 6. 相关坑（已修）

- 容器 `websockets` 必须 **16.0**（17.x sync 客户端非 legacy 行为会让 `cdp_publish._send` 挂起）→ `requirements-docker.txt` 钉版。
- 容器重建后 hostname 变化 → Chrome `Singleton*` 陈旧锁 → entrypoint 启动前 `rm -f`。
- `ops/*` 改动**必须重建镜像**（entrypoint/supervisord 烘焙在镜像里）。
