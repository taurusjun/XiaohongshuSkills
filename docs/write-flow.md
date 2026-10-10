# 写稿流程（write）流程图

> 入口：`cli write-full [--key K | --rewrite-today | 默认] [--deliver] [--dry-run] [--split-large] [--split-preview]`
> 代码：`agent/write.py`（编排）、`services/*`（机械服务）、`config/*.json`（硬规则）

## 1. 主流程（run）

```mermaid
flowchart TD
  A["cli write-full<br/>--key / --rewrite-today / 默认"] --> B{选候选}
  B -->|--key| B1[按 key 取 1 篇]
  B -->|--rewrite-today| B2["pick_rewrite_today<br/>今天 S/A/AKB，忽略已写"]
  B -->|默认| B3["pick_candidates<br/>S/A/AKB；去 cluster 重复；<br/>排已写 + 失败队列 + 纯重复"]
  B1 --> C
  B2 --> C
  B3 --> C
  C["跨日漏写提示 + 拆篇(preview/split-large)"] --> D["逐篇 write_one"]
  D --> E{akb_type=bullet?}
  E -->|是| E1["只入库：preselected=1 / publish_xhs=0<br/>score_dims=akb-top-bullet"] --> Z
  E -->|否| F["prepare_package（写前准备）"]
  F --> G["classify_material：LLM 通读 → material_type"]
  G --> H["classify_relations：LLM 判关联类型"]
  H --> H1{含 纯重复?}
  H1 -->|是| H2["grade=C 跳过"] --> Z
  H1 -->|否| I["pkg.relations=合格类型；hist_text=仅合格节选"]
  I --> J["渠道路由：force > channel_hint > compose 自判"]
  J --> K{channel}
  K -->|xhs| K1["_produce(xhs)"]
  K -->|gzh| K2["_produce(gzh)"]
  K -->|both| K3["_produce(xhs) + _produce(gzh)"]
  K1 --> L
  K2 --> L
  K3 --> L
  L{ok?} -->|是| M["update_news 入库<br/>rewritten_*/wechat_* + related_keys=合格<br/>+ cluster 兄弟回指 + 读回校验"]
  M --> M1["写后判定 channel/preselected（both 不丢）"]
  M1 --> M2["content_gate → manual_review 标（不阻断）"]
  M2 --> M3["write_failures.mark_resolved（清旧失败记录）"]
  L -->|否| N["_enqueue_failure（失败队列）<br/>写 error.log"]
  N --> Z["下一条"]
  M3 --> Z
  Z --> D
  D --> O["批末：batches manifest + kana.auto_promote"]
  O --> P{非 dry-run 且非 --key?}
  P -->|是| Q["阶段6 recommend_and_schedule<br/>+ 图集 one-way + 交付(飞书)"]
  P -->|否| R[结束]
  Q --> R
```

## 2. 单篇改稿循环（_produce）

```mermaid
flowchart TD
  A["_produce(channel)"] --> B["need = xhs:story8/news7 · gzh7"]
  B --> C{attempt ≤ 3?}
  C -->|attempt=1 且有首轮| D1["用首轮 compose 结果"]
  C -->|attempt>1| D2["compose(retry_ctx)<br/>含：上版门禁问题 + 评分差距<br/>+ 残留假名的『整句』上下文"]
  D1 --> E
  D2 --> E
  E["dunhao 顿号 → kana.replace →<br/>name_variants 形近字 → fit_title(边界/LLM)"] --> F["precheck 机械门禁"]
  F --> F1{material_type ∈<br/>catalog/multi_artist/commentary?}
  F1 -->|是| F2["去掉『密度』问题（原 skill：只免密度）"]
  F1 -->|否| F3[保留]
  F2 --> G
  F3 --> G
  G{正文为空?} -->|是| G1["block + error.log"] --> C
  G -->|否| H["renwei.review（六类信号,exit1拦）<br/>+ gzh_review"]
  H --> I{有 gate 问题?}
  I -->|有| C
  I -->|无| J["评分 score_content(5维)/score_gzh(3维)<br/>（解析容错 + 重试）"]
  J --> J1{"score ≥ need?"}
  J1 -->|是| K["break（通过）"]
  J1 -->|否| C
  C -->|满 3 轮| L["ok = 无问题 且 score≥need<br/>（字数/评分不放水）"]
```

## 3. 写前准备（prepare_package）

```mermaid
flowchart LR
  A[content_ja] --> B["截断 cap：一般 9000 / export 20000"]
  B --> C["format_route：post/export, tmin/tmax"]
  C --> D["渠道路由：channel_hint > routing.route_detail(hint+confidence)"]
  D --> E["refs=references.relevant(k=6)"]
  E --> F["patterns=feedback_patterns.relevant"]
  F --> G["同事件 cluster → merge_text(≤1200/条) + rel_candidates"]
  G --> H["同人物历史：extract_entities→related.find_related<br/>→ hist_excerpts + rel_candidates"]
  H --> I["pkg{content_ja,target,spec,merge_text,hist_excerpts,rel_candidates,...}"]
```

## 4. 假名两层

```mermaid
flowchart LR
  A["compose 出的正文"] --> B["kana.replace：字典命中即换"]
  B --> C{"仍有假名 → 门禁 >5?"}
  C -->|是| D["反馈『含假名的整句』给 LLM 重译"]
  D --> E["LLM 返回新 title/body"]
  E --> B
  C -->|否| F[通过]
  B --> G["log_pending：新词入 data/kana_pending.jsonl"]
  G --> H["auto_promote：LLM 中译 → config/kana_replace.json<br/>(片段防护；审计 kana_autopromoted.jsonl)"]
  H --> I["人工复核 /kana-autopromoted（可撤回）"]
```

## 关键服务/配置
- 机械：`precheck` `dunhao` `kana` `name_variants` `titles` `format_route` `routing` `renwei` `gzh_review` `content_gate` `split_write`
- 数据/RAG：`references` `feedback_patterns` `related` `batches` `write_failures`
- 硬规则 config：`review_thresholds` `newfont` `kana_replace` `ja_localize` `name_variants` `adult_industry` `off_topic` `routing`
