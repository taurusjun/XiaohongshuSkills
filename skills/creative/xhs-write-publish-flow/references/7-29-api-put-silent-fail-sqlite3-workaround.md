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

## 注意事项

- 只用UPDATE，不用DELETE/DROP TABLE
- 更新后必须commit()，否则不落盘
- 建议在同一个python脚本中完成：读draft→解析标题/正文→UPDATE→SELECT验证