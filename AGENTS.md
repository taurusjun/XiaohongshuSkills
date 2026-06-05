# AGENTS.md — XiaohongshuSkills API 文档

本文件供 AI Agent（Hermes 等）使用，描述可调用的 REST API。

## 基础信息

- Base URL：`http://localhost:5000`
- 返回格式：JSON
- 鉴权：无（本地服务）

---

## API 端点

### 1. 查询文章列表

```
GET /api/news
```

**Query Parameters：**

| 参数 | 类型 | 说明 | 示例 |
|------|------|------|------|
| `date_from` | string | 入库时间起始（含），ISO 格式 | `2026-06-01` |
| `date_to` | string | 入库时间截止（含），ISO 格式 | `2026-06-05` |
| `sort_by` | string | 排序字段，默认 `created_at DESC` | `title_score DESC` |
| `limit` | int | 最多返回条数，默认 50 | `20` |
| `offset` | int | 偏移量，用于分页，默认 0 | `0` |
| `publish_xhs` | int | 筛选发布状态（0/1） | `1` |
| `preselected` | int | 筛选预选状态（0/1） | `1` |

**响应示例：**

```json
{
  "items": [
    {
      "key": "abc123",
      "title": "文章标题",
      "summary": "摘要",
      "created_at": "2026-06-05T10:00:00",
      "publish_xhs": 1,
      "preselected": 0,
      "xhs_public_time": "",
      "ref_keys": ""
    }
  ],
  "total": 42
}
```

---

### 2. 获取文章详情

```
GET /api/news/<key>
```

返回单篇文章的完整字段，包括 `content`、`rewritten_content`、`rewritten_title` 等。

**响应示例：**

```json
{
  "key": "abc123",
  "title": "文章标题",
  "content": "正文...",
  "rewritten_title": "",
  "rewritten_content": "",
  "comment": "",
  "summary": "",
  "category": "tech",
  "tags": ["AI", "日本"],
  "title_score": 85,
  "content_score": 90,
  "publish_xhs": 1,
  "preselected": 0,
  "xhs_public_time": "",
  "ref_keys": "",
  "created_at": "2026-06-05T10:00:00"
}
```

---

### 3. 更新文章字段

```
PUT /api/news/<key>
Content-Type: application/json
```

**可更新字段（全部可选，只传需要改的）：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `preselected` | int (0/1) | 是否预选发布 |
| `xhs_public_time` | string | 计划发布时间（ISO 格式，如 `2026-06-05 10:00`） |
| `ref_keys` | string | 关联文章 key，逗号分隔 |
| `rewritten_title` | string | 改写后标题 |
| `rewritten_content` | string | 改写后正文 |
| `publish_xhs` | int (0/1) | 标记为发布 |
| `publish_time` | string | 实际发布时间 |
| `title_score` | int | 标题评分 (0-100) |
| `content_score` | int | 内容评分 (0-100) |

**请求示例：**

```json
{
  "preselected": 1,
  "xhs_public_time": "2026-06-05 14:00",
  "rewritten_title": "新标题",
  "rewritten_content": "改写后的内容..."
}
```

**响应示例：**

```json
{"ok": true}
```

---

## 使用规范

1. **只通过 API 读写数据**，禁止直接操作 SQLite 文件（`data/news_dev.db`）。
2. **不要执行任何破坏性操作**（DROP TABLE、DELETE、直接修改 .db 文件等）。
3. **如果上述 API 不满足需求**，请通过以下渠道提交需求，等待功能扩展后再使用：
   - GitHub Issue（本仓库）
   - Telegram 联系项目负责人
   - 飞书私信联系项目负责人
4. **不要绕过 API 自行实现数据库操作**，即使技术上可行。

---

## 常见操作示例

### 预选一批文章

```bash
# 将 key=abc123 的文章标记为预选
curl -X PUT http://localhost:5000/api/news/abc123 \
  -H 'Content-Type: application/json' \
  -d '{"preselected": 1, "xhs_public_time": "2026-06-05 10:00"}'
```

### 查询今日生成的文章

```bash
curl "http://localhost:5000/api/news?date_from=2026-06-05&date_to=2026-06-05&limit=50"
```

### 写入改写内容

```bash
curl -X PUT http://localhost:5000/api/news/abc123 \
  -H 'Content-Type: application/json' \
  -d '{"rewritten_title": "新标题", "rewritten_content": "改写后正文..."}'
```
