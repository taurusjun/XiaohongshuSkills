# 译名规则与半译自查（10/25 用户纠正后建立）

## 规则

1. **正文/标题一律用中文规范译名**，日文原名最多留在 `tags` 里，且不要中英/中日双写同一个团体。
2. **禁止半译**：只翻后半截、前半截留拉丁字母的写法（`Morning娘。`）比全写日文更扎眼，用户会当场抓出来。
3. tags 里如果已经写了中文规范名，把同义的日文 tag 去重掉，别堆两个。

## 对照表（遇新名字往这里加）

| 日文原名 | 中文规范名 | 禁止写法 |
|---|---|---|
| モーニング娘。 | 早安少女组 | Morning娘。/ モー娘。/ 早安娘 |
| けやき坂46 | 榉坂46 | 平假名榉46 |
| ひなた坂46 | 日向坂46 | 平假名坂46 |
| 野中美郷 | 野中美乡 | 野中美郷（郷是日文新字体） |

## 全库自查 SQL

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "
SELECT substr(key,1,12) k, substr(COALESCE(rewritten_title,title),1,24) t,
       CASE WHEN COALESCE(rewritten_content,'') LIKE '%Morning%' THEN 'rc ' ELSE '' END ||
       CASE WHEN COALESCE(rewritten_title,'')   LIKE '%Morning%' THEN 'rt ' ELSE '' END ||
       CASE WHEN COALESCE(summary,'')           LIKE '%Morning%' THEN 'sm ' ELSE '' END ||
       CASE WHEN COALESCE(content,'')           LIKE '%Morning%' THEN 'ct ' ELSE '' END AS hit
FROM news
WHERE COALESCE(rewritten_content,'') LIKE '%Morning%'
   OR COALESCE(rewritten_title,'')   LIKE '%Morning%'
   OR COALESCE(rewritten_content,'') LIKE '%娘。%'
ORDER BY created_at DESC"
```

`content` 字段命中不用改：那是抓取端机翻，`publish_xhs=0` 的素材不直接发。

## 误判排除

- 节目名：`Good! Morning`、`Brand New Morning` —— 正常。
- 正常中文：`姑娘。`、`拼命三娘。`、`三娘。` —— `%娘。%` 会全部命中，逐条看上下文。

## 修法与边界

- 只用 PUT 改 `rewritten_content` 单字段，**不要带 `publish_xhs`**（带 `1` 会触发图集下载）。
- 替换后校验：残留计数 0、假名 0、`publish_xhs`/`publish_mode`/`xhs_pub_time` 未被改动。
- **已发布的稿子 DB 改了线上不会变**：要如实说「存档已修、线上那条得在创作中心手动编辑正文」，
  不要含糊成「已修好」。
