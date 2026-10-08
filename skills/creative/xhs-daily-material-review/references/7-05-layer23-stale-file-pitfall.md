# 7/5层间数据传递事故：layer23 子 agent 使用旧日期文件

## 故障现象

2026-07-05 的每日素材review中，通过 delegate_task 分发第2~3层给子 agent。子 agent 加载 `xhs-daily-material-review-layer23` skill 后，没有使用 context 中传递的 7/5 今日 S/A 素材列表，而是：

1. 在 workspace 中搜索 `search_files(target='files', pattern='material_review_layer1_*')`
2. 找到 `material_review_layer1_2026-06-26.md`（6/26的旧存档）
3. 基于 6/26 的素材（斋藤飞鸟/柏木由纪/永尾玛莉亚）做分析
4. 输出了一份完整的第2~3层报告，**但所有素材都是6/26的，完全没有触及7/5的今日素材**

## 根因

- `search_files` 是子 agent 可用的工具（toolsets=["terminal", "file", "web"] 中包含 file）
- 子 agent 自然倾向于使用本地文件作为结构化输入（比解析 context 字段中的非结构化文本更「舒服」）
- layer23 skill 的「输入」部分没有明确禁止搜索旧文件

## 后果

主进程拿到 layer23 的子 agent 报告后，发现所有素材标题都与今日不同。主进程不得不自行补做第2~3层分析：
- 调用 API 获取今日 S/A 素材的 content_ja
- 每件进行 DB search 查旧记录
- 完整输出价值建议和跨时间关联

## 教训

1. **layer23 子 agent 必须被明确告知「不要搜索本地旧文件」**
2. **主进程必须验证子 agent 输出的日期/素材与今日一致**，不能盲目信任
3. **context 中的非结构化文本**虽然不那么好解析，但它是唯一可靠的今日数据源
4. 如果子 agent 输出明显错日期，主进程应自行补做分析（已有全量素材数据），而不是重跑子 agent
