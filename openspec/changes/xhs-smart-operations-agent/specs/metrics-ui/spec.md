## ADDED Requirements

### Requirement: 列表页展示收藏率列
系统 SHALL 在 Web UI 的文章列表页新增「收藏率」列，计算方式为 `xhs_saves / xhs_views`（百分比显示），支持按该列排序。

#### Scenario: 有实发数据时展示收藏率
- **WHEN** 文章 `xhs_saves` 和 `xhs_views` 均非零
- **THEN** 列表行显示「X.X%」格式的收藏率

#### Scenario: 尚无实发数据
- **WHEN** 文章 `xhs_saves` 或 `xhs_views` 为 0 或 null
- **THEN** 收藏率列显示「—」

#### Scenario: 按收藏率排序
- **WHEN** 用户点击「收藏率」列表头
- **THEN** 列表按收藏率降序排列，无数据的行排在末尾

### Requirement: 文章详情页展示实发数据面板
系统 SHALL 在文章详情页新增「实发数据」区块，展示浏览/点赞/收藏/评论及最后回收时间。

#### Scenario: 有回收数据时展示面板
- **WHEN** 用户打开文章详情页，且 `xhs_collected_at` 非空
- **THEN** 页面展示实发数据面板，包含当前浏览/点赞/收藏/评论数字和回收时间点列表

#### Scenario: 无回收数据时展示提示
- **WHEN** `xhs_collected_at` 为空或 null
- **THEN** 面板区域显示「等待数据回收（发布后 4h 自动抓取）」

### Requirement: 手动触发数据回收
系统 SHALL 在文章详情页提供「立即回收」按钮，调用后端接口触发该文章的 CDP 数据抓取。

#### Scenario: 手动触发成功
- **WHEN** 用户点击「立即回收」按钮
- **THEN** 后端启动后台任务抓取数据，前端轮询任务状态（复用现有 `/api/task/<tid>` 机制），完成后刷新面板数字
