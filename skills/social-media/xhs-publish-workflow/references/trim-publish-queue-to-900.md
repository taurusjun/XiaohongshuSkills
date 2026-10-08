# 待发布队列压到 900 字（10/5 实作）

## 触发

用户：「待发布超过900字的都需要改成900字左右」。

口径：**挂入待发布队列（`publish_xhs=1`）的稿子，正文压到约 900 字（落 880–910）**。
这是**发布口径**，与写稿阶段「story 正文不设上限、以密度为准」不冲突——写稿时不压，挂队列前再压。

## 0. 先确定作用范围

```bash
DB=~/PG/XiaohongshuSkills/data/news_dev.db
sqlite3 "$DB" "SELECT key, length(rewritten_content) rl, format, is_long_form, substr(rewritten_title,1,40)
  FROM news WHERE publish_xhs=1 AND (publish_time IS NULL OR publish_time='') ORDER BY rl DESC;"
```

- 待发布 = `publish_xhs=1 AND (publish_time IS NULL OR publish_time='')`（前端判据，与 API 的 `publish_xhs=published` 参数无关——那个只查 `publish_xhs=1`，不区分 `publish_time`）
- **队列里所有 >900 的都要过**，包括 900±10 边缘（905、939 也算「超过900」）。目标是全部落进 880–910
- `length()` 含换行符；xhs 正文字数 = `len(rc) - rc.count('\n')`。复核时两个数要对得上

## 1. 压缩（编辑性压缩，不是重写）

从 DB 取 `rewritten_content`（老批次草稿多半已不在 /tmp）。

- 保留：开场钩子、核心引语、时间线、关键数字、结尾的具体收束
- 砍：冗余修饰、重复情绪句、次要配角发言、并列名单里的次要项（顺带解决顿号行）
- 只改字数，**不改标题、不换叙事线**。改完若从「报道」变成「评论」= 压过头，回退
- 压稿文件写成 `## 标题\n正文`，**首行必须带 `## `**，否则 `batch_precheck.py` 报「缺首行 `## 标题`」

### ⚠️ 坑：DB 正文直接 `split('\n')[1:]` 会吃掉第一段

DB 的 `rewritten_content` 首行就是正文，不是标题行。若把 DB 内容当 draft 用 `'\n'.join(lines[1:])` 计字数，会静默少算第一段。
10/5 实测：`a1dbaa1d3a84` 因此从 905 被算成 773，差点被误判为「压过头」。
修法：压稿时显式拼 `title + '\n' + body`；或对只含正文的文件按整文件当 body 计数。

## 2. 门禁 + 落盘（逐篇闭环）

```bash
SC=~/.hermes/skills/creative/xhs-write-publish-flow/scripts
python3 $SC/normalize-dunhao.py <dir>                      # 并列项第2个以后的 、→·
python3 $SC/batch_precheck.py --dir <dir> --plan <plan.json>
#   plan.json: {"<key12>": {"fmt":"story","lf":1,"ja":<主素材content_ja长度>}}
for f in <dir>/*.md; do
  ALL_PROXY="" python3 ~/.hermes/skills/writing/renwei-writing/scripts/renwei-pre-commit.py "$f" 2>&1 | tail -3
done
```

落盘用 `sqlite3 UPDATE`（不用 API PUT），**一次写全**：

```python
c.execute("UPDATE news SET rewritten_title=?, rewritten_content=?, publish_mode='rewritten',"
          " publish_xhs=1, score_dims=? WHERE key=?", (title, body, sc, full_key))
```

必须确认：
- `publish_xhs=1` + `publish_mode='rewritten'` 保留
- `related_keys` 未被清空（`length(related_keys)` 复核；UPDATE 不带该列不会动它，但用 API PUT 会全量覆盖）
- `rewritten_title` 与 draft 首行一致

## 3. 已知取舍：深访压到 900 会跌破密度 30%

ja 3k+ 的深访压到 900 字 → 密度 22–27%，低于 30% 红线。

**用户明确指定字数时，字数指令优先于密度红线**（同 `xhs-write-publish-flow` 的规定）。不得自行加字回去、不得因为密度不够而拒绝压稿。
在 `score_dims` 理由里写明「密度 XX.X%（用户指定900字优先）」，供次日复盘区分「漏检」和「授权例外」。

10/5 实测三条低于 30%：`407d23b8` 22.7%（ja 3932）/ `633f00ee` 26.1%（ja 3471）/ `d4469c1d` 27.3%（ja 3280）。

## 4. 10/5 台账（可当格式模板）

| key(40) | 标题 | 字数 | 重评分 |
|---|---|---|---|
| 1b1b6055409e… | 七个人站成一排道歉，那个不在场的人已经解约 | 1440→911 | 8.7→8.6 |
| 407d23b82f55… | 那位当过偶像的人，为什么又回到被观看的位置 | 1209→894 | 8.9→8.8 |
| 633f00ee67c5… | 妹妹的视频，存在丈夫的手机里 | 1068→906 | 8.8→8.6 |
| d4469c1d2d95… | 三个聪明人凑在一起，聊的却是怎么什么都不做 | 1033→897 | 8.4→8.3 |
| 93d99061518c… | 最后一句留给笑声，增本绮良在樱坂的八年 | 939→893 | 8.5→8.4 |
| a1dbaa1d3a84… | 越想养好身体，越容易把自己逼到反面 | 905→892 | 8.1 |

≤900 的四条（`fe95084b` 872 / `da711042` 858 / `09784e351769` 855 / `7e523522` 473）不动。
压完 `##` 小标题全部保留（story ≥2），假名 0，renwei exit 0/2，重评分全部 ≥8 → 不进改稿循环。
