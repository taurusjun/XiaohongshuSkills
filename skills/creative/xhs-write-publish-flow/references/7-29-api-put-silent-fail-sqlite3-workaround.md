# 7/29 API PUT无声失败 + sqlite3入库替代方案

## 现象

2026-07-29写稿批处理中，13篇story lf=1素材通过Python urllib PUT写入DB：
```
PUT /api/news/<40-char-key> → {"ok":true}
```
但后续 `GET /api/news/<key>` 显示 `rewritten_content: null`。
**7/29实测**：10次PUT返回ok但仅3次实际写入成功。改用sqlite3 UPDATE后13/13全部成功。

## 根因

API PUT接口的key匹配逻辑与直接SQLite查询不一致。list端点返回key（40位SHA1）与detail端点接受的key可能使用不同的哈希后缀算法——即使key看似完整，也可能指向DB中不存在的行。

## 替代方案：直接sqlite3 UPDATE

```python
import sqlite3
db = '/Users/user/PG/XiaohongshuSkills/data/news_dev.db'
conn = sqlite3.connect(db)
conn.execute(
    'UPDATE news SET rewritten_title=?, rewritten_content=?, publish_mode=?, preselected=? WHERE key=?',
    (title, body, 'rewritten', 1, full_40_char_key)
)
conn.commit()
conn.close()
```

## 优点

1. **100%成功率** — bypasses API key匹配问题
2. **不覆盖其他字段** — 只更新指定列，不会像API PUT那样因全量覆盖清空已有字段
3. **简单可验证** — 更新后立即用同连接SELECT验证

## 适用范围

- **所有xhs字段**（rewritten_title, rewritten_content, publish_mode, preselected）
- **所有gzh字段**（wechat_title, wechat_content, channel）
- **状态字段**（publish_xhs, preselected）

## 10/31 补充：不是「偶尔失败」，有些字段 PUT 永远写不进去

`PUT` 有字段白名单，白名单外的字段会**返回 ok 但静默丢弃**，而且每次都这样，不是随机：
- 实测 `{"score_dims": "..."}` → `{"ok":true}`，GET 回来一字未变（重打分、补 review 记录时最容易踩）
- 同类：`grade` / `grade_reason`
- 白名单内的字段（rewritten_title / rewritten_content / related_keys / publish_xhs / publish_mode / preselected / xhs_pub_time）PUT 正常

判据：**PUT 之后必须 GET 回读同一个字段并比对**，别只看 `{"ok": true}`。字段值没变就换 sqlite3 UPDATE。

另：`related_keys` 想清空时传 `""` 有效（10/31 实测，清掉一条挂错的关联素材后 GET 为 0 条）。


## 注意事项

- 只用UPDATE，不用DELETE/DROP TABLE
- 更新后必须commit()，否则不落盘
- 建议在同一个python脚本中完成：读draft→解析标题/正文→UPDATE→SELECT验证

## 10/31 补充：`score_dims` 根本不在 API 白名单里（不是匹配失败，是字段被忽略）

改稿后重打分，`PUT {"score_dims": ...}` 返回 `{"ok": true}`，GET 回来**还是旧值**、正文和标题都没被动。这不是 key 匹配问题——`score_dims` 压根不在 PUT 的可更新字段表里（见项目 `AGENTS.md` 的字段清单：只有 `preselected / xhs_pub_time / related_keys / publish_mode / rewritten_title / rewritten_content / publish_xhs / publish_time / title_score / content_score / status`），API 静默丢弃未知字段。

**判据：PUT 返回 200 ≠ 写入成功。必须 GET 回来比对值，`a.get(f) == new` 才算生效。** 10/31 实测 `score_dims` 的 `生效: False`，正文长度与标题未变，说明只丢了这一个字段。

正确写法（只改一列，不碰其它字段）：

```python
conn.execute("UPDATE news SET score_dims=? WHERE key=?", (new_dims, key))
conn.commit()
# 立刻 SELECT 三列回验：score_dims 生效 / length(rewritten_content) 未变 / rewritten_title 未变
```

**同类字段：任何「非 AGENTS.md 白名单」的列（`score_dims`、`grade`、`grade_reason`、`wechat_*`、`xhs_*` 统计列）都走 sqlite3 UPDATE**，不要指望 PUT。改完一律三列回验，别只看 `{"ok": true}`。
