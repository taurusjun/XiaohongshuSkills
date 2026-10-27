# 10/22 单日批实作（cron 全自动：59 条 → 12 xhs + 1 gzh + 7 bullet + 13 merged + 5 排期）

按 6 阶段管道一次跑完，未向用户提问、未触发发布。本文件记录这批的**可复用做法**与**新踩的四个坑**。

## 1. 台账（结果面）

| 项 | 数 | 说明 |
|---|---|---|
| 当日素材 | 59 | review 存档 `~/.hermes/daily-reviews/2026-10-22.md`（52KB，S 7 / A 12 / B 27 / C 13，AKB大TOP 15） |
| xhs 成稿 | 12 | story 10（含 1 篇 export）+ news 2 |
| gzh | 1 | A7 琥珀歌 → `wechat_` 字段，1728 字，8.6 分（9/22 成人产业规则链） |
| bullet | 7 | 晒照/表纸型，只 `preselected=1 + score_dims='akb-top-bullet'` |
| merged | 13 | 含 S4/A2/A3/A4/A11 弃用与 C1 五条、C4/C5/C6 重复条 |
| 跳过 | 26 | A9 合规、A10 review 暂缓、C 级 5 条、B 级 19 条 |
| 评分 | 8.4–9.2 / news-pass / gzh 8.6 | 一次通过，零改稿轮 |
| 排期 | 10/23 五条 | 08:53 / 12:03 / 14:56 / 18:03 / 19:55 |

## 2. 新坑（四个，按踩到顺序）

### 坑1：`l1_raw_<date>.json` 不存在 → dump_ja.py 直接退出
`dump_ja.py` **不会自己去抓数据**，只读 `~/.hermes/workspace/l1_raw_<YYYY-MM-DD>.json`。当日 review cron 若没留这份 dump（本次就没有），先补一次：

```bash
ALL_PROXY="" curl -s --noproxy '*' \
  "http://127.0.0.1:5000/api/news?date_from=2026-10-22&date_to=2026-10-22&limit=500" \
  -o ~/.hermes/workspace/l1_raw_2026-10-22.json
```

这份 dump 的 rows 自带 `format / is_long_form / title_score / content_ja`，**体裁判定与素材落盘可一步完成**（本次 18 个 key 一次循环打印 fmt/lf/ts/ja_xhs）。

### 坑2：`len(body)` ≠ precheck 的 body 字数，差 15–20 字
python 的 `len()` 把换行、标点都算满分，`xhs_word_count.py` 的算法不同（CJK×1、其余×0.5 一类）。本次按 `len(body)` 写到 801 的稿子，precheck 报 `body=780 < 800` FAIL。**判据一律以 `batch_precheck.py` / `xhs_word_count.py` 为准**，写的时候就按「全文件 850+」落笔（body ≈ 全文件 − 标题行 15–20 字）。

### 坑3：850 余量线要「一次加 40–60 字」，不要 3 轮小补
本次 5 篇落在 800–850 之间，precheck 给 WARN。分三轮各加 15 字共跑 3 次 precheck 才全部越过 850 —— 白耗 2 轮。**纪律：首轮就按 850–900 写；若已出 WARN，一次 patch 补 40–60 字（一段两句话），再跑一次 precheck 收尾。**

### 坑4：排期后再数「今日成稿数」会少 5 条
`preselected=1 AND rewritten_content<>'' AND publish_xhs=0` 这个口径在**排期之后**会排除掉刚设成 `publish_xhs=1` 的那 5 篇（本次显示 7 而不是 12）。**核对批次总量要用不带 `publish_xhs` 的口径**：

```sql
SELECT count(*) FROM news WHERE date(created_at)='2026-10-22' AND rewritten_content<>'';
```

## 3. 一次跑完的落库脚本形状（xhs + gzh + merged + bullet 同一个脚本）

四件事分四段，全部 `sqlite3` 直写、最后 `commit()` 一次，避免 PUT 静默失败与中途异常丢 score_dims：

```python
# ① xhs 正文
conn.execute("""UPDATE news SET rewritten_title=?, rewritten_content=?, publish_mode='rewritten',
    preselected=1, publish_xhs=0, publish_method=?, related_keys=? WHERE key=?""",
    (title, body, PM.get(k,'post'), ','.join(rel), full_key))
# ② 合并/弃用条（主 key 是 12 位前缀记在 score_dims 里）
conn.execute("UPDATE news SET preselected=1, publish_xhs=0, score_dims=? WHERE key LIKE ?",
             (f'merged-into-{main12}', p12+'%'))
# ③ bullet
conn.execute("UPDATE news SET preselected=1, publish_xhs=0, score_dims='akb-top-bullet' WHERE key LIKE ?", (p12+'%',))
# ④ gzh（不进 rewritten_ 字段）
conn.execute("UPDATE news SET wechat_title=?, wechat_content=?, channel='gzh', preselected=0, publish_xhs=0 WHERE key=?", ...)
```

配套要点：
- `PM = {'c0ab475bc388': 'export'}` 只列 ja>3000 的那一篇，其余默认 `post`；本次 4232 字 → export，正文 1357 字（32.1%），不压。
- `related_keys` 一律 `km[p12]['key']`（40 位），并入后 `assert all(len(x)==40)`；收尾再跑一次「related 里不得含本条 key 自身」的断言。
- 脚本末尾一次性打印 `key12 / body_xhs / ja / density / pm / rel 条数`，这一张表就是阶段4的验证输出，不必再单独 curl。
- **gzh draft 不进 plan.json**（会 SKIP），单独校字数/假名/顿号行。

## 4. 阶段6 选篇：本次新增的一条判据

`4536774f5503`（樱坂流媒体只排第12）人物历史数据不差（樱坂 n=13，中位 413，最高 6616），仍被降到**备选**：10/21 19:53 刚发过同一单曲同一组数字（`2c5e9f07` 樱坂卖 73.6 万张）。**同事件的「紧邻第二波」即使换了角度也降备选**——与已发稿相隔不到 24h 的同数据稿会自我分流，比换一个全新题材更亏。

随机探索位用 `random.sample` 抽中 `109ed70bc17f`（大西流星，该人物历史仅 1 条 13 阅读）——结果是「零历史包袱 + 事件全新」的稿子，说明随机位不必刻意挑安全牌，抽到什么就用什么，理由里如实写「历史数据弱但事件首发」即可。
