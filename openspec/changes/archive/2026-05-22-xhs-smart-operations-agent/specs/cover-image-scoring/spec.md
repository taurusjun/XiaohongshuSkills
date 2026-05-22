## ADDED Requirements

### Requirement: 使用 DeepSeek 视觉能力对封面图进行客观维度评分
系统 SHALL 在 gallery 图片下载完成后，调用 LiteLLM（DeepSeek 视觉模型）对候选封面图进行多维度客观评分。

> **能力边界说明（基于 DeepSeek 官方确认）：**
> - ✅ 可评估：图片内容描述、人脸清晰度、构图、色彩对比、情绪表情可见性
> - ❌ 不可评估：审美高级感（主观）、流量吸引力（无真实数据）、时事梗图的社会语境
>
> 本评分系统只定义**客观可描述**的技术性维度，不预测点击率。点击率的校准依赖实发数据回收后的相关性分析。

评分维度（0 / 0.5 / 1）：

| 维度 | 类型 | 描述 |
|---|---|---|
| `face_clarity` | 加分 | 人脸清晰可辨，主体居中或位于视觉焦点 |
| `emotion_visible` | 加分 | 可观察到明显情绪（微笑/惊讶/专注等），而非面无表情 |
| `composition_clean` | 加分 | 主体突出，背景简洁不杂乱 |
| `color_contrast` | 加分 | 色彩对比适度，无明显过曝/欠曝 |
| `promo_feel` | 减分 | 含明显广告感（品牌 logo、宣传文字叠加、水印） |
| `multi_person_blur` | 减分 | 多人同框且多人模糊，无法识别主体 |

`cover_score = sum(加分维度 × weight) - sum(减分维度 × weight)`，clamp 到 [0, 5]。

评分结果存储在 `cover_image_scores` 表，通过 `news_key + image_url` 关联。

#### Scenario: gallery 下载完成后自动触发封面图评分
- **WHEN** `gallery_download.py` 完成一篇文章的图片下载
- **THEN** 自动对前 3 张图调用 DeepSeek 视觉评分，将结果写入 `cover_image_scores` 表

#### Scenario: DeepSeek 评分直接使用本地文件，先 resize 控制 token 成本
- **WHEN** 评分调用时
- **THEN** 读本地 cache 文件，用 Pillow resize 到最长边 **512px**（保持比例），再 base64 编码传给 DeepSeek。单张图片 token 消耗 < 500 tokens（原始高分辨率写真图不压缩约 1500 tokens/张）。

> **说明：** 评分在 gallery_download.py 完成后立即触发，图片已在本地 cache，无需构造 URL。3 张/篇 × 10 篇/天 × 500 tokens ≈ 15,000 tokens/天用于图片评分，可控。

#### Scenario: 减分维度在 prompt 中显式标注方向
- **WHEN** 构造 DeepSeek 评分 prompt 时
- **THEN** prompt 中明确区分加分维度和减分维度的方向：「以下为**负面特征维度**（存在时扣分，值越高问题越严重）：`promo_feel`（广告/水印/宣传感）、`multi_person_blur`（多人同框但主体模糊）」；加分维度另外列出，防止 LLM 统一理解为「1=好」

#### Scenario: 视觉 API 调用失败时优雅降级
- **WHEN** DeepSeek 视觉调用失败（超时/格式不支持/base64 过大）
- **THEN** 记录错误日志，`cover_score` 置为 null，不阻断后续流程；运营者在 gallery_preview 中仍可手动选图

#### Scenario: 评分结果展示在 gallery_preview 选图界面
- **WHEN** 运营者打开 gallery_preview 选图
- **THEN** 每张图片下方展示 `cover_score`（如 ⭐ 3.5/5），分数仅作参考，运营者有最终选图权

#### Scenario: 飞书审批卡片展示封面图评分
- **WHEN** 飞书推送每日候选审批卡片
- **THEN** 每篇文章展示最高 `cover_score` 的封面缩略图及评分

### Requirement: 封面图评分纳入综合发布候选排序，初始权重保守
系统 SHALL 在候选文章排序时，将 `cover_score` 纳入综合评分。

`candidate_score = title_score × w1 + content_score × w2 + cover_score × w3`

**`cover_score` 参与排序时使用该文章所有候选封面图的最高 `cover_score`**（而非平均分）。运营者在 gallery_preview 选定封面图后，`cover_image_scores` 中该图标记 `is_selected=1`，供后续相关性分析（DeepSeek 评分 vs 实际 CTR）使用。

> **初始权重设计原则：** `cover_score` 分布区间（实际通常 [0,3]）与文本分数分布（通常 [1.5,4]）量纲不同。在积累足够数据（≥ 30 篇）前，`w3` 应保守设置。
>
> 初始默认权重：`w1=0.45, w2=0.40, w3=0.15`（存储在 `agent_strategy.json` 的 `candidate_weights`）。

> **P0 阶段分步交付：** P0 只实现「评分 + 在 gallery_preview 展示分数」，`cover_score` 暂不接入 `candidate_score` 排序公式（`w3=0` 等效）；P1 阶段确认评分逻辑稳定后，再将 `w3` 设为 0.15 接入排序。

#### Scenario: 有封面评分时使用综合排序（P1 起启用）
- **WHEN** 候选文章有 `cover_score` 记录，且 `w3 > 0`（P1 启用后）
- **THEN** planner 用该文章最高 `cover_score` 计算 `candidate_score` 并排序

#### Scenario: 无封面评分时降级为文本评分排序
- **WHEN** 文章 `cover_score` 为 null（评分失败或 P0 阶段 w3=0）
- **THEN** 只用 `title_score × 0.55 + content_score × 0.45` 排序，不阻断流程

#### Scenario: reflection_runner 建议是否上调 w3
- **WHEN** `reflection_runner` 积累了 ≥ 30 篇 `cover_score` + `xhs_views` 数据，且 Pearson r(cover_score, xhs_views) > 0.3
- **THEN** 在飞书周报中建议「封面评分与实际流量正相关（r=X），可考虑将 w3 从 0.15 上调至 0.25」

### Requirement: 人工纠正封面图评分，纠正值与 DeepSeek 原始判断独立存储，并推动定义迭代
系统 SHALL 允许运营者在 gallery_preview 中对封面图维度评分进行纠正（0 / 0.5 / 1），并填写纠正理由。纠正记录有两个独立价值：(1) 即时修正该图的 `cover_score`；(2) 作为封面图维度定义迭代的素材，推动 DeepSeek 评分标准改进。

> **与文本维度的对称设计：** 文本维度的人工纠正通过 `override_note` 聚合后推动 `scoring_dimensions.json` 的 `edge_case` 更新。封面图维度采用相同机制——覆盖图评分维度也纳入 `scoring_dimensions.json`（`category: "图片"`），纠正理由聚合后推动封面图维度的 `edge_case` 更新，DeepSeek 下次评分时使用更准确的定义。

`cover_image_scores` 表新增字段：
- `human_override INTEGER DEFAULT 0`：是否被人工纠正
- `human_value REAL`：人工纠正值（0 / 0.5 / 1），NULL 表示未纠正
- `override_note TEXT`：运营者填写的纠正理由（如「侧脸，主体不清晰，应为 0.5」）
- `deepseek_value REAL`：DeepSeek 原始值的备份（纠正时将原 value 移入此字段）

封面图 6 个评分维度（`face_clarity` / `emotion_visible` / `composition_clean` / `color_contrast` / `promo_feel` / `multi_person_blur`）以 `category: "图片"` 纳入 `scoring_dimensions.json`，每个维度同样具有 `definition / example_1 / example_0 / edge_case` 字段，由同一套版本管理机制维护。

#### Scenario: 运营者纠正某维度评分并填写理由
- **WHEN** 运营者在 gallery_preview 中将某图的 `face_clarity` 从 1 改为 0.5，填写理由「侧脸，主体不清晰」
- **THEN** `cover_image_scores` 写入：`human_override=1, human_value=0.5, override_note="侧脸，主体不清晰", deepseek_value=1`；`cover_score` 用 `human_value` 替换该维度贡献后重算

#### Scenario: 纠正记录聚合推动封面图维度定义迭代
- **WHEN** `reflection_runner` 运行，且某封面图维度在过去 4 周内 `human_override=1` 的记录 ≥ 3 条
- **THEN** 调用 LiteLLM 分析这批 `override_note`，提炼共同模式，生成 `edge_case` 更新建议，在飞书周报中展示，例如：「`face_clarity` 被纠正 4 次，建议 edge_case 追加：侧脸或背对镜头时，即使面部可见也给 0.5 而非 1」

#### Scenario: 运营者确认后封面图维度定义发布新版本
- **WHEN** 运营者在飞书点击「采纳定义建议」
- **THEN** `scoring_dimensions.json` 中对应封面图维度的 `edge_case` 更新，`version` 递增，下次 DeepSeek 评分时 prompt 中该维度使用新定义

#### Scenario: 积累纠正数据后校准维度对 CTR 的影响
- **WHEN** `reflection_runner` 运行，且有 ≥ 20 张图片的人工纠正记录（含 `xhs_views` 数据）
- **THEN** 计算各维度「使用 human_value 后的 cover_score vs 实际 xhs_views」的相关性，生成「哪些封面维度真正影响流量」的分析报告，写入飞书周报（与维度定义建议一起展示）
