# 日文团体名/人名的中文写法（禁止半译）

用户口径：改写稿是纯中文，**日文名必须整词译成通行中文名**。最常见的错法是「只译一半，剩下的留罗马字」——10/25 被抓到的实例：

| 错 | 对 | 成因 |
|---|---|---|
| `Morning娘。` | 早安少女组 | 原文 `モーニング娘。`，只把「娘。」译了，`モーニング` 留成英文 |
| `モーニング娘。`（留日文） | 早安少女组 | 把日语原词直接搬进中文正文 |
| `平假名榉46` / `平假名坂46` | 榉坂46 / 日向坂46 | 自造译名 |
| `野中美郷` | 野中美乡 | 郷 是日文新字体，中文应作 乡 |

## 固定译法（用户给定）

- けやき坂(46) → 榉坂(46)
- ひなた坂(46) → 日向坂(46)
- モーニング娘。 → 早安少女组

遇新名先判断是否「半译」：正文里出现拉丁字母夹在中文中间（如 `Morning娘`、`Good Morning` 例外——节目名可保留英文原名）基本都是漏译。

## 扫库口径

```bash
# 改写稿里的半译残留（content 字段是抓取端机翻，未发布，可不动）
sqlite3 data/news_dev.db "SELECT substr(key,1,12), substr(COALESCE(rewritten_title,title),1,26), created_at
 FROM news WHERE rewritten_content LIKE '%Morning娘%' OR rewritten_content LIKE '%モー娘%'
   OR rewritten_content LIKE '%娘。%' OR rewritten_content LIKE '%早安娘%';"
```

注意 `娘。` 会误伤正常中文（「姑娘。」「拼命三娘。」），必须逐条看上下文再改，别批量替换。

## 改法

- 走 API `PUT /api/news/<key>` 只传 `rewritten_content`，**不要动 publish_xhs / publish_mode**。
- 已发布的稿件：DB 改的是存档，线上正文不会跟着变；要改线上得在创作中心编辑该笔记，先问用户。
- 改完复查：残留计数 0、假名数 0、字数变化 = (新名长度 − 旧名长度) × 出现次数。
