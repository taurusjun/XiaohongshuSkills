# 抓取(ingest)失败日 vs 真·零素材日（2026-10-07）

## 触发场景

当日 `date_from=<today>` 返回 total=0，但**不是**「今天没新闻」——而是**上游素材抓取(fetch)链路失败**。

## 一、先诊断：区分「抓取失败」与「真无新闻」

1. 抓取日志：
   - `/Users/user/PG/XiaohongshuSkills/logs/fetch_runner.log` —— launchd `com.xhs.fetch-runner` 每日 00:30 触发的轮询日志（`=== fetch_runner 启动 ===` → 每 60s `task 0 status=running` → `status=done` / `=== 抓取完成 ===`）。
   - `/Users/user/PG/XiaohongshuSkills/data/logs/task_YYYY-MM-DD.log` —— 单次抓取任务明细（逐关键词）。
2. 判据：
   - 抓取失败：全关键词 `[关键词] 抓取出错: Connection timed out`（每词 3 次重试全败）→ 结尾「❌ 所有关键词均未找到新闻」。
   - 真无新闻：关键词成功执行但命中 0 条，无 timeout。
3. DB 复核：
   - `cp <cwd>/data/news_dev.db /tmp/nd.db`（含 -wal/-shm）后
   - `sqlite3 /tmp/nd.db "SELECT count(*) FROM news WHERE created_at LIKE '<today>%';"` → 0
   - `sqlite3 /tmp/nd.db "SELECT max(created_at) FROM news;"` → 昨天/更早

## 二、抓取失败根因定位配方（10/7 实测）

症状：`scripts/yahoo_news_auto.py::fetch_news_via_cdp` 每个关键词报 `抓取出错: Connection timed out`。

分层排查（逐项实测，10/7 结果）：

| 检查项 | 命令 / 方法 | 10/7 实测 |
|--------|-------------|-----------|
| Chrome CDP 存活 | `curl -s http://127.0.0.1:9222/json/version` | ✅ Chrome 152 |
| 代理连通 | `curl -x socks5h://127.0.0.1:$PORT https://api.telegram.org/` | ✅ 302 |
| 直连源站 | `curl --noproxy '*' https://news.yahoo.co.jp/` | ✅ 200 |
| Chrome 加载 example.com | CDP `Page.navigate` 观察事件 | ✅ `Page.loadEventFired` 0.8s |
| Chrome 加载 Yahoo 搜索页 | CDP `Page.navigate` 观察事件 | ❌ 无 `loadEventFired`，但 `Runtime.evaluate('document.title')` 返回正确标题 |

**判定 CDP 就绪事件的最小探针**（在 venv 里跑，`websocket-client` 已装）：
`PUT /json/new` 建 tab → `ws=create_connection(webSocketDebuggerUrl)` → 发 `Page.enable`（收响应）→ 发 `Page.navigate` → 循环 `ws.recv()` 看 event：
- `about:blank` / `example.com` → `Page.loadEventFired` 触发。
- Yahoo 搜索页 → 只有一串 `Page.frameNavigated`（主帧 + 大量广告/追踪子帧），**`loadEventFired` 持续不触发**；`ws.recv()` 在 15s socket 超时抛 `WebSocketTimeoutException: Connection timed out` → 该关键词判「拿不到页面」。

**根因**：`fetch_news_via_cdp` 用 `while <20s: if msg.get("method")=="Page.loadEventFired": break` 判断页面就绪。Yahoo 搜索页由于持续加载子帧，`loadEventFired` 不触发 → 每词 15s 超时 → 3 次重试全败。**Chrome 与网络本身正常**——是「加载完成判定」与页面行为不匹配（Chrome 自动升级到 152 / Yahoo 页面结构变更的可能触发因素）。

**修复方向（脚本侧，review cron 无权限改）**：不等 `Page.loadEventFired`，改为「固定等待 N 秒后直接 `Runtime.evaluate` 取 `document.documentElement.outerHTML`」，或用主帧 `Page.frameNavigated` 作就绪信号。修复后重跑 `scripts/fetch_runner.py`（= 调 `POST /api/trigger-fetch` 并轮询任务状态），或等次日定时。

## 三、⚠️ 决策：抓取失败日**不要**盲目按「3 日扩展」重审

主 skill 的 zero-entries 规则（7/07）说：当日 0 条 → 扩展最近 3 天重审。**10/7 实测暴露该规则的盲区：**

- 若最近 3 天**此前已各自完成 review**（存档已存在），扩展重审 = 100% 重复、零新增价值。
- **更危险的是下游**：`每日3点写稿` cron **读当日存档的 S/A 清单**并「覆盖当日全部 S+A+AKB大TOP 素材」，入库时设 `preselected=1, publish_xhs=0`。若本存档误列旧素材 S/A，写稿 cron 会**重写旧素材并把已发布条 publish_xhs 回退为 0** → 重复发文 / 状态回退风险。

**10/7 的正确处置（不扩展）：**
1. 交付「抓取故障状态报告 + 第 4 层发布数据回顾」，**不含第 1~3 层、不含 S/A 清单**（写稿 cron 读不到 S/A 会自然跳过）。
2. 顺带对照「发布管道」是否健康——ingest 与 publish 是**两条解耦链路**：素材断供 ≠ 发布停摆。10/7 实测 MAX_PUB 有值（10/06 22:23 id 8640）+ 10/07 定时队列 4 条 → 发布管道正常。
3. 报告里给：根因 + 修复建议 + 重跑建议；存档仍写 `~/.hermes/daily-reviews/<today>.md` 并 segment-send。

**何时才该扩展**：当日 0 条 **且** 抓取只是延迟/未跑（无 timeout 失败证据）**且** 最近几天素材**未被 review 过**（存档缺失）。三者不齐时不扩展。

## 四、第 4 层可独立执行（零素材日唯一可交付层）

第 4 层（发布数据回顾）不依赖当日素材，照常跑：
`python3 ~/.hermes/skills/xhs-daily-material-review-layer4/scripts/layer4_aggregate.py 'YYYY-MM-DD HH:MM'`
（注意 layer4 无 `creative/` 子目录；分层用 published 全量 offset 分页聚合，见 layer4 skill 4a）。

## 五、环境架构（排查用）

- 素材抓取：launchd `com.xhs.fetch-runner`（`~/Library/LaunchAgents/`），程序 `scripts/fetch_runner.py`，每日 00:30 触发，日志 `logs/fetch_runner.log`。
- Web app：launchd `com.xhs.webapp`，`web/app.py` on `127.0.0.1:5000`（提供 `/api/news`、`/api/trigger-fetch`）。DB = `<cwd>/data/news_dev.db`。
- Chrome CDP：`com.xhs.cdp-keeper`（每 300s 保活），`127.0.0.1:9222`，`--no-proxy-server` 直连。
- Hermes cron：`每日素材review`（`30 1 * * *`，CST）→ 本 skill；`每日3点写稿`（`0 3 * * *`）→ 消费当日存档 S/A。
