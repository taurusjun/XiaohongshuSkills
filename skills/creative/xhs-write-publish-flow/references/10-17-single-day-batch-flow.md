# 10/17 单日批实作（61 条素材 → 14 xhs + 1 gzh + 4 bullet + 10 merged）

## 启动状态
- 当日 review 存档 `~/.hermes/daily-reviews/2026-10-17.md` 存在（cron 01:38 正常，61 条，S 8 / AKB大TOP 7 / A 9 / B 10 / C 27）。
- 前一日 10/16 批次已完整落地（53 条里 16 条有正文、26 条 preselected），**无跨日漏写**。
- 启动时 `date(created_at)='2026-10-17'` 为 62 行 vs review 61 行 → 多出的 `7a8c876838e7`（山内瑞葵初舞台，news）是 review 之后入库的同事件重复行，按 9/20 规则挂 `merged-into-87116ded`，**不补写第二篇**。

## 落库台账（14 xhs + 1 gzh）
| key12 | 体裁 | publish_method | 正文 | 密度 | 分数 |
|---|---|---|---|---|---|
| 87116ded511e | story | post | 853 | 86.3% | 8.2 |
| a3a2e27beea6 | story | post | 885 | 41.1% | 8.6 |
| 31d6bd1dc5c3 | story | **export** | 1027 | 32.9% | 9.0 |
| 43f59a98e8ab | story | post | 866 | 82.7% | 8.6 |
| 5c65bef9cc3c | story | post | 873 | 58.4% | 8.5 |
| f4813c3a0c8a | story | post | 908→890(xhs口径) | 47.4% | 9.1 |
| 6e3545ac73ad | story | post | 891 | 52.0% | 8.9 |
| b8cbf5c72940 | story | post | 856 | 56.1% | 8.6 |
| 49a991558e66 | story | **export** | 1734 | 46.3% | 9.1 |
| e35a1b3d99c4 | story | **export** | 1887 | 43.7% | 9.0 |
| 116d00bb7cc1 | news | post | 414 | 60.5% | news-pass |
| 8d590be1028f | news | post | 371 | 68.2% | news-pass |
| 82b1336d1640 | news | post | 286 | 63.3% | news-pass |
| fa58d80dbea8 | news | post | 371 | 63.3% | news-pass |
| 0fb7af762ad4 | story | — | 1568 | — | gzh 8.3 |

## gzh 改道（9/22 + 10/14 规则第三例）
`0fb7af762ad4` 栗林里莉 8 年复归深访（ja 3505）：核心是家庭创伤 / 自立 / 职业选择的人物弧光，成人行业只是背景 → **走 gzh 长文**（`wechat_title/wechat_content` + `channel='gzh'` + `preselected=0`），用词统一「成人影像行业」，标题落在「39岁，她决定回到镜头前」。三分：去魅通过 / 背景锚定通过 / 8.3。

## merged 条（10 条，`preselected=1` + `publish_xhs=0` + `score_dims='merged-into-<主条>'`）
- ca44c5296abe / 7a8c876838e7 → 87116ded（山内瑞葵 初舞台 同事件）
- ab02b0a5b16d → 43f59a98（奥田彩花 初日前会見）
- 54d0fa11fac6 → 31d6bd1d（＝LOVE 9周年 同源重复，选最长 key 当主条）
- dc8d6f452a21 → 49a99155（道枝 宵之月 後編，前後編合并 1 篇）
- 93f4e782a40e → 480c8068（Venue101，与 10-16 已发稿同源，跨日不重写）
- a74e3452ced9 / 516efe2bf922 / 874eb964e7d0 → b8cbf5c7（西畑 逆転男子 4 来源）
- 5584aa99ab35 → 8d590be1（岩本照×高橋尚子 同场景）

## bullet（4 条，`score_dims='akb-top-bullet'`，只入库不写正文）
8ce6a6b334e4（兒玉遥 写真集）、04c7191a9e61（西野未姬 午餐照）、7a2e283cce4b（井上和 GirlsAward）、d1c6ef8bc64c（金村美玖 機微）

## 跳过的条目（事实源，供次日跨日检查）
- **B 级 10 条全部不写**（热度分级≠体裁，本轮按「覆盖 S+A+AKB大TOP」执行）：d3c2d3bb / db95ccc9 / e6951ccb / cdad9134 / 9750391f / f122917c / 60923534 / 9ec645f2 / 3ecf39f6 / 856a3979
- **C 级 27 条**：集群内重复 15（5ee52457 / 5b8a71eb / 5584aa99→已挂 merged / a74e3452→已挂 / 516efe2b→已挂 / 874eb964→已挂 / 81a8bbdd→related / c1753173→related / 4453363 / f0a6ec1a / 212acace / 2b8e0b33 / 57314907 / 5e39f457）、日期陷阱 2（d74249ba 10-16 已发 id=9209 / 1b5778c9 10-16 已覆盖）、**非目标赛道 10**（28be9c32 竞艇 / 734ebe45 广播 / 8617c685 ASUS ProArt 标签误付与 / d15ab204 + 6afa8e52 WEC 赛车 / 2b3c94da Mrs.GREEN APPLE 标签误付与 / 8cd45111 + da0b24e2 Billboard 速报 / acfb3a28 / a1e368bf / 9461c6e2）

## 本轮新坑 / 复验
1. **顿号行是最大 FAIL 源**：`batch_precheck` 首轮 11/15 FAIL，10 篇全是「一行 ≥2 个、」。修法以「逗号拆句」为主（人名/地名/曲名列表才用 U+00B7 ·），比 `normalize-dunhao.py` 盲替换更自然 —— 该脚本会把动词短语也换成 ·（10/14 已记录，本轮继续适用）。
2. **假名计数抓的是「假名汉字的团名」**：`青い向日葵`（い×14）整篇命中 kana=14，靠 precheck 才揪出 → 汉化「青向日葵」。凡「汉字+送假名」的团名/曲名必须整体定中文写法。
3. **export 与 post 的 900 口径是两个数**：`xhs_word_count.py`（换行不计）才是发布口径；`len(rewritten_content)`（含换行）会多算 18~40 字。f4813c3a 按 len 是 945（需压），按脚本是 927；压到脚本口径 890 后 len 显示 908 —— **判断「是否 >900」必须用脚本口径，判断落库内容必须用 DB 值**。
4. **`pub_time_plan.py` 的 jitter 可能给出「整点后 1 分钟」**（本轮 20:01）：脚本只保证分钟 ≠ 0，不保证「刻意避开整点」。**做法：先无 --keys 干跑几个 seed，挑一组 5 个偏移都 ≥3 分钟的 seed（本轮 seed=1 → 08:56/11:54/14:55/18:07/20:06）再 --apply**。
5. review 存档的「AKB大TOP 事件型→全文」与第3层「纯重复→并入」冲突时，按**第3层（可操作层）**执行合并（ca44c529→87116ded、ab02b0a5→43f59a98），避免同事件两篇互相稀释。
