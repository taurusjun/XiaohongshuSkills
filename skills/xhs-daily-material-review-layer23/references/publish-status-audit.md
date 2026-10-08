# 发布状态取证（第3层搁置判定用）

本层要判「同系列 N 连入库 0 发布」和「发布管道是否停摆」，但**列表 API 拿不到发布时间维度**。
本文记录 9/23 实测的能力边界与可用替代路径。

## 1. API 过滤能力实测（9/23）

服务端会**静默忽略**未知参数（不报错、返回全量 7436 条、仍按 created_at DESC），所以
「参数没报错」≠「参数生效了」。必须核对返回的 `total` 是否变化、首行 created_at 是否仍是降序。

| 尝试 | 结果 |
|---|---|
| `sort_by=xhs_pub_time` | ❌ 忽略 |
| `sort_by=pub_time` | ❌ 忽略（返回顺序还乱了） |
| `sort_by=publish_time` | ❌ 忽略 |
| `sort_by=updated_at` | ❌ 忽略 |
| `xhs_pub_from=2026-09-01` | ❌ 忽略（total 仍 7436） |
| `status=published` | ❌ 忽略（total 仍 7436） |
| `published=1` | ❌ 忽略 |
| `/api/stats`、`/api/overview`、`/api/summary`、`/api/health` | ❌ 全部 404 |
| `/` | 返回「XHS 运营管理」HTML 看板（无 JSON 接口） |
| `sort_by=created_at`、`search=`、`limit=` | ✅ 正常 |

列表响应里 `published: 766` 只是**总数**，不带时间分布，无法用于「最近发布是什么时候」。

> ⚠️ 不要在 `/api/*` 上继续猜端点。已确认没有可用的发布统计接口，直接走下面的只读副本。

## 2. DB 定位

```bash
lsof -p $(pgrep -f "web/app.py" | head -1) | grep cwd
```

app 的 cwd 即项目根，库文件在 `<cwd>/data/` 下：

- `news_dev.db` — 主库
- `news_dev_backup_<ts>.db` — 备份
- `multi_source.db` — 多源

## 3. 只读副本配方（不碰原库）

```bash
cp <cwd>/data/news_dev.db     /tmp/nd.db
cp <cwd>/data/news_dev.db-wal /tmp/nd.db-wal 2>/dev/null
cp <cwd>/data/news_dev.db-shm /tmp/nd.db-shm 2>/dev/null
sqlite3 /tmp/nd.db "SELECT ..."
```

- **必须带上 `-wal` / `-shm`**，否则读到的是上次 checkpoint 的旧快照，今天的入库看不到。
- `sqlite3 -readonly <原库路径>` 在 WAL 库上实测报 `Error: in prepare, unable to open database file (14)`。别在这上面耗调用，直接复制副本。
- 查副本 = 纯读操作，不违反本层「禁止写入」铁则（不要对原库执行任何写 SQL）。

## 4. SQL cookbook

管道健康度（按日发布量）：

```sql
SELECT date(xhs_pub_time) d, count(*) FROM news
WHERE COALESCE(xhs_pub_time,'')!='' GROUP BY d ORDER BY d DESC LIMIT 10;
```

最近实际发布（判断管道存活）：

```sql
SELECT substr(key,1,12), created_at, xhs_pub_time,
       COALESCE(NULLIF(rewritten_title,''), title)
FROM news WHERE COALESCE(xhs_pub_time,'')!=''
ORDER BY xhs_pub_time DESC LIMIT 8;
```

定时发布队列（未来发布，发布前 0 阅读属预期，**禁止**写成「已发布」）：

```sql
SELECT substr(key,1,12), xhs_pub_time FROM news
WHERE xhs_pub_time > datetime('now') ORDER BY xhs_pub_time LIMIT 10;
```

逐人物搁置判定：

```sql
SELECT '人物名' k, count(*) n,
       sum(CASE WHEN COALESCE(xhs_pub_time,'')!='' THEN 1 ELSE 0 END) pub,
       COALESCE(max(xhs_pub_time),'-') lastpub
FROM news
WHERE title LIKE '%人物名%' OR content_ja LIKE '%人物名%' OR rewritten_title LIKE '%人物名%';
```

**⚠️ 必须三字段 OR。** 只用 `title LIKE` 实测会严重低估发布数：

| 人物 | 仅 `title LIKE` | 全文 OR | 差异 |
|---|---|---|---|
| 西畑大吾 | 61 条 / **0** 发布 | 144 条 / 4 发布 / lastpub 07-16 | 会把「有 4 条发布史」的人误判成强搁置 |
| 深泽辰哉 | 42 / 0 | 43 / 0 | 一致（确实强搁置） |
| 久保史绪里 | 39 / 0 | 85 / 2 / lastpub 07-19 | 漏掉 2 条发布 |

## 5. 9/23 实测样例（可作判读参照）

**管道**：最新 pub=2026-09-22 18:24，当日 4 条，定时队列空 ⇒ 管道**存活但积压**（今日入库 52 条）。

**强搁置人物（全文 OR）**：

| 人物 | n | pub | lastpub |
|---|---|---|---|
| 前田敦子 | 53 | **17** | 09-02 ✅ 活跃 |
| 相川暖花 | 7 | **4** | 08-04 ✅ 有先例 |
| 若月佑美 | 22 | 3 | 08-02 ◐ |
| 武元唯衣 | 26 | 3 | 07-08 ◐ |
| 佐藤胜利 | 75 | 3 | 08-09 ◐ |
| 猪俣周杜 | 45 | 2 | 08-09 ◐ |
| 青叶坂46 | 28 | 2 | 08-19 ⚠️ 之后 12+ 连 0 发布 |
| 久保史绪里 | 85 | 2 | 07-19 ⛔ |
| 宫馆凉太 | 189 | 2 | 06-22 ⛔ |
| 秋元才加 | 6 | 1 | 05-16 ⛔ |
| 大家志津香 | 7 | 1 | 05-14 ⛔ |
| 石田千穗 | 3 | 1 | 04-22 ⚠️ |
| 西畑大吾 | 144 | 4 | 07-16 ⛔ 近 2 月 0 发布 |
| 深泽辰哉 | 43 | 0 | — ⛔ |
| 土屋太凤 | 72 | 0 | — ⛔ |
| 逢田珠里依 | 12 | 0 | — ⛔ |
| 芹那/小籔/石川小百合/吉村纱也香/樱井日奈子 | 3/7/1/1/2 | 0 | — ⛔ |

**用法**：`n` 大 + `pub=0` 或 `lastpub` 远早于今天 ⇒ 强搁置，对应素材在报告里降级/合并/跳过；
`pub` 占比高（如 17/53、4/7）⇒ 该人物是本批里**发布基础最好**的，应给最高优先。
