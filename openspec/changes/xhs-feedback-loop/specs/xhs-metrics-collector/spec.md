## ADDED Requirements

### Requirement: 发布后定时回收 XHS 互动数据
系统 SHALL 在文章发布后的 4h、24h、72h 三个时间点，通过 CDP 访问已发布笔记页面，抓取浏览量、点赞数、收藏数、评论数，并写回 SQLite `news` 表。

#### Scenario: 到达回收时间点时抓取数据
- **WHEN** 脚本运行，且存在 `xhs_pub_time` 已填充、`xhs_collected_at` 未包含对应时间点标记的文章
- **THEN** 系统使用 CDP 打开该笔记 URL，提取互动数字，更新 `xhs_views / xhs_likes / xhs_saves / xhs_comments`，并在 `xhs_collected_at` 中追加时间点标记（如 `4h,24h`）

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
系统 SHALL 将抓取到的互动数据原子性地写入 `news` 表，并记录回收时间戳。

#### Scenario: 数据写入成功
- **WHEN** CDP 成功提取到至少一项互动数字
- **THEN** 系统更新对应字段，设置 `xhs_collected_at` 为已收集的时间点列表

#### Scenario: 字段不存在（首次迁移）
- **WHEN** `news` 表缺少 `xhs_views` 等新字段
- **THEN** 系统在初始化时自动执行 `ALTER TABLE` 添加字段，不影响现有数据
