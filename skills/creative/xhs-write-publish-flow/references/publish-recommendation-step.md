# 阶段6 · 待发布推荐（写稿流程的最后一步）

> 写稿 + review 全部完成后**自动进入**本阶段，不向用户请示、不中途汇报。
> 本阶段只**排期**，绝不**发布**（禁止 `POST /api/trigger-publish`）。

## 0. 产出口径（一次给全）

1. **5 篇推荐**：每篇给「标题 + 一句话理由（含人物历史数据）+ 完整 40 位 key + 预发布时间」。
2. **2 条备选补位**。
3. **数据总览**：断更天数、本月每日发布量、候选池规模、当前待发队列条数。
4. **落库确认**：5 篇 `xhs_pub_time` + `publish_xhs=1` 已验证。

## 1. 选篇：4 篇数据驱动 + 1 篇随机探索

**候选池**（当日新写优先）：
```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
 "SELECT substr(key,1,12), substr(rewritten_title,1,30), format, is_long_form, score_dims
    FROM news
   WHERE preselected=1 AND publish_xhs=0
     AND COALESCE(rewritten_content,'')!=''
     AND date(created_at)>=date('now','-2 day')
   ORDER BY created_at DESC"
```

**4 篇「历史数据推荐」排序口径**（见 `xhs-publish-workflow` 的「发布节奏盘点」节）：

| 优先级 | 判据 |
|--------|------|
| 1 | 时效性素材（当天/近两日事件，过夜打对折）——必须说明「今天就得发」 |
| 2 | 人物历史数据最强（`search=<人名>&publish_xhs=published` → 已发条数/中位 `xhs_views`/最高/最近3条） |
| 3 | 分数最高但本人近期走低的 → 标「质量牌」而非「流量牌」，别让用户误判预期 |

约束：**同人物/同题材不挤在同一天**；同一事件的稿件不挨着发；避开明天已有排期的题材（见 §3）。

**1 篇「随机探索」**：从 4 篇之外的剩余候选中**真随机抽 1 篇**（`random.sample`，不看分数/data）。目的：探索未被历史数据覆盖的题材/人物，防止推荐被既有流量池锁死。输出时**明确标注「随机探索」**。

> 数据面板**先出**再给结论——这个用户对「先摆数据后给结论」敏感。

## 2. 预发布时间：明天 09/12/15/18/20（带随机偏移，禁整点）

用独立脚本 `scripts/pub_time_plan.py` 生成（东京时区「明天」）：

```bash
cd ~/.hermes/skills/creative/xhs-write-publish-flow/scripts
# 1) 看计划（不写 DB）
python3 pub_time_plan.py --seed 42
# 2) 直接写库：xhs_pub_time + publish_xhs=1（按顺序对应 5 个时段）
python3 pub_time_plan.py --keys <K1>,<K2>,<K3>,<K4>,<K5> --apply
```

- 默认时段 `09:00,12:00,15:00,18:00,20:00`，偏移默认 `±8min`，**区间内随机且排除 0**，
  结果分钟必不为 `:00`（示例：`08:55 / 11:52 / 14:59 / 17:59 / 19:56`）。
- 需要复现同一批时间时带 `--seed`；换时段用 `--slots`；换日期用 `--date`。
- `--apply` 只做 `UPDATE news SET xhs_pub_time=?, publish_xhs=1 WHERE key=?`，
  写后自动读回校验；`--no-publish-flag` 则只写 `xhs_pub_time`。
  **不碰** `publish_mode` / `publish_method` / `related_keys` / `rewritten_*`。

## 3. 冲突检查（排期前必做）

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
 "SELECT substr(key,1,12), substr(title,1,28), xhs_pub_time
    FROM news WHERE xhs_pub_time LIKE '<明天日期>%' ORDER BY xhs_pub_time"
```

若明天某时段已被占用，把冲突篇排除出候选（或说明「该时段已排」），不要两条挤同一时段。

## 4. 铁律 / 坑

- ❌ **不触发发布**：本阶段只写 `xhs_pub_time` + `publish_xhs=1`；`POST /api/trigger-publish` 一律禁止。
- ❌ 不设 `publish_time`（系统发布后自动填）。
- ❌ 不要用 API `PUT` 写 `xhs_pub_time`（全量覆盖会清空其它字段）——用脚本的 `sqlite3 UPDATE`。
- ✅ `publish_xhs=1` 是「已排期/待发布」标记，**不会自动发布**（发布只由 `trigger-publish` 或人工触发）。
- ✅ 保留写稿阶段设好的 `publish_method`（长文 `export` / 图文 `post`）与 `related_keys`。
- ⚠️ 与「写稿入库只设 `preselected=1`」的分工：那条规则管**入库**，本阶段是**流程最后一步**，
  只对**被推荐的这 5 篇**设 `publish_xhs=1`，其余稿子维持 `preselected=1 / publish_xhs=0`。
