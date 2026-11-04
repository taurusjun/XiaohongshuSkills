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

## 一词分译：日文一个词，中文两个词（10/31）

日文常用一个词覆盖中文两个词的含义，硬选一个会在标题和正文之间裂成两套说法。

| 日文 | 中文分裂成 | 选法 |
|---|---|---|
| 編集 | 编辑（文字/出版）／剪辑（影音） | 歌名/书名等**专名**一律按同形汉字写《编辑》，中文读者能与原名对上；只有叙述「剪片子这件事」时才用剪辑 |
| 育ち | 教养／家教／出身 | 「出身」是日语直译伪词，中文不形容礼仪（10/21 板野友美稿） |
| 広報 | 公关／宣传 | 逐字替换会造出中文不存在的「广报」（10/23 大盛真步稿） |

**实锤（10/31 乃木坂《編集》稿 `c0040119`）**：标题写《编辑》、正文写「一首以**剪辑**为名的歌」——同一个词两套说法，读者会疑惑「歌名到底叫啥」。修法：全文统一成「编辑」，862 字没变、假名仍 0。

**规则：**

1. 开写前先定这个词在**本篇**用哪个译法，标题、小标题、正文一次统一。
2. 两套说法并存时，改的是正文去靠标题（专名以标题为准），不是反过来。
3. 需要让读者知道原词另一层意思，就在正文**解释一次**（「編集，就是剪片子、剪素材的那个词」），不要在同一篇里换词。
4. 改完 grep 一遍旧译法：`标题用 A、正文用 B` 这类错，字数/假名/门禁全都拦不住，只有回读能抓。

