## ADDED Requirements

### Requirement: 发布后定时回收 XHS 互动数据（4h / 24h / 72h）
系统 SHALL 在文章发布后的 4h、24h、72h 三个时间点，通过 CDP 访问已发布笔记页面，抓取浏览量、点赞数、收藏数、评论数，并写回 SQLite `news` 表。

**xhs_collected_at 字段格式约定：**
- 逗号分隔字符串，值域 `{4h, 24h, 72h}`，按回收时间顺序追加
- 例：`4h` → `4h,24h` → `4h,24h,72h`
- Python 判断：`'72h' in (xhs_collected_at or '').split(',')`
- SQL 判断：`INSTR(xhs_collected_at, '72h') > 0`
- 包含 `72h` 标记后，`xhs_saves` / `xhs_comments` 视为相对稳定值，agent_runner 可据此触发 topic_performance 更新

#### Scenario: 到达回收时间点时抓取数据
- **WHEN** 脚本运行，且存在 `xhs_pub_time` 已填充、`xhs_collected_at` 未包含对应时间点标记的文章
- **THEN** 系统使用 CDP 打开该笔记 URL，提取互动数字，更新 `xhs_views / xhs_likes / xhs_saves / xhs_comments`，在 `xhs_collected_at` 中追加时间点标记

#### Scenario: 回收时间点尚未到达
- **WHEN** 文章 `xhs_pub_time` 距今不足 4h
- **THEN** 系统跳过该文章，不发起 CDP 请求

#### Scenario: 某时间点已回收
- **WHEN** 文章 `xhs_collected_at` 已包含 `24h` 标记
- **THEN** 系统跳过该文章的 24h 回收，不覆盖已有数据

#### Scenario: CDP 抓取失败
- **WHEN** 页面加载超时或互动数字元素不存在
- **THEN** 系统记录错误日志，跳过该文章，不修改数据库，下次运行时重试

### Requirement: 回收结果持久化到 SQLite
系统 SHALL 将抓取到的互动数据写入 `news` 表，并记录回收时间戳。

`news` 表新增字段（Phase 0 Schema Migration）：
- `xhs_views INTEGER DEFAULT 0`
- `xhs_likes INTEGER DEFAULT 0`
- `xhs_saves INTEGER DEFAULT 0`
- `xhs_comments INTEGER DEFAULT 0`
- `xhs_collected_at TEXT`（格式见上方约定）
- `xhs_pub_time TEXT`（文章实际发布时间，已有字段，确认可用）

#### Scenario: 数据写入成功
- **WHEN** CDP 成功提取到至少一项互动数字
- **THEN** 系统更新对应字段，追加 `xhs_collected_at` 时间点标记

#### Scenario: 字段不存在（首次迁移）
- **WHEN** `news` 表缺少上述新字段
- **THEN** 系统在初始化时自动执行 `ALTER TABLE` 添加字段，不影响现有数据

### Requirement: 单篇手动触发回收
系统 SHALL 支持通过 CLI 对指定文章手动触发一次数据回收，用于调试和补录。

#### Scenario: 手动触发单篇回收
- **WHEN** 执行 `python scripts/metrics_collector.py --key <news_key>`
- **THEN** 立即对该文章发起 CDP 请求，无论当前时间点是否到达，回填数据后打印结果
