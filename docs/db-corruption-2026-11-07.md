# 生产库损坏事故（2026-11-07）

## 结论

生产 `data/news_dev.db` 报 `database disk image is malformed`，根因是每小时备份脚本
`scripts/commit_db.sh` 在库被 webapp / Hermes 打开且存在 WAL 时，用 `cp` 直接覆盖了在线主库。
数据未丢（10178 条，integrity ok），已恢复并修复脚本。

## 时间线

| 时间 | 事件 |
|---|---|
| 19:17 | 每小时 `com.xhs.db-backup` 运行；`news` 从 10166 → 10178，触发 `cp "$TMP" "$DB"` |
| 19:17~19:19 | webapp 开始对所有 DB 请求返回 500：`sqlite3.DatabaseError: database disk image is malformed` |
| 19:19 | 用户反馈 `/api/news`、`/detail/...` 500 |
| 19:20 | 停写库进程，保留现场；确认主库移走 WAL 后 integrity ok，10178 条 |
| 19:23 | 重写 `commit_db.sh`，恢复服务，webapp 200 |

## 根因

旧脚本：

```bash
sqlite3 "$DB" ".dump" | sqlite3 "$TMP"
# 内容有变化时：
cp "$TMP" "$DB"          # ❌ 覆盖在线主库
```

SQLite WAL 模式下，主库文件被替换后，旧的 `-wal` / `-shm` 仍引用旧主库的页帧。
下一次读取时 WAL 与新主库不匹配，直接报 `database disk image is malformed`。

触发条件：

- 库被其他进程打开（webapp / Hermes kernel / metrics）
- 仍有未 checkpoint 的 WAL
- 内容有变化，走到 `cp` 分支

## 影响范围

- 生产库：主库文件本身完好，仅 WAL 失配；10178 条与 19:17 备份一致，**无数据丢失**
- 容器库：未受影响（quick_check ok）
- 19:17 之后少量写入（含一次图集任务的 `gallery_url`）可能丢失，重跑即可

## 恢复步骤

```bash
# 1) 停写库进程
for label in ai.hermes.gateway com.xhs.webapp com.xhs.db-backup \
             com.xhs.metrics-collector com.xhs.fetch-runner \
             com.xhs.mcp-sse com.xhs.reflection-runner; do
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
done

# 2) 保留现场
mkdir -p ~/db-corrupt-20261107
cp -p data/news_dev.db* ~/db-corrupt-20261107/

# 3) 移走 stale WAL/SHM
rm -f data/news_dev.db-wal data/news_dev.db-shm

# 4) 验证
sqlite3 data/news_dev.db "PRAGMA integrity_check; SELECT count(*) FROM news;"
# ok / 10178

# 5) 启动服务
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.xhs.webapp.plist
# metrics / fetch / mcp / reflection / db-backup 同理
hermes gateway start
```

现场副本保留在 `~/db-corrupt-20261107/`（主库 + WAL + SHM）。

## 修复

`scripts/commit_db.sh`：

- 用 `VACUUM INTO` 生成一致性快照（老版本 SQLite 回退 `.backup`）
- 只对临时快照做校验，然后滚动 `news_dev.db` / `news_dev.db.prev`
- **绝不再覆盖 `data/news_dev.db`**

```bash
sqlite3 "$DB" "VACUUM INTO '$TMP'"
# 校验 news 行数 + integrity_check
# 内容有变化才 mv 当前备份 -> prev，再 cp 快照 -> 当前备份
```

提交：

```
生产  ff557d8  fix(backup): 禁止 cp 覆盖在线 DB，改用 VACUUM INTO 一致性快照
容器  85ef2d3  同上
```

## 附带修复

webapp 曾被手动启动过一个实例（父进程为 Hermes gateway），占用 5000，导致 launchd
实例反复 `Address already in use` 重启。已停手动实例并 bootstrap，现由 launchd 单实例托管。

## 防复发

1. 备份脚本永远不要在在线库上做 `cp` / `mv` 覆盖写入
2. 备份前后校验 `PRAGMA integrity_check` 与 `news` 行数
3. 需要压缩时用 `VACUUM INTO` 或 `.backup`，在独立文件上做
4. 建议加一条监控：每小时 `PRAGMA quick_check`，失败飞书告警
