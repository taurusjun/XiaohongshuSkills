# Session 6/5 Workflow Pitfalls and Fixes

## 致命错误1：用截短的key写数据（全SCHEMA白费）

**现象：**
用 `2e0ad9b77fbcbd13`（16位）写入 update.sh 的 ref_keys，但数据库中的 key 是完整的40位 SHA1：
`2e0ad9b77fbcbd135687bd53da56f51deb4cda58`

API 返回 `{"ok": true}`，但实际上数据没有写入任何已存在的行——因为主键匹配不到。所有 6 篇文章的 ref_keys 全部写入失败，但没有报错。

**原因：**
在之前的上下文中拿到的是截短的 key（为了展示简洁），但实际 DB 里的 key 是完整的。API 的 PUT /api/news/<key> 如果 key 不存在，返回 `{"ok": true}` 而不是 404——这是个无言的失败。

**修复：**
```bash
# 先确认 key 是否存在于 DB
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT key FROM news WHERE key='<截短版本>%'"
# 用完整版本再写一次
```

**教训：**
- 永远不要在 key 上截短。40位就是40位
- API 返回 `{"ok": true}` 不代表写入成功——需要 sqlite3 验证
- 验证方法：`sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT related_keys FROM news WHERE key='<完整key>'`

## 致命错误2：ref_keys 字段不存在（字段名写错）

**现象：**
数据库 schema 中字段名是 `related_keys`，但代码中写入了 `ref_keys`。6篇文章全部写入了一个不存在的字段，所有数据丢失。

**原因：**
AGENTS.md 中写的是 `ref_keys`，但实际数据库执行 `.schema news` 后发现字段是 `related_keys`。update.sh 的 PUT API 接受任何字段名并返回 `{"ok": true}`，不会提醒字段不存在。

**教训：**
- 拿到一个新数据库后，**第一个操作必须是 `.schema` 确认字段名**
- 不要信任 AGENTS.md 或任何文档中的字段名——它们可能已经过期
- 写入后必须 sqlite3 查该字段确认值存在

## 致命错误3：Shell双重引号导致 \n 字面量写入

**现象：**
入库后用 `sqlite3` 查库发现换行符变成了 `\\n`（两个字符：反斜杠+n 字面量），而不是真实的换行符。小红书的渲染器不识别字面量 `\\n`。

**错误的写法（不要用）：**
```bash
bash update.sh <key> rewritten_content="$(cat file.md)"
# shell 展开时把文件内容的 \n 变成了 \\n
```

**正确的写法：**
```bash
python3 -c "
import json
data = json.dumps({'rewritten_title': '标题', 'rewritten_content': open('/tmp/draft.md').read()})
with open('/tmp/payload.json', 'w') as f: f.write(data)
"
curl -s -X PUT -H "Content-Type: application/json" -d @/tmp/payload.json "http://192.168.0.70:5000/api/news/<key>"
```

**验证方法（必做）：**
```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT substr(rewritten_content, 1, 100) FROM news WHERE key='<key>'"
# 确认显示的是真实换行，不是 "\\n"
```

## 流程教训：不要问用户"要不要继续"

用户明确说过几次的不准再犯：
1. 入库后直接跑 content review，不问
2. review 不到8分直接改、重跑，不问
3. 到了8分告知结果但不问"要不要发"
4. 一篇完成后直接继续下一篇，不问

## 密度的准确计算方法

**原文总长 ≠ 主素材 content_ja 长度。** 必须加 related_keys 的 content_ja。

```bash
# 查主素材
len1=$(sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT LENGTH(content_ja) FROM news WHERE key='<主key>'")

# 取 related_keys
related=$(sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT related_keys FROM news WHERE key='<主key>'")

# 拆逗号、逐个查
IFS=',' read -ra KEYS <<< "$related"
total_len=$len1
for rk in "${KEYS[@]}"; do
    len=$(sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT LENGTH(content_ja) FROM news WHERE key='$rk'")
    total_len=$((total_len + len))
done

# 正文长度
body_len=$(sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "SELECT LENGTH(rewritten_content) FROM news WHERE key='<主key>'")

# 占比 = body_len / total_len * 100
echo "占比: $(( body_len * 100 / total_len ))%"
```
