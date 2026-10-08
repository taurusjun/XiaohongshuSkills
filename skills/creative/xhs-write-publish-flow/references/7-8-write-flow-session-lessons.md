# 7/8 写稿Session教训

## 1. 写稿前先读daily review文件，不是自己翻DB

用户说「开始写稿」时，第一件事是读 `~/.hermes/daily-reviews/YYYY-MM-DD.md`。
不要搜session、不要拉API全量、不要看renwei输出——分级是review已经做完的工作，
写稿阶段只需消费它的输出。

**正确做法：**
```bash
# 第一步
read_file(path='~/.hermes/daily-reviews/$(date +%Y-%m-%d).md')
# 从文件中提取S/A/B级和AKB大TOP的key列表
```

## 2. API list endpoint vs search endpoint key后缀不一致

Same material has different key suffix depending on how you query it:
- `curl .../api/news?limit=200` 返回的key
- `curl .../api/news?search=关键词` 返回的key
- 这两者的key后缀可能不同（7/8教训：006b469...ebd065 vs 006b469...0b6a0f）

**入库时必须用search返回的key做PUT，入库后也要从search验证。**
用list端点的key做PUT会返回 `{"ok":true}` 但不落盘。

**正确做法：**
1. 写稿前从search获取完整key（含正确后缀）
2. 用这个key做PUT
3. 入库后从search验证rewritten_title是否写入
4. 如果list端点显示数据为空但search显示正常→search的key是对的

**根因：** news_dev.db中同一条记录可能有多个不同URL来源，list endpoint的主key
和search匹配的key可能对应不同来源记录。

## 3. 标题#前缀bug（第3次踩坑）

内联python写法 `lines[0].replace('## ', '', 1).strip()` 只处理双井号，
`# 标题`（单井号）入库后标题带 `#` 前缀。

**正确写法：**
```python
title_line = lines[0].strip()
if title_line.startswith('## '):
    title = title_line[3:].strip()
elif title_line.startswith('# '):
    title = title_line[2:].strip()
else:
    title = title_line
```
或通用写法：`lines[0].strip().lstrip('#').strip()`

## 4. 改稿循环评分必须重读全文

改稿后重跑review时，必须重读正文字段再重新逐维度评分，
不能根据改了什么点做「预估调整」。预估值不代表实际读者感受。
