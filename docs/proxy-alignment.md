# 代理对齐生产 · 实施方案（2026-10-30）

> 起因：图集下载报
> `ProxyError(192.168.5.2:20808 Connection refused)`，而容器里配的端口是 20808、真实代理在 20809。
> 排查后发现了三处设计与实现的偏差，本文记录对齐方案与验收口径。

---

## 1. 生产是怎么做的（基线）

| 项 | 生产实现 | 位置 |
|---|---|---|
| 代理地址 | `HTTP_PROXY=http://127.0.0.1:20809` | `~/PG/XiaohongshuSkills/scripts/.env` |
| 谁维护端口 | **`~/gateway-watchdog.sh`**（launchd 每 120s）| `~/Library/LaunchAgents/com.xhs.gateway-watchdog.plist` |
| 怎么探 | 先读 Nayout 日志的 `mixed-port`，不通再 `seq 20800 20820` | 同上 |
| 漂移后做什么 | `sed` 改生产 `.env` + `ai.hermes.gateway.plist`；reload gateway；回收 9223 Chrome；重启 webapp | 同上 |
| 取代理 | `get_proxies()`：`PROXY_URL` 非空就返回 `{"http","https"}` | `config/yahoo_conf.py:48` |
| 启动检查 | `check_proxy()`：代理不通 → 测直连 → `_disable_proxy()` | `scripts/yahoo_common.py:40` |
| 必须代理名单 | `PROXY_REQUIRED_DOMAINS`（x/twitter/instagram/youtube/google/tiktok/telegram）| `config/net_fallback.py:22` |
| 强制直连名单 | `DIRECT_ONLY_DOMAINS`（`realsound.jp`，WAF 拦代理 IP）| `config/net_fallback.py:33` |
| 失败回退 | `install()` 在 `requests.Session.request` 外层：非必须代理站 → 直连重试一次 | `config/net_fallback.py:59` |

**结论：生产没有周期性“扫描结果文件”**，端口是 watchdog 直接 `sed` 进 `.env`；
业务代码里没有任何端口发现逻辑。

---

## 2. 容器为什么没跟上（三个偏差）

| # | 偏差 | 后果 |
|---|---|---|
| 1 | 代理写在 `docker-compose.override.mac.yml` 的 `environment:` | env 在**容器创建时固化**，端口漂移必须重建容器 |
| 2 | `gateway-watchdog.sh` 只 `sed` **生产** `.env` | 容器那份 `.env` 永远停在旧端口（20808）|
| 3 | 我在 `config/yahoo_conf.py` 加了 21 端口扫描 | 与生产不一致、重复造轮子、且是在错误层解决问题 |

> 附带记录：`_load_env()` 用 `os.environ.setdefault` → **容器 env 里的值会盖过 `.env`**，
> 所以偏差 1 会让人误以为“改 `.env` 没用”。

---

## 3. 目标设计（三条）

1. **代理只存在于挂载的 `scripts/.env`**，compose env 不注入 → 改文件即可生效，不重建容器
2. **端口漂移复用 `gateway-watchdog.sh`**，它同时维护生产与容器两份 `.env`，并在容器内重启 webapp
3. **业务代码零扫描**：`get_proxies()` 恢复成生产版本

---

## 4. 改动清单

### ① `config/yahoo_conf.py`：删除扫描，恢复生产实现

删除：`_PROXY_STATE` / `_tcp_ok` / `_discover_port` / `proxy_url()` / `proxy_alive()`，
以及被改过的 `get_proxies()`，恢复为：

```python
def get_proxies() -> dict:
    """返回 requests 格式的代理字典，无代理时返回空字典。"""
    if not PROXY_URL:
        return {}
    return {"http": PROXY_URL, "https": PROXY_URL}
```

保留 `scripts/gallery_fetch.py` 的 `_tft_get()`（它自己的直连兜底，经确认保留）。

### ② `docker-compose.override.mac.yml`：移除代理 env

删掉 `HTTP_PROXY` / `HTTPS_PROXY` / `ALL_PROXY` / `PROXY_URL` 四行，`NO_PROXY` 保留。
**需要一次性重建容器**（env 只能在创建时设定）；此后漂移不再重建。

### ③ `~/gateway-watchdog.sh`：漂移时同步容器

在漂移分支追加：

```bash
XHS_ENV_C="$HOME/PG/xhs-docker-src/scripts/.env"
[ -f "$XHS_ENV_C" ] && sed -i '' "s|192\.168\.5\.2:$cur|192.168.5.2:$live_port|g" "$XHS_ENV_C"
docker exec xhs supervisorctl -c /opt/ops/supervisord.conf restart webapp >/dev/null 2>&1
```

并把脚本副本纳入版本库 `ops/gateway-watchdog.sh`（便于追溯）。

### ④ 图片 CDN 直连：**本次不做**

2026-10-30 在容器内 `curl --noproxy` 实测：

| 域 | 直连 |
|---|---|
| `gravure.mantan-web.jp` | 200（1.7s）|
| `cdn.deview.co.jp` | 403 |
| `lpt.c.yimg.jp` / `sns-img.xhscdn.com` / `ismcdn.jp` / `cdn.livedoor.jp` / `data.smart-flash.jp` / `img-mdpr.freetls.fastly.net` | **000（连不上）** |

→ 这批 CDN 在容器网络里**大多必须走代理**，整体加入 `DIRECT_ONLY_DOMAINS` 会直接打断下载。
结论：暂不加入；将来只按“真实图片 URL 实测 200 且 <2s”的域名逐个加。

---

## 5. 执行顺序

```
1. ① 改 yahoo_conf.py（删扫描）
2. ② 改 override（删 4 行代理 env）
3. 用双 -f 重建容器（唯一正确命令）：
   docker compose -f docker-compose.yml -f docker-compose.override.mac.yml up -d
4. 验证：env 无代理 / 进程读到 .env / 挂载 9 个 / 服务 RUNNING / DB 行数不变 / webapp 200
5. ③ 改 gateway-watchdog.sh + 备份到 ops/
6. 模拟漂移：把容器 .env 改成死端口 20808 → 跑 watchdog → 应自动改回 20809 + webapp 重启
7. pytest
8. 图集 drain 抽样验证
9. 提交
```

### 唯一正确的重建命令

```bash
docker compose -f docker-compose.yml -f docker-compose.override.mac.yml up -d
```

只跑 `docker compose up -d` 会丢 `data/.venv/logs/tmp/gallery/exports` 6 个挂载（已踩过）。

---

## 6. 验收标准

1. `docker exec xhs env | grep -i proxy` → 只剩 `NO_PROXY`
2. `docker exec xhs ... -c "from config import yahoo_conf as y; print(y.PROXY_URL)"` → `http://192.168.5.2:20809`（来自 `.env`）
3. 漂移模拟后：两份 `.env` 都被改回、webapp 重启、无容器重建
4. `pytest` 全绿
5. 图集 drain 无代理报错

## 7. 回滚

- 代码：`git revert <commit>`
- 容器：把 4 行代理 env 加回 override → 双 `-f` 重建
- watchdog：`cp ops/gateway-watchdog.sh.bak ~/gateway-watchdog.sh`

## 8. 明确不做

- 不加任何定时扫描任务
- 不改生产仓库（只读）
- 不动 keeper / 发布链路
- 不迁移 CDP 重构那 3 个提交
