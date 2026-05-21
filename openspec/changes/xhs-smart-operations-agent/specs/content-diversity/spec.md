## ADDED Requirements

### Requirement: 体裁适用性前置判断，轮换从适合的体裁中选取
系统 SHALL 在内容生成前，先由 LLM 判断该篇原始资讯适合哪些体裁，再从适合的体裁中按轮换顺序选取——而非把轮换体裁直接强加给不适合的内容。

**四种体裁及其适用条件：**

| 体裁 | 标识 | 适用条件 |
|---|---|---|
| 资讯体 | `news` | 任何内容都适用（最低门槛，兜底选项）|
| 故事体 | `story` | 内容有时间弧度：有起伏、前后对比、意外转折；**或**内容为长文（含翻页），如长访谈、纪念/回顾类报道 |
| 盘点体 | `ranking` | 内容可以提炼出≥3个可并列比较的元素（代表作、时期、风格等），或有自然的排名/清单属性 |
| 对比体 | `comparison` | 内容含有两个或以上可以比较的对象（两个人物、两个时期、两种风格） |

**选取逻辑（优先级）：**
```
今日轮换目标体裁（来自 content_format_rotation）
        ↓
是否在该文章的「适用体裁」列表中？
        ├─ 是 → 使用轮换目标体裁  ✓
        └─ 否 → 从适用体裁中选择与轮换目标最接近的一个
                 仍无合适选项 → 使用 `news`（兜底）
```

`news` 永远适用，不会出现无合适体裁的情况。

#### Scenario: 轮换目标体裁与文章内容匹配
- **WHEN** 今日轮换目标为 `ranking`，文章是「XX十大代表作回顾」
- **THEN** LLM 判断 `format_suitability: ["news", "ranking", "story"]`，轮换目标 `ranking` 在列表中 → 使用盘点体生成

#### Scenario: 轮换目标体裁不适用，自动降级
- **WHEN** 今日轮换目标为 `comparison`，文章是「XX参加某品牌发布会」（单一事件，无可比较对象）
- **THEN** LLM 判断 `format_suitability: ["news"]`，`comparison` 不在列表中 → 使用 `news` 兜底，不强行套用对比体；在飞书摘要中记录「今日轮换目标 comparison 未生效（文章不适合），已使用 news」

#### Scenario: 运营者覆盖体裁选择
- **WHEN** 运营者在飞书审批卡片点击「修改体裁」，选择了 LLM 未认定适用的体裁
- **THEN** 系统使用运营者指定的体裁重新生成，同时提示「该体裁可能不适合本文内容，请确认」；最终由运营者决定

### Requirement: Yahoo 长文翻页抓取，长文内容优先 story 体裁
系统 SHALL 在抓取 Yahoo Japan 文章时，检测正文是否有分页（翻页特征），若有则自动抓取所有分页并拼接为完整正文，确保后续生成和体裁判断基于完整内容。

**翻页检测特征**（Yahoo Japan 常见模式）：
- 页面底部存在「次のページへ」/「→」翻页按钮
- URL 参数含 `page=2`、`p=2` 等分页标记
- 正文末尾出现「(続く)」/「（つづく）」标记

**长文判断标准**：拼接后正文字数 > 800 日文字符（约相当于 2+ 屏内容）。

长文是 `story` 体裁的强信号，原因：访谈/回顾/纪念类报道天然有叙事弧度，强制压缩为资讯体会丢失文章价值；完整长文提供了足够素材让 LLM 构建起伏叙事。

#### Scenario: 检测到翻页，自动抓取完整正文
- **WHEN** 抓取 Yahoo Japan 文章时，DOM 中检测到翻页标志
- **THEN** 系统依次请求后续分页 URL，将各页正文拼接后写入 `news.content_ja`，并标记 `news.is_long_form=True`、`news.page_count=N`

#### Scenario: 长文自动注入 story 到体裁适用性
- **WHEN** `news.is_long_form=True`
- **THEN** 在体裁前置判断时，`story` 自动进入 `format_suitability`（无论 LLM 判断结果如何），理由为「长文/多页内容，具备故事体素材深度」

#### Scenario: 长文优先选取 story（如在轮换序列中）
- **WHEN** `is_long_form=True` 且今日轮换目标恰好为 `story`
- **THEN** 直接使用 `story`，无需等待 LLM 单独判断适用性（已由 is_long_form 保证）

#### Scenario: 翻页抓取超时或失败
- **WHEN** 某分页请求超时（> 10 秒）或返回非 200
- **THEN** 使用已成功抓取的页面内容继续处理，记录 `page_fetch_error=True` 和实际成功页数；不因翻页失败丢弃整篇文章

#### Scenario: 短文章不受影响
- **WHEN** 文章正文 < 800 日文字符，且无翻页标志
- **THEN** 正常处理，`is_long_form=False`，体裁适用性纯由 LLM 判断

### Requirement: 体裁适用性由 LLM 在翻译阶段同步判断，输入使用完整摘要
系统 SHALL 在翻译+初步处理日文原文阶段，增加体裁适用性判断，**使用 300-500 字的完整正文摘要**作为判断依据（而非截断到前 100 字，否则日本娱乐资讯的正文开头通常是背景铺垫，精华在中间，会导致 LLM 判断不出 `ranking`/`comparison` 等体裁）。

**合并 prompt 结构：**
```
任务1：翻译以下日文标题和摘要为中文
任务2：从 [news, story, ranking, comparison] 中判断适合的体裁（1-3个）

日文标题：{title_ja}
日文正文摘要（前500字）：{content_ja[:500]}

返回 JSON（两个任务独立输出，互不影响）：
{"title_zh": "...", "summary_zh": "...", "format_suitability": ["news"], "reason": "..."}
```

**合并 prompt 的 fallback 规则：** 翻译和体裁判断独立 fallback，互不影响——翻译失败重试，体裁判断失败仅降级为 `["news"]`（不触发翻译重试）。

#### Scenario: 使用完整摘要判断体裁适用性
- **WHEN** 调用 LLM 翻译日文原文时
- **THEN** 同一 prompt 中追加体裁适用性判断，判断输入使用 `content_ja[:500]`（而非 `[:100]`）；一次请求返回 `{title_zh, summary_zh, format_suitability}`

#### Scenario: 体裁判断失败时独立降级
- **WHEN** LLM 返回的 JSON 中 `format_suitability` 字段缺失或格式错误
- **THEN** `format_suitability` 降级为 `["news"]`，翻译结果正常使用，不触发整体重试

#### Scenario: Q&A 格式长文不自动注入 story
- **WHEN** 文章 `is_long_form=True`，但正文中含有 ≥ 3 个 Q&A 标识（「Q:」「—」「質問：」「聞：」等采访问答格式）
- **THEN** `story` 不自动注入 `format_suitability`（Q&A 结构定死，难以改写成叙事体），仍由 LLM 判断；`ranking` 作为候选（访谈中通常有多个可并列的话题点）

#### Scenario: 体裁判断结果持久化
- **WHEN** LLM 返回 `format_suitability`
- **THEN** 写入 `news.format_suitability`（JSON 数组），供 planner 和内容生成阶段读取

### Requirement: 内容体裁轮换按周循环，不强制每日覆盖
系统 SHALL 维护跨日的体裁轮换索引（存储在 `agent_strategy` 表），每次成功使用某体裁后推进索引。目标是每周内出现多种体裁，而非每天都必须覆盖所有体裁。

轮换配置存储在 `agent_strategy.json` 的 `content_format_rotation` 字段（如 `["news", "story", "ranking", "news", "comparison", "news", "story"]`，7 天一循环）。

#### Scenario: 体裁成功使用后推进索引
- **WHEN** 某文章以 `ranking` 体裁成功生成并进入候选池
- **THEN** 轮换索引推进，下次选择时从下一个体裁开始（如 `comparison`）

#### Scenario: 体裁降级为 news 时索引不推进
- **WHEN** 轮换目标为 `comparison` 但降级使用了 `news`
- **THEN** 轮换索引不推进（下次仍尝试 `comparison`），避免因内容不适合导致某体裁永远被跳过

### Requirement: 双层相似度检查，避免重复内容触发平台检测
系统 SHALL 在候选文章进入发布队列前，执行两层相似度检查：词汇层（TF-IDF）和结构层（句式模式）。

> **背景：** 日本娱乐资讯的高度相似性不主要体现在词汇重叠（每篇都有不同艺人名），而体现在 AI 使用相同 prompt 生成的**句式结构和段落模式**（开头句式、段落起始词、结尾句式高度一致）。仅做 TF-IDF 检测会漏掉这类结构性同质化。

**词汇层检查：** 与同一 `vertical` 下近 30 天已发布内容的 TF-IDF 余弦相似度 > 0.6。

**结构层检查：** 提取该文章的「开头三字句式 + 段落首词列表 + 结尾句式」，与近 30 天内容做字符串匹配，连续 5 篇出现相同结构模式时触发告警。

#### Scenario: 词汇层检测到高相似内容
- **WHEN** 候选文章与同 `vertical` 近 30 天已发布文章 TF-IDF 余弦相似度 > 0.6
- **THEN** 文章标记 `similarity_warning=True`，飞书审批卡片标注「⚠️ 与已发布内容话题高度重合，建议跳过或修改体裁后重生成」

#### Scenario: 结构层检测到语言模式过于雷同
- **WHEN** 近 30 天已发布内容中，连续 5 篇的开头句式 / 段落首词 / 结尾句式高度重复
- **THEN** 飞书推送「⚠️ 最近 5 篇内容句式结构雷同，建议检查内容生成 prompt 模板，考虑增加句式变化指令」；不影响当前候选文章的审批流程

#### Scenario: 历史数据不足时跳过检查
- **WHEN** 近 30 天已发布内容 < 5 篇
- **THEN** 跳过相似度检查，不影响候选文章流程

#### Scenario: 多垂类模式下相似度检测仅在同垂类内进行
- **WHEN** `active_verticals` 包含多个垂类
- **THEN** 相似度检测只与同一 `vertical` 的已发布内容比较，不跨垂类（娱乐和美食内容天然相似度低，跨类检测无意义）

### Requirement: 账号人设一致性约束
系统 SHALL 在每日计划生成时，检查核心锚定话题（`focus_topics`）的配额占比，冷启动期强制集中。

#### Scenario: 冷启动期强制话题集中
- **WHEN** `growth_stage=cold_start` 且当日计划中核心话题占比 < 80%
- **THEN** planner 自动压缩探索配额直到占比满足要求；调整情况在飞书摘要中说明

#### Scenario: 稳定期话题多样性放开
- **WHEN** `growth_stage=stable`
- **THEN** 不强制话题集中度约束，按配置的 exploration_ratio 自由分配

### Requirement: 低非资讯体覆盖率告警，提示扩展素材来源
系统 SHALL 在每日执行体裁分配后，如果当日所有候选文章的 `format_suitability` 中非 `news` 体裁覆盖率过低，通过飞书提示运营者。

#### Scenario: 连续多天非 news 体裁覆盖率低
- **WHEN** 连续 3 天当日候选文章中，`format_suitability` 包含 `ranking` 或 `comparison` 或 `story` 的比例 < 20%
- **THEN** 飞书推送「⚠️ 近 3 天素材以简短资讯为主，非资讯体覆盖率 < 20%，建议考虑扩展抓取来源（如增加长文关键词、设置翻页抓取）以获得更多适合多元体裁的素材」

#### Scenario: 覆盖率正常
- **WHEN** 当日候选文章中，非 news 体裁覆盖率 ≥ 20%
- **THEN** 不推送任何提示
