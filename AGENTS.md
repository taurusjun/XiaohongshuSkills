# AGENTS.md — XiaohongshuSkills API 文档

本文件供 AI Agent（Hermes 等）使用，描述可调用的 REST API 和辅助脚本。

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

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `date_from` | string | 无 | 入库时间起始（含），如 `2026-06-01` |
| `date_to` | string | 无 | 入库时间截止（含），如 `2026-06-05` |
| `status` | string | `active` | 状态：`active` / `archived` / `skipped` |
| `category` | string | 无 | 分类筛选 |
| `search` | string | 无 | 标题/内容关键词搜索 |
| `publish_xhs` | string | 无 | `1`=已标记 / `published`=已发 / `pending`=待发 / `unpublished`=未标记 |
| `preselected` | int | 无 | `1`=预选 / `0`=未预选 |
| `fmt` | string | 无 | 格式：`news` / `story` |
| `score_min` | int | 无 | 最低综合评分（0-100） |
| `fetch_by` | string | 无 | 来源筛选，如 `yahoo` / `xhs` |
| `needs_review` | int | `0` | `1`=只看需要人工审核的 |
| `sort_by` | string | `created_at` | 排序字段，可选 `title_score`、`content_score` |
| `sort_dir` | string | `DESC` | 排序方向：`DESC` / `ASC` |
| `limit` | int | `200` | 最多返回条数，上限 500 |
| `offset` | int | `0` | 分页偏移 |

**响应结构：**

```json
{
  "rows": [
    {
      "key": "abc123",
      "title": "文章标题",
      "summary": "摘要",
      "category": "娱乐",
      "tags": ["AKB48"],
      "title_score": 85,
      "content_score": 90,
      "publish_xhs": 0,
      "preselected": 0,
      "xhs_pub_time": "",
      "ref_keys": "",
      "format": "news",
      "fetch_by": "yahoo",
      "created_at": "2026-06-05 10:00:00"
    }
  ],
  "total": 42,
  "today": 138,
  "pending": 3,
  "published": 435
}
```

---

### 2. 获取文章详情

```
GET /api/news/<key>
```

返回单篇文章的完整字段。

**响应结构（主要字段）：**

```json
{
  "key": "abc123",
  "title": "文章标题",
  "content": "正文...",
  "content_ja": "日文原文...",
  "comment": "小红书评论",
  "summary": "摘要",
  "category": "娱乐",
  "tags": ["AKB48"],
  "image_url": "/path/to/cover.jpg",
  "title_score": 85,
  "content_score": 90,
  "publish_xhs": 0,
  "publish_mode": "normal",
  "preselected": 0,
  "xhs_pub_time": "",
  "ref_keys": "",
  "rewritten_title": "",
  "rewritten_content": "",
  "format": "news",
  "fetch_by": "yahoo",
  "created_at": "2026-06-05 10:00:00",
  "scores": {}
}
```

---

### 3. 更新文章字段

```
PUT /api/news/<key>
Content-Type: application/json
```

只传需要更新的字段，其余字段不变。

**可更新字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `preselected` | int (0/1) | 是否预选发布 |
| `xhs_pub_time` | string | 计划发布时间，如 `2026-06-05 14:00` |
| `ref_keys` | string | 关联文章 key，逗号分隔 |
| `publish_mode` | string | 发布模式：`normal`（默认）/ `rewritten`（用改写内容）/ `free`（自由文本）/ `caption`（图说模式） |
| `rewritten_title` | string | 改写后标题（`publish_mode=rewritten` 时生效） |
| `rewritten_content` | string | 改写后正文（`publish_mode=rewritten` 时生效） |
| `publish_xhs` | int (0/1) | 标记为待发布 |
| `publish_time` | string | 实际发布时间（pipeline 写入，一般不手动设） |
| `title_score` | int | 标题评分 (0-100) |
| `content_score` | int | 内容评分 (0-100) |
| `status` | string | `active` / `archived` / `skipped` |

**rewritten 发布流程：**
```
PUT {"publish_mode": "rewritten", "rewritten_title": "...", "rewritten_content": "..."}
PUT {"publish_xhs": 1}   ← pipeline 会自动读 publish_mode 并使用改写内容
```

**响应：**

```json
{"ok": true}
```

---

## 辅助脚本

工作目录 `~/.hermes/workspace/` 下提供以下脚本，**优先用脚本代替手写 curl**：

### query.sh — 查询文章列表

```
bash query.sh [--参数 值 ...]
```

| 参数 | 说明 |
|------|------|
| `--date_from DATE` | 入库时间起始，如 `2026-06-01` |
| `--date_to DATE` | 入库时间截止，如 `2026-06-05` |
| `--status STATUS` | active（默认）/ archived / skipped |
| `--category CAT` | 分类筛选 |
| `--search KEYWORD` | 标题/内容关键词搜索 |
| `--publish_xhs VAL` | 1 / published / pending / unpublished |
| `--preselected VAL` | 1=预选 / 0=未预选 |
| `--fmt FORMAT` | news / story |
| `--score_min N` | 最低综合评分 |
| `--fetch_by SOURCE` | 来源，如 yahoo / xhs |
| `--needs_review` | 只看待审核 |
| `--sort_by FIELD` | 排序字段，默认 created_at |
| `--sort_dir DIR` | DESC（默认）/ ASC |
| `--limit N` | 返回条数，默认 20 |
| `--offset N` | 分页偏移，默认 0 |

**示例：**

```bash
# 查今日生成、按标题评分排序
bash query.sh --date_from 2026-06-05 --sort_by title_score --limit 20

# 查待发布文章
bash query.sh --publish_xhs pending

# 查预选文章
bash query.sh --preselected 1 --limit 50
```

---

## 使用规范

1. **只通过 API / 辅助脚本读写数据**，禁止直接操作 SQLite 文件（`data/news_dev.db`）。
2. **禁止任何破坏性操作**（DROP TABLE、DELETE、直接修改 .db 文件等）。
3. **如果 API 不满足需求**，请通过以下渠道提需求，等待功能扩展后再使用：
   - GitHub Issue（本仓库）
   - Telegram 联系项目负责人
   - 飞书私信联系项目负责人
4. **不要绕过 API 自行实现数据库操作**，即使技术上可行。

### update.sh — 更新文章字段

```
bash update.sh <key> <field>=<value> [<field>=<value> ...]
```

支持同时更新多个字段，数值类型字段（`preselected`、`publish_xhs`、`title_score`、`content_score`）自动不加引号。

| 字段 | 示例值 |
|------|--------|
| `preselected` | `1` / `0` |
| `publish_xhs` | `1` |
| `publish_mode` | `normal` / `rewritten` / `free` / `caption` |
| `xhs_pub_time` | `"2026-06-05 14:00"` |
| `ref_keys` | `key1,key2` |
| `rewritten_title` | `"新标题"` |
| `rewritten_content` | `"新正文"` |
| `title_score` | `88` |
| `content_score` | `90` |
| `status` | `archived` / `skipped` |

**示例：**

```bash
# 预选并设定发布时间
bash update.sh abc123 preselected=1 xhs_pub_time="2026-06-05 14:00"

# 写入改写内容并设定发布模式
bash update.sh abc123 publish_mode=rewritten rewritten_title="新标题" rewritten_content="新正文"

# 标记发布（pipeline 会读 publish_mode 走对应路径）
bash update.sh abc123 publish_xhs=1
```
