# Workspace 存稿 vs DB 入库缺口

## 问题

公众号稿件的 historic drafts 散落在 `~/.hermes/workspace/公众号_*.md`，但很多从未写入DB。而DB中的 `wechat_title` / `wechat_content` 字段才是发布脚本真正消费的字段。

这就导致了：
- 有稿子但发不出去（脚本只看DB，不看workspace）
- 新来的agent不知道workspace里有稿子，重复从零写稿
- 同一素材在workspace有3个版本，DB里只有1个（长尾谦杜案例）

## 库存盘点方法

```bash
# 1. 查workspace中所有公众号draft
ls -la ~/.hermes/workspace/公众号_*.md

# 2. 查DB中channel=gzh的已入库稿件
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT key, substr(wechat_title,1,50), LENGTH(wechat_content), wechat_publish, date(created_at) FROM news WHERE channel='gzh' ORDER BY created_at DESC;"

# 3. 找出缺口的稿件（在workspace有但DB没有的）
# 手工对比文件名 vs DB标题
```

## 迁移路径

从workspace draft迁移到DB `wechat_title` / `wechat_content` 的步骤：

1. **读draft文件**，确定质量是否可发
2. **找到该素材对应的DB key**（通过sqlite3查同人物/同事件）
   ```bash
   sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
     "SELECT key, title FROM news WHERE title LIKE '%关键词%' ORDER BY created_at DESC;"
   ```
3. **wechat_content 存 markdown，不转HTML**
   - `wechat_content` 存的是原始markdown（和rewritten_content格式一致：`##`小标题、`>`引用、**加粗**）
   - 发布时由 format_engine 渲染成微信兼容内联HTML
   - **不提前转HTML** — 格式引擎在不同时间点选择的主题不同，提前固化HTML会失去主题切换自由度
4. **写入DB**（API PUT）
   ```json
   {"wechat_title": "标题", "wechat_content": "<p>HTML正文</p>", "channel": "gzh", "wechat_publish": 0}
   ```
5. **验证**
   ```bash
   sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
     "SELECT wechat_title, LENGTH(wechat_content), wechat_publish FROM news WHERE key='<完整40位key>'"
   ```

## 注意

- wechat_publish 初始设为0，等用户review后再设1
- 标题可以和小红书不同（这是wechat_title独立于rewritten_title的原因）
- 如果draft内容与DB已有wechat_content重复，跳过
- 长尾谦杜系列4个版本 → 合并成1篇再入库
