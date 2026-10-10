# 已证实的稳定性问题

> 调查时间 2026-10-05 · 远端分支 `dev2` · 全部结论有代码/日志/SQL 证据
> 数据快照：`data/news_dev.db`（生产库，788 篇已发布+active）

分级：✅ 已证实 · ⚠️ 待验证（代码层风险）· ❓ 需业务确认

---

## 1. ✅ 72h 窗口标签链路断裂（功能已死）

**现象**：`news.xhs_collected_at` 从来没有被写入过窗口标签，三个下游消费者因此永远查不到数据。

**证据**

| 检查 | 结果 |
|---|---|
| 带 `%72h%` 标签的行 | **0** / 788 |
| 带任意 `(…)` 标签的行 | **0** / 788 |
| 符合 `agent_runner` 满 7 天待更新条件的行 | **0** |
| `metrics_history` 行数 | 1,156,969（采集本身正常在跑） |

三个等标签的下游：
- `scripts/agent_runner.py:307` — `AND xhs_collected_at LIKE '%72h%'`（满 7 天文章的话题表现更新）→ **从未触发**
- `scripts/dimension_analysis.py:66` — `n.xhs_collected_at LIKE ?`（有回退路径，见下）
- `scripts/dimension_analysis.py:14` / `reflection_runner.py:62` — `metrics_history.collected_at LIKE '%72h%'`

**根因（git 证据）**

| 提交 | 变化 |
|---|---|
| `fe1fbe7` | 加入 `_calc_window_label` / `_titles_match` + Gap1-5 测试套件 |
| `df598f5`（2026-05-23） | 重写 metrics_collector（Excel 导出 → CDP 拦 JSON），**删掉两个函数** |
| `57c8222`→`553fbcd`→`5d49f91` | 又回到 Excel 方案，模糊匹配**内联**成 3 处 `SequenceMatcher(...).ratio()`，**窗口标签逻辑没跟着回来** |

`collect_all()` 现在只写 `metrics_history`（`collected_at = "%Y-%m-%d %H:00"`，无标签），**从不写 `news.xhs_collected_at`**。
典型的「改结构没做全局 grep 收尾」——正是远端 `CLAUDE.md` 规则 4 要防的情况。

**为什么测试没发现**：`tests/test_gap1_collected_at.py` 的 TC-FL-2/TC-FL-3 在 fixture 里**手写** `xhs_collected_at='…(72h)'`，用生产里不存在的假数据把测试喂绿了。

**复现**

```bash
sqlite3 data/news_dev.db "SELECT COUNT(*) FROM news WHERE xhs_collected_at LIKE '%72h%';"   # → 0
```

**修法（待定）**：A. 恢复 `_calc_window_label` + 让 `collect_all` 按追加模式写 `news.xhs_collected_at`；B. 只修测试，确认 72h 口径已废弃则连带清掉三处 `LIKE '%72h%'` 查询；C. 折中，只恢复函数不改写库。

**状态**：⏸ 已上报，等业务方确认 72h 口径是否还在用

---

## 2. ✅ `xhs_note_id` / `xhs_title` 从未写回（发布正常，仅回写失败）

**现象**：788 篇已发布文章，**没有一篇**有 `xhs_note_id`；`xhs_title` 只有 1 篇非空。

**证据**

| 检查 | 结果 |
|---|---|
| 已发布+active 且 `xhs_note_id` 非空 | **0** / 788 |
| `xhs_title` 非空 | **1** / 788 |
| `publish_method='post'` 且发布晚于功能上线日（2026-05-23） | 334 篇 → 带 note_id 的 **0** 篇 |
| `publish_method='export'` | 19 篇（该分支本就不抓 note_id，符合设计） |

所以不是历史遗留：功能上线之后用普通分支发布的 334 篇，**note_id 回写无一成功**。

> ⚠️ 措辞澄清：**发布本身没有失败**。日志有 `PUBLISH_STATUS: PUBLISHED`、DB 里 `xhs_pub_time` 也正常写入，笔记是发出去的。
> 失败的**只是「发布后回写 note_id」这一步** —— 334 篇 = 334 次发布里 note_id 一次都没记下来。

**根因链（日志坐实）**

任务日志 `data/logs/task_2026-10-0*.log`（5 天）：

```
PUBLISH_STATUS: PUBLISHED  出现 12 次
Note published at:         出现  0 次   ← 关键
```

发布流程末尾固定长这样：

```
[cdp_publish] Publish button clicked.
PUBLISH_STATUS: PUBLISHED          ← 无条件打印，只要 _click_publish 没抛异常
[pipeline] Done.
✅ 发布成功，已记录时间             ← yahoo_news_publish 标记成功
```

- `publish_pipeline.py` 只在 `if note_link:` 时才打印 `[pipeline] Note published at: …`
- → `_click_publish()`（`cdp_publish.py:4961`）**每次都返回空**：点完按钮等 5s，页面里找不到 `a[href*="xiaohongshu.com/explore"]`，也匹配不到 24 位 hex
- → `publish_to_xhs()` 的正则 `xiaohongshu\.com/explore/([a-f0-9]{24})` 从 stdout 抓不到 → `note_id = ""`
- → `if note_id and sqlite_key:` 不成立 → **`update_news` 的 note_id/xhs_title 写入分支永不执行**

**关于成功信号**（不是发布失败，而是这个信号本身不构成证据）：`PUBLISH_STATUS: PUBLISHED` 是**无条件打印**的，只表示「点击按钮没报错」；同理 `yahoo_news_publish.py` 的成功判定只看子进程 `returncode == 0`。因此日志既不能证明也不能否证笔记是否真的上线（审核、限流、静默失败都不会反映出来）。本次调查**未发现任何发布失败的证据**，运营方反馈发布正常，与日志一致。

**影响（已量化，比初判轻）**：`metrics_collector` 依赖 `xhs_note_id` 做精确匹配，实际退化为标题模糊匹配（0.7 阈值）。但实测覆盖率仍达 **737/788 = 93.5%**（`metrics_history` 覆盖 739 个不同 news_key），**指标回收没有坏，标题匹配基本够用**。

真正的风险是健壮性而非功能：标题被改写、或系列文章标题相似时可能错配/漏配，且失去了 note_id 这个可交叉校验的锚点。剩余 51 篇无记录（含排期未发布的）待逐个确认。

**复现**

```bash
sqlite3 data/news_dev.db "SELECT COUNT(*) FROM news WHERE publish_xhs=1 AND xhs_note_id IS NOT NULL AND xhs_note_id != '';"  # → 0
grep -c "Note published at" data/logs/task_$(date +%F).log   # → 0
```

**修法方向（未实施）**：`_click_publish` 的 note_link 抓取需要重写（发布成功后页面结构/跳转已变，5s 固定等待也不够，应改为轮询 + 监听 `Network` 事件里的发布接口响应，从中直接取 note_id）；同时把「已发布」判定从「点了按钮」改成「拿到 note_id / 接口返回成功」。

**状态**：🔴 未修，影响数据回收精度

---

## 3. ✅ 测试套件收集失败（1 个文件整体丢失）

**现象**：`pytest` 直接报错中断收集。

```
ImportError: cannot import name '_calc_window_label' from 'scripts.metrics_collector'
ERROR tests/test_gap1_collected_at.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
```

`tests/test_gap1_collected_at.py:6` 导入 `_calc_window_label, _titles_match`，两个函数已不在 `scripts/metrics_collector.py`（全项目仅该测试文件仍引用）。
因 import 失败，该文件里 **6 个用例全部丢失**（含本可通过的 TC-FL-2/TC-FL-3）。

**其余状态**：跳过该文件后 **38 passed in 4.52s**。

**复现**

```bash
.venv/bin/python -m pytest -q                                          # → 1 error
.venv/bin/python -m pytest -q --ignore=tests/test_gap1_collected_at.py  # → 38 passed
```

**顺带**：`scripts/twitter_test.py` 匹配 pytest 默认 `*_test.py` 规则被收集，但它其实是手写脚本（`test_proxy`/`test_auth` 返回值），产生 `PytestReturnNotNoneWarning`。

**状态**：🔴 未修（修法取决于问题 1 的决策）

---

## 4. ⚠️ 配图临时文件按 basename 复用（代码层风险）

`yahoo_news_publish.py` 的 `publish_to_xhs()` 中：

```python
tmp = os.path.join(tempfile.gettempdir(), 'xhs_pub_' + os.path.basename(u.split('?')[0]))
if not os.path.exists(tmp) or os.path.getsize(tmp) == 0:
    r = _req.get(u, ...)   # 只在不存在或为空时才下载
```

**风险**：两篇不同文章的配图若 **basename 相同**（如都叫 `cover.jpg` / `image.jpg`），第二篇会**直接复用第一篇的图**，不重新下载。

**现状**：`/tmp/xhs_pub_*` 当前 0 个文件（未在生产中观测到实际错配），故标为待验证。日志中若有「图片下载失败」之外的异常配图，可回溯此项。

**修法**：文件名加内容哈希或 key 前缀（如 `xhs_pub_<key>_<basename>`）。

**状态**：⚠️ 未修，未观测到实际发生

---

## 关联影响

问题 1 与问题 2 都落在同一条数据回收链上，但**严重程度不同**：

- **问题 1（窗口标签）是更实质的**：`agent_runner.py:307` 的「满 7 天文章话题表现更新」因查不到 `%72h%` 而**从未触发过**（符合条件的行为 0）——这是一个确定停摆的功能。`dimension_analysis` 虽有回退路径不至于空输出，但「按 4h/24h/72h 分窗口比较内容爆发力」的设计意图无法实现。
- **问题 2（note_id）是健壮性问题**：精确匹配退化为 0.7 阈值标题模糊匹配，实测覆盖 93.5%，功能上够用；代价是失去交叉校验锚点，标题改写/相似标题场景下有错配风险。

两处都**不影响发布**。优先级建议：先确认问题 1 的口径是否还要（决定是修链路还是清查询），问题 2 可排在其后。

## 容器内已知坑（2026-10）

- **crontab 的 `%` 必须转义**：未转义的 `%`（如 `RANDOM%1800`）会让 cron 从 `%` 处截断命令（其后作为 stdin）→ 任务**静默不执行**。修：写成 `\%`。
- **fetch profile 属主**：`/data/chrome-profiles/fetch` 必须是 `user`；若被 root 创建，user 起的 Chrome 打不开端口（fetch 空跑）。修：`chown -R user:user`。
- **fetch 锁（`_fetch_running`）**：已有抓取时新触发返回 `locked:true` → `fetch_runner` 静默 `exit 0`（正常互斥）；异常残留用 `POST /api/admin/reset-fetch-lock` 复位。
- **gallery thefirsttimes**：`/report/{id}/attachment/{slug}/` 路径此前未支持（只认 `/news/`）→ 图集为空；已修（支持 `news|report` + 附件页回退 + 直连兜底 + 跨页去重）。
- **mcp 崩溃循环**：`fastmcp` 因 SOCKS 代理报错在 supervisord `autorestart` 下每 ~3s 重启、持续烧核 → 已移除 `[program:mcp]`。
