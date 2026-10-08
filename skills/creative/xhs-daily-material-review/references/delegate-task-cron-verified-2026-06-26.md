# delegate_task Cron 模式端到端验证（2026-06-26）

## 背景

6/26之前，delegate_task 分发的 cron review 被认为「不可靠」。6/25死锁因 layer skill 不存在，6/26上午测试（job 99e85）失败原因未完全查明（可能因主 cron 和测试 job 同时跑导致的竞争条件）。

## 本次验证（2026-06-26 端到端测试）

**执行时间**：~10:30 JST
**模式**：delegate_task 分发 layer1 + layer4（并行）→ layer23（串行）→ 主进程汇总→审计→segment-send
**模型**：deepseek-chat（所有子 agent 使用相同模型）

### 各层耗时

| 层 | 耗时 | 数据规模 |
|:--:|:----:|:---------|
| Layer 1 | 176s | 80条全量扫描 → 聚类8组 → 分级S/A/B/C |
| Layer 4 | 43s | 50条published → 38条>48h → 4个新模式 |
| Layer 23 | 94s | 11条S/A素材 → 价值建议 + 11人关联查询 |
| 汇总+审计+segment | ~10s | 存档7616字符 → 3段输出 |

**总计**：~320s（约5分钟）

### 验证结论

1. **delegate_task 在 layer skill 存在时工作正常。** 三个子 agent 全部成功返回，无 stale_stream_kill。
2. **Layer 1 + Layer 4 并行可行。** 两者读同一个 API 但读不同 endpoint（Layer 1 读 `?date_from=today`，Layer 4 读 `?publish_xhs=published`），不冲突。
3. **data-feedback-patterns.md 的模式更新委托给 Layer 4 子 agent 正确执行。** 4个新模式(64-68)全部 append + 趋势表更新。证明 Layer 4 子 agent 的 `do_not` 限制与主 skill 的 `4d` 要求之间不再矛盾（已修复）。
4. **重复执行安全。** 如 cron 因竞争条件被重复触发，第二次可能覆盖同日期存档文件，但不应产生有害副作用。

### 推荐做法

- **cron 每天 10:00 CST**：delegate_task 分发（已验证可行，干净分离各层关注点）
- **手动触发**：主进程直做（更快，不需子 agent 预热）
- **测试新功能**：用 one-shot cron job + 老日期存档做对比验证

### 风险点

- 子 agent 和主进程**不可同时读/写同一端点的相同数据集**（竞争条件风险）
- 子 agent 和主 cron 同时运行 → 两套输出都可能发到同一个对话
