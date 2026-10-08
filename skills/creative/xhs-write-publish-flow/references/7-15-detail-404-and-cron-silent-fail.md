# Detail端点404 + cron静默故障（7/15）

## 1. API Key后缀不匹配导致Detail端点404

**症状：** 从全量list端点 `GET /api/news?date_from=...&limit=200` 获取的key，在 `GET /api/news/<key>` 返回 **HTTP 404**。

**原因：** list端点返回的key与detail端点接受的key可能后缀不同（已知SQLite查询索引问题）。  
**更具体的版本：** list端点的key放到detail端点的URL里，服务端查不到该条记录 → 404。

**与前几天的"空content_ja"模式的关系：**

| 症状 | 根因 | 首次记录 |
|------|------|---------|
| detail端点返回 `content_ja: null` | key后缀不匹配 | 7/11 |
| detail端点返回 **404** | key后缀严重不匹配（连记录都查不到） | 7/15首次明确 |

两者是同一根因的不同严重等级，**恢复流程相同**。

**恢复流程（已无歧义）：**

```python
# 从list端点rows获取content_ja，不走detail端点
import urllib.request, json

url = "http://127.0.0.1:5000/api/news?date_from=2026-07-15&limit=200"
with urllib.request.urlopen(url, timeout=10) as r:
    rows = json.loads(r.read())['rows']

# 按title搜索目标key
for r in rows:
    if '关键词' in r.get('title', ''):
        key = r['key']         # 用list端点的key
        cj = r.get('content_ja', '') or ''   # list rows自带content_ja
        print(f"key={key} cj_len={len(cj)}")
```

**埋点字段验证（判断是否真的入库成功）：**  
PUT返回 `{"ok":true}` 不代表数据落盘（截短key、后缀不匹配都可能返回ok:true但实际没更新）。  
之后必须用 `GET /api/news/<key>` 验证 `rewritten_title` 和 `rewritten_content` 非空。

## 2. Cron job静默失败

**症状：** 每日review任务在 `agent.log` 中显示 `Running job` 且完成了几轮API调用，但：
- 没有output文件写入 `~/.hermes/cron/output/<job_id>/`
- `last_run_at` 未更新
- 用户未收到deliver
- `cronjob list` 显示 `last_status=ok` 但实际没交付

**诊断：** 看 `grep <job_id> agent.log | tail` 的最后一条。如果结束于创建OpenAI client（`client created`）而没有后续的完成日志，说明任务在子agent执行阶段丢失（可能delegate_task超时）。

**手动恢复：** `cronjob action=run job_id=<job_id>`
