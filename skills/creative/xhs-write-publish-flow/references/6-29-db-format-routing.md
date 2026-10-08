# 体裁判定：改用DB字段（6/29修正）

## 背景

6/29 session 中，相叶雅纪的素材 DB 标记为 `format='story'` 且 `is_long_form=1`，但我按正文写了688字就当短news处理了，跳过了长文story的8分review要求。用户指出：**体裁由DB字段决定，不是正文写多少字决定。**

## 修正后的规则

写稿阶段1b改为：先用 `sqlite3` 查 `format` + `is_long_form` 字段，再决定路由：

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT format, is_long_form, story_type FROM news WHERE key='<完整40位key>'"
```

| DB字段值 | 体裁 | 处理 |
|----------|------|------|
| `format='story'` 且 `is_long_form=1` | **长文story** | 写1000-1500字，必须有`##`小标题分段 |
| `format='news'` 或 `is_long_form=0` | **短news** | 写约900字，不强制小标题 |

**不得用正文xhs字数反推体裁。** format=story的素材即使正文写得少，review时也要按长文story标准（5维度≥8分+##分段）。

## 之前的问题

- 相叶雅纪（fc9c4d64...）: DB标记story|1，我写成688字短news → 应重写为长文story
- 小坂菜绪（636f7440...）: DB标记story|1，但原文1839字全是综艺流程 → 走短news路由是可接受的例外

## 改动文件

- `SKILL.md`: 阶段1表格 "路由判断"→"查DB体裁字段路由"
- `SKILL.md`: 结构要求 "正文≥700字"→"DB中 format='story' 且 is_long_form=1"
- `SKILL.md`: Review通过标准 长文判断改为DB字段
- `references/operations-guide.md`: 1b路由判断段全部替换为体裁判断（DB字段）
