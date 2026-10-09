# 发布规律（feedback_patterns）· 结构化 · 决策接入 · Admin UI

> review **第4层**（发布数据回顾）每天从已发布笔记的真实数据里提炼「规律」，跨会话累积。
> 本文说明**结构化字段含义**、**决策接入**与**人工 review 的 Admin 页面**。
> 配套 [review-skill-parity.md](review-skill-parity.md)（第4层）· [write-skill-parity.md](write-skill-parity.md)（写稿侧消费）。

## 1. 双载体：md（人类报告）+ 表（决策检索）

| 载体 | 位置 | 作用 |
|---|---|---|
| **md 报告** | 种子（git 跟踪、只读）`skills/creative/xhs-daily-material-review/references/data-feedback-patterns.md`；**运行时**（data 卷、可写）`data/feedback/data-feedback-patterns.md`，可用 `XHS_FEEDBACK_MD` 覆盖 | 人类可读的累积报告 + review LLM 续写上下文（`### N.` 模式节 + `## 跨会话趋势表`） |
| **表 `feedback_patterns`** | DB（`data/news_dev.db`） | **结构化规律**，供**决策检索**（`relevant()`）/ Admin 编辑 |

**为什么不全用 md**：md 适合人看/审计，但"规律"的语义（题材/标题类型/结论方向/对策）无法精确查询 → 决策检索走结构化的表。

> 该 md 是**第4层的数据产物**，**不属于写稿知识库**：`services/references.py` 已把种子 `data-feedback-patterns.md`（及 `.bak-*`）从 `references` 检索中**排除**，写稿只通过 `feedback_patterns.relevant()`（读表）消费规律，避免重复/读到旧种子。

## 2. 结构化字段（facets）

表结构：`no(PK) / date / title / body(md原文) / tags` + **8 个 facets**：

| 字段 | 含义 | 取值 |
|---|---|---|
| `category` | 类目 | 娱乐 / 经济 / 体育 / 社会 / 其他 |
| `genre` | 题材 | 自由短词（争议发言 / 行业分析 / 亲情 / 偶像退圈 / 写真 / 宣传…） |
| `title_style` | 标题类型 | 自由短词（分析性长标题 / 数字对比 / 不点名争议 / 半开玩笑请求 / 悬念…） |
| `publish_mode` | 发布方式 | normal / rewritten / caption / free / any |
| `direction` | 结论方向 | 爆发 / 稳态 / 零曝光 / 延迟归位 / 降级 / 中性 |
| `action` | 对策 | 优先 / 慎用 / 改道gzh / 降级 / 跳过 / 中性 |
| `confidence` | 置信 | 待验证 / 待第2例 / 已确认 |
| `entities` | 人物/IP | 逗号分隔（日文原形优先，用于检索命中） |

示例：
```
no=132  category=娱乐  genre=行业分析  title_style=分析性长标题
        direction=零曝光  action=改道gzh  confidence=待第2例  entities=FRUITS ZIPPER
no=134  category=娱乐  genre=争议发言  title_style=不点名争议
        direction=爆发    action=优先     confidence=待验证    entities=川荣李奈,桥本环奈
```

## 3. 写入路径

1. **review 第4层**：LLM 在输末机器 JSON 给 `feedback.patterns`（含上述 facets）→ `services/feedback_patterns.update(...)`：
   - **渲染 md**（`### {no}. {title}` + body）插到 `## 跨会话趋势表` 之前；趋势行追加表末；**同日幂等**；
   - **写表**：接续 `MAX(no)+1` 分配编号并 upsert。
2. **历史回填**：`ops/backfill_feedback_facets.py`（对缺 `category` 的行用 LLM 抽取 facets，一次性）。
3. **路径**：首次运行若 `data/feedback/…` 不存在，从仓库**种子**拷贝（`feedback_patterns._fb_path()`）。

## 4. 决策接入

- 检索：`feedback_patterns.relevant(query, k=3)` —— 字段化打分 `entities×3 > category/genre/title_style×2 > title/tags/body×1`，命中回带 `方向/对策/题材/标题`；无命中回退最近 k 条。
- **write 写稿**：`prepare_package` 按标题/日文标题检索 → `compose` 注入「=== 历史发布规律 ===」（据此避坑，如"分析性长标题零曝光"）。
- **write 阶段6 推荐**：每篇推荐 `reason` 追加命中的历史规律（选题参考）。

## 5. Admin UI（人工 review）

- 页面 **`/feedback-patterns`**（侧栏「📈 发布规律」入口）：
  - 表格列 `# / 类目 / 题材 / 标题类型 / 方向 / 对策 / 置信 / 实体 / 标题·正文`；
  - 搜索（多词 AND，命中 title/body/tags/genre/title_style/entities/action/direction/category/confidence）+ 类目过滤；
  - 「编辑」弹窗改 facets + title/body/tags → 保存写库。
- REST（薄壳 `web/feedback_views.py`）：
  - `GET /api/feedback-patterns?q=&category=` → 列表
  - `GET /api/feedback-patterns/<no>`
  - `PUT /api/feedback-patterns/<no>`（body = 可编辑字段）

## 6. 运行与验证

```bash
.venv/bin/python -c "import sys;sys.path.insert(0,'.');from services import feedback_patterns as f;print(len(f.all_patterns()))"   # 表内模式数
.venv/bin/python ops/backfill_feedback_facets.py            # 回填历史 facets
curl -s "http://127.0.0.1:5000/api/feedback-patterns?category=娱乐"
# 页面：http://<host>:15000/feedback-patterns
```

## 7. 涉及文件

- `services/feedback_patterns.py`（聚合 / md 追加 / 结构化表 / 检索）
- `agent/review.py` + `agent/prompts/review.md`（第4层输出 `feedback.patterns`）
- `agent/write.py`（写稿 + 阶段6 消费规律）
- `web/feedback_views.py` + `web/app.py`（Admin 页面/导航）
- `ops/backfill_feedback_facets.py`（历史回填）
