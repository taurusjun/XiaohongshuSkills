# 10/27 单日批 · 台账与两个新坑

**素材：** 55（review 分级 S7 / A12 / AKB大TOP6 / B16 / C14）
**落库：** xhs 成稿 16（story 6 + news 10）+ bullet 3 + merged 5 = preselected 24；gzh 0
**门禁：** batch_precheck FAIL 0（16/16 在 plan 内）；renwei exit1=0，exit2=2（格言公式各 1 处，非聚集接受）
**评分：** 9.1 / 8.6 / 8.3 / 8.2 / 8.2 / 8.0（story 6 篇全部 ≥8）；news 10 篇 news-pass
**排期：** 10/28 08:53 / 12:03 / 14:54 / 18:05 / 20:08（5 条，gate --strict exit 0）

## 坑 1：写稿脚本里 dict 键用 8 位、循环变量是 12 位 → 全批静默回落默认值

入库脚本第一版写：

```python
p12 = f.replace('xhs_draft_','').replace('.md','')   # 12 位
METHOD = {'5295d787':'export', ...}                  # 8 位
conn.execute("... publish_method=? ... related_keys=? ...",
             (METHOD.get(p12,'post'), ','.join(REL.get(p12,[])), k))
```

`METHOD.get(p12)` 恒为 `None` → 全部退回 `post`；`REL.get(p12,[])` 恒为空 → **16 篇 related_keys 全被写成空串**。
症状与 10/26 记录的形态 A 同源：**不报错、正文正确、只有 method 与 related 两项是坏的**。
本批靠「入库后 SELECT publish_method, related_keys 逐条看」当场揪出并补写；若不看，1526 字长文会被当成 900 字图文发。

**纪律：** 同一脚本里所有 dict 查找的键必须和循环变量同源（统一 `p8 = p12[:8]`，或在建 dict 时就用 12 位）。

## 坑 2：review 的 55 条 ≠ 入库时刻的 DB 条数（迟到行是 dedup 丢掉的那两条）

04:00 写稿时 `l1_raw_2026-10-27.json` 是 55 行；06:00 收尾复查 `date(created_at)='2026-10-27'` 变成 **57**。
新增两条正是 review 正文里标注的 discarded 行（`128a2dde` 335 字、`294ae34b` 381 字，铃木佑捺同事件），
它们在 l1 dump 里被 dedup 过滤，但 DB 里存在且 `preselected=0`。

处置：不补写第二篇，两条标 `score_dims='merged-into-4a23c78aaffb'`（主条已承载全部信息）。
**收尾必查：** `SELECT count(*) FROM news WHERE date(created_at)='<今天>'` 与开工时的 dump 行数比对，差额逐条定性。

## 坑 3：pub_time_plan.py 只认 40 位全键

传 12 位前缀 → 5 条全 `FAIL <key> 不存在`（脚本尾部才报，前面已打印时间表，容易误以为成功）。
**先 `SELECT key FROM news WHERE substr(key,1,12)=?` 解析全键再传。**

## 坑 4：排期后图集下载仍有 3/5 缺口

`pub_time_plan.py --apply` 的图集环节照旧因 URL 拼 tuple 全部 skip；手工 `POST /api/gallery-download/<40位key>` 后：
2 条拿到图（16 张 / 5 张，已回填 `publish_images`），3 条返回「未检测到图集链接」（Yahoo 原文无图集）。
→ **只有真有图集才回填；空图集不要写空数组，留着让发布侧用封面。**

## 选题口径实作

- 11 条被 `pub_history_gate --strict` 驳回，其中 6 条是脏/泛 tag 误伤（`日本明星` `EV电动车` 共演 tag 等），5 条是真重复（道枝/井上和/上白石/高桥凛 0 天前）。
- 当日 5 条过闸 → 4 篇历史数据推荐 + 1 篇随机探索（随机池限定 `created_at >= -3day` 的过闸候选，**不碰四个月前的全库池**）。
