## ADDED Requirements

### FR-HRQ-1: HUMAN_REVIEW 分支处理
- 当 `diagnose_low_score()` 返回 `Action.HUMAN_REVIEW` 时，`process_news_item` 必须：
  1. 设置 `news["_needs_review"] = True`
  2. 正常调用 `insert_news()`（文章入库，status='active'）
  3. 在 `score_dims` 对应记录中标记 `action='HUMAN_REVIEW'`（通过 `upsert_score_dims` 扩展）
  4. 发送飞书异步通知（fire-and-forget，失败只记日志）

### FR-HRQ-2: Web UI 支持 needs_review 筛选
- `/api/news` 接受 `needs_review=1` 查询参数
- 返回所有 `score_dims` 中含 `action='HUMAN_REVIEW'` 的文章
- 详情页显示"⚠️ 需要人工审核"黄色标签

### FR-HRQ-3: 飞书通知内容
- 标题：`⚠️ 文章需要人工审核`
- 内容：文章标题、title_score、content_score、诊断原因
- 链接：Web UI 详情页 URL（`http://{server}:5000/detail/{key}`）

### Test Cases
- TC-HRQ-1: mock `diagnose_low_score` 返回 `HUMAN_REVIEW`，验证 `_needs_review=True` 且文章入库
- TC-HRQ-2: `diagnose_low_score` 返回 `HUMAN_REVIEW` 时，飞书通知被调用（mock assert）
- TC-HRQ-3: `diagnose_low_score` 返回 `DISCARD` 时，飞书通知**不**被调用
- TC-HRQ-4: `/api/news?needs_review=1` 只返回有 HUMAN_REVIEW action 的文章
