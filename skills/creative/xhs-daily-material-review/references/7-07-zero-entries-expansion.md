# Zero-Entry Date Expansion（7/7经验）

## 问题描述

2026-07-07（火曜日）的 cron review 中，API 返回 `date_from=2026-07-07&limit=200` → total=0。今日无新素材入库。

## 根因

素材抓取流程（feed fetcher）可能因为以下原因未在当日触发或未完成：
- 节假日/周末（7/7是火曜日，非节假日——可能是feed延迟）
- 上游源数据延迟
- 抓取cron未运行

## 处理方法

### Layer 1 的做法（已验证可行）

1. **先查当日** — 标准的 `date_from=today`
2. **发现0条后自动扩展为3天范围** — `date_from=today-3d&date_to=today&limit=200`
3. **153条命中**（7/4~7/6）— 足够做完整的聚类和分级
4. **在所有输出中明确标注** — 「⚠️ 今日无新素材，基于最近3天数据(7/4~7/6, 共153条)分析」
5. **继续执行完整四层流程**

### 完全无数据的判定

如果扩展到7天仍为0条，说明系统异常或全链路故障。此时：
- 输出「⚠️ 今日无新素材」
- **跳过后续层执行**（聚类/分级/价值建议/发布数据回顾无意义）
- 不写存档
- 不调用 segment-send

## 对下游影响

- **日期标注**：存档文件标题仍用 review 日期（YYYY-MM-DD），但在首行说明实际数据范围
- **第2~3层**：context 中注明了数据日期范围，layer23 子 agent 拉 API 时也用扩展后的 date range
- **第4层**：发布数据回顾不依赖当日素材日期，正常执行（published数据按published时间分层）
- **审计**：Checklist for 第1层需验证是否处理了「0条素材」场景

## Layer 1 skill 中的标准化做法

详见 `xhs-daily-material-review-layer1` skill 的 1a 小节，已加入标准化的 `ZERO_ENTRIES_FOR_TODAY` → 3天扩展 → 7天扩展的 fallback 流程。
