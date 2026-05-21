## ADDED Requirements

### Requirement: 「收藏驱动」维度在注册表中完整定义
系统 SHALL 在 `config/scoring_dimensions.json` 中注册「收藏驱动」维度，包含完整的结构化定义，供 LLM prompt 构造使用。

「收藏驱动」的注册表条目：
```json
{
  "name": "收藏驱动",
  "category": "内容",
  "direction": "plus",
  "default_weight": 1.0,
  "definition": "内容含有让用户想「存起来以后用」的元素：清单/排行/攻略/对比/知识点/方法论。核心判断：读完后用户会觉得「这个以后可能用得到」，而非仅仅「这条消息我知道了」",
  "example_1": "盘点10部值得反复看的日剧，每部附推荐理由和适合人群；或：某艺人的护肤步骤分享，含具体产品和使用顺序",
  "example_0": "某剧今日开播，官方公布主演阵容；或：某艺人出席品牌活动，现场气氛热烈",
  "edge_case": "含数字列表但内容是纯事实罗列（如「5位出席嘉宾名单」）给0；含推荐/建议/方法的给1，即使列表很短"
}
```

#### Scenario: LLM 基于完整定义准确判断
- **WHEN** 文章正文含「推荐理由/步骤/技巧/方法/清单」等元素
- **THEN** LLM 依据 `definition` 中的判断标准返回 `收藏驱动: {"value": 1, "reason": "..."}`

#### Scenario: 纯资讯通知不被误判为收藏驱动
- **WHEN** 文章仅描述事件发生（某明星出席/某剧开播/某人获奖），无可复用内容
- **THEN** LLM 依据 `example_0` 参照，返回 `{"value": 0}`，不因文章「有价值」而误给 1

#### Scenario: 边界情况——短列表
- **WHEN** 文章含「这3点值得关注」但内容只是事实陈述
- **THEN** LLM 依据 `edge_case` 判断：无推荐/建议/方法属性 → 给 0

### Requirement: 收藏驱动以等权 +1 计入内容评分（本 change 范围）
系统 SHALL 将「收藏驱动」维度以等权 +1 计入 `content_score`，与现有维度逻辑完全一致。

> **权重配置说明：** `agent_strategy.json` 及其权重覆盖机制由 `xhs-smart-operations-agent` 统一创建，本 change 不依赖该文件。本 change 实施完成时，「收藏驱动」以 `default_weight=1.0` 参与等权计算。加权求和由后续 change 统一实现。

#### Scenario: 收藏驱动为 1 时内容分提升
- **WHEN** `收藏驱动 = 1`
- **THEN** `content_score` 以等权 +1 计算，上限 5

#### Scenario: 旧文章缺少该维度
- **WHEN** `score_dims` 中无「收藏驱动」记录的旧文章
- **THEN** 系统不报错，该维度视为 0，通过 regenerate 补充

### Requirement: 维度元数据同步注册到 _DIM_DEFS
系统 SHALL 在 `sqlite_db.py` 的 `_DIM_DEFS` 中注册「收藏驱动」，类型为「内容」，计分方式为「加分」，保持与注册表一致。

#### Scenario: Web UI 详情页展示新维度及其 LLM 理由
- **WHEN** 用户打开文章详情页的评分面板
- **THEN** 「收藏驱动」显示在「内容」标签页，含 0/1 值、LLM 理由，支持人工纠正（来自 dimension-registry capability）
