# 10/26 单日批实作：47 条 → 11 xhs（story 8 / news 3）+ 5 merged

**结论先行：稿件与门禁零返工（precheck FAIL 0、renwei exit1=0、story 8/8 ≥8.3 分）。坑全在两个地方——
「key 前缀位数」被重新踩了一遍（第 6 次），以及「阶段6 闸门几乎全灭时该回退到哪」没有规则。**

---

## 一、8 位 vs 12 位：同一条 dict 链上的三种静默形态

10/25 的「文件名 14 位 → 导入循环 12 位 → continue 静默跳过」是第一种。本批是另外三种，
**共同点是全部不报错**：

| 形态 | 写法 | 症状（无异常、无日志） |
|---|---|---|
| A | `REL={'98c03f1c':[...]}`、`METHOD={'98c03f1c':'export'}`，循环 `k12=文件名[12位]` | `.get()` 全取默认：`rel=0`（`related_keys` 被写成空串**清空**）、`method='post'`（5412 字长文路由成图文） |
| B | `K={r['key'][:8]:r['key']}` 建 map，评分字典却用 12 位键 | `KeyError`，炸在第二段脚本（与前一段的 UPDATE 不是同一次跑，排查时容易误判「上一段也没生效」） |
| C | `full()` 里 `assert len(m)==1`，遇跨日 related（10/24 的 `6c20fec5` 不在今日 list dump） | `AssertionError` 中止循环，**前 6 行已 commit**，整批看起来像「跑到一半死了」 |

### 统一修法（写进脚本，别靠记性）

```python
def full(p):                                  # 唯一解析口，接受任意位数前缀
    m = [k for k in KEYS if k.startswith(p)]  # ① 今日 list dump
    if len(m) == 1:
        return m[0]
    m = [r[0] for r in conn.execute("SELECT key FROM news WHERE key LIKE ?", (p + '%',))]  # ② DB 兜底（跨日/历史）
    if len(m) != 1:
        print('MISS', p)                      # ③ 禁止 assert：一处失败不该中断整批
        return None
```

- 所有 dict 键、draft 文件名、`METHOD` 查表一律由**同一个** `k12 = full[:12]` 派生。
- `REL` / `METHOD` 的键写完整 12 位，或直接统一走 `full()`；不要一半 8 位一半 12 位。
- **入库后立刻复核这两项**：`related_keys` 条数 与 `publish_method`。它们是被静默改坏的唯一两个字段
  （正文永远是对的，所以「字数看着没问题」不能作为映射正确的证据）：
  ```bash
  sqlite3 $DB "SELECT substr(key,1,12), publish_method, length(rewritten_content),
                      [len(x) for x in related_keys...] FROM news WHERE date(created_at)='今天'"
  ```
- 顺带确认 `related_keys` 每段 `len==40` 且不含本条自身（9/17 的 self-key 坑）。

---

## 二、阶段6 闸门：当日 11 条只有 2 条过 `--strict`

`pub_history_gate.py --strict --keys <当日全部>` 结果 2/11，9 条驳回。**必须逐条分辨真假**，
但处置一律按机械规则（换稿），只是汇报要写清真伪：

| key | 驳回理由 | 真假 |
|---|---|---|
| `98c03f1c` | 佐藤绮星 / 前田敦子 2 天前刚发（10/24 已发同人复刻 MV） | **真重复** |
| `29364542` | 岩本照 近14天2条 | **真**（题材疲劳） |
| `008b89ba` | 向井康二 2 天前 + M!LK / 上白石萌音 2 天前 | 半真（主角向井确实撞） |
| `c5b51af6` `acaf6637` | `欅坂46` / `桜坂` / `欅坂` 近14天 3–5 条 | **误伤**（樱坂新旧团名同一 tag） |
| `0828ea2d` | `YOASOBI` 3 天前 | **误伤**（同馆对照的共演 tag） |
| `7ac4c6ac` | `EV电动车` 2 天前 | **纯误伤**（汽车分类带来的脏 tag） |
| `41984934` | 大西流星 14天2条 + `游戏` | 半误伤（主角是细田佳央太） |
| `acea1413` | `日本明星` 1 天前 | **纯误伤**（泛化 tag） |

### 回退顺序（本批新立的规则）

当日新稿过闸不足 4 篇时，**补位只能取 `created_at` 最近 1–3 日的过闸候选**（本批取 10/25、10/23），
**绝不从全库过闸池里捞**：本轮全库 914 条过闸，最早到 **6 月**（`954b38caa1a9` 小嶋阳菜、
`245997d1f319` 能条爱未…都是 6 月稿、从未发布、人物数据很漂亮）。捞它们 = 把四个月前的新闻
排进明天的队列，数据再好看也是过期选题。

判据一句话：**闸门管「会不会自我分流」，时效管「值不值得发」，两个都要过。**

---

## 三、`pub_time_plan.py --apply` 不会踢图集下载（真实缺陷，需手工补）

排期成功（5/5 verify OK）后，脚本尾巴的图集环节 5 条全 skip：

```
skip (...) -> URL can't contain control characters. "/api/gallery-download/('0f9fcd9e..."
```

即它把 tuple 的 repr 拼进了 URL。同时注意：`--apply` 走的是 `sqlite3 UPDATE`，**不会触发**
`PUT /api/news` 那条自动起线程的路径。→ **排期后必须手工逐条补**：

```bash
curl -s --noproxy '*' -X POST "http://127.0.0.1:5000/api/gallery-download/$FULLKEY" \
     -H 'Content-Type: application/json' -d '{"gallery_url":""}'
```

再按 `xhs-publish-workflow` 的规矩：等 `gallery-status=done` 且多张 → 回填 `publish_images`
→ 才允许发布（否则线上只有封面一张图）。

---

## 四、占位条卫生检查的假警报

`score_dims LIKE 'merged-into-%' OR ='akb-top-bullet' AND publish_xhs=1` 查出 4 条，
一度以为是残留占位条。实际全部 `xhs_pub_time` 在过去、`xhs_views>0`、无 `note_id` 缺失问题
→ **是历史上真的被当作占位发出去的老条**，不是本批残留。

**判据加一条：`AND xhs_pub_time > datetime('now','localtime')` 才是「残留占位」，过去的不要动。**

---

## 五、renwei 收尾改写的连带风险（老坑新证据）

为消掉 `0828ea2d` 的一处「格言公式」改写了句子，改完 renwei 仍 exit 2，但 `precheck` 变成
**FAIL 1**——新句子写成了 `当地人口、远征成本、同期的竞争对手，才决定…`（2 个顿号）。
修成「当地人口和远征成本，再加上同期的竞争对手」才归零。

**纪律：renwei 命中点改写后必须回跑 `batch_precheck`（顿号行是真条件），不能只看 renwei exit code。**
这不是新规则，但本批证明「为过 renwei 的改写」是最稳定的顿号行来源之一。

---

## 六、本批数字（可作校准基线）

| 项 | 值 |
|---|---|
| 素材 | 47（去重 39 事件）；S5 / A11 / AKB TOP3 / B16 / C12 |
| 成稿 | xhs 11（story 8 + news 3）+ merged 5 + gzh 0 + bullet 0 |
| 字数 | export 1 篇 2093；story post 881–957；news 300–373 |
| 密度 | 36.4 / 37.8 / 41.1 / 42.3 / 46.1 / 46.3 / 59.3 / 66.8 / 68.2 / 70.3 / 87.4 % |
| 评分 | 8.9 / 8.8 / 8.7 / 8.5 / 8.5 / 8.4 / 8.3 / 8.3 + 3×news-pass |
| 门禁 | precheck FAIL 0（11/11 在 plan 内）；renwei exit1=0，exit2=2（单处非聚集，接受） |
| 跳过 | 8 条（review 明示：多波次重复 / 赛道外 / 合规风险） |

**落笔校准**：story post 目标 900±50，实测中文落笔比自估长 5–10%（本批 1027 需回修到 943），
写的时候按 900 收，别按 950 收；export 按 `ja×0.32`（5412 → 2047，37.8%）一次到位即可。
