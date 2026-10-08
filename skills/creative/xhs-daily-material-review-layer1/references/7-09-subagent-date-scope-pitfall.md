# 7/9 Layer 1 Subagent Date Scope Pitfall

## 症状

- Layer 1 subagent 命令中使用了 `curl .../api/news` 但**未带 `date_from` 参数**
- 结果：返回全库 3,192 条（pending 2,617 + published 575）
- 分级出5条S级：丸刈り社会派、長尾謙杜スキャンダル、能條結婚、佐久間声優、井上和TOY STORY5
- Layer 23 并行运行时查当日数据（`date_from=2026-07-09`），只找到 61 条
- Layer 23 的 S/A 素材完全不同于 Layer 1（篠田麻里子、田野忧+乙武、七海奈奈、前田敦子、柏木由紀）
- Layer 1 的「能条爱未結婚」等素材确实在 DB 中有，但属于**前几天已入库的旧素材**，不在当日 61 条新素材中

## 根因

API `GET /api/news` 无任何参数时返回**全部待处理数据**（pending rows），不是当日数据。`date_from` 参数是必需的——没有它等于查全库。

这句话在 Layer 1 skill 的 1a 示例命令中说得很清楚（`date_from=$(TZ=Asia/Tokyo date '+%Y-%m-%d')`），但 subagent 在执行时跳过了示例命令，自己写了一个 `curl .../api/news?limit=200` 去查「标题分数最高的前200条」，没加 `date_from`。

## 教训

1. subagent 容易「看示例」但不「照做示例」——skill 中的示例命令写得再清楚，subagent 也可能自己写一个「看起来差不多」的版本但漏了关键参数
2. 铁则级别的警告比普通标注更有效——7/9 补丁将这条规则从「示例中的好习惯」升级为「⚠️ 铁则」
3. 如果 Layer 1 和 Layer 23 并行跑，且 Layer 1 数据有问题，主进程的最后一个安全网是：**汇总时验证 Layer 1 的分级结果是否出现在当日数据中**。如果 Layer 1 的 S 级素材一条都不在当天 61 条里，说明 Layer 1 的数据范围有问题。

## 主进程的验证启发（7/9）

本 session 中主进程发现 Layer 1 报告说「总计 3,192 条」但当日实际只有 61 条，立即意识到 Layer 1 的数据范围有问题。主进程的做法：
1. 再次单独查询当日 API 确认实际数字（61条）
2. 读取 Layer 23 的报告——确认 Layer 23 使用的是当日数据
3. 以 Layer 23 的 S/A 分级为主体，复用 Layer 1 的聚类信息和 AKB大TOP/B级判断
4. 在汇总存档第一行标注「データ範囲：2026-07-09 のみ」
5. 在报告中注明 Layer 1 与 Layer 23 的差异原因
