# Cron 测试纪律（6/26教训）

## 根因：test prompt写了"不调用segment-send.sh"

### 发生了什么

6/26 测试调度器模式的review时，测试job的prompt里写了**"本次测试不调用 segment-send.sh"**，导致：

1. 主进程汇总完review后写入了存档文件
2. 但没有分段发送到Telegram
3. 用户没收到结果，以为测试没跑完
4. 实际上 `last_status: ok`，存档文件也写好了（`~/.hermes/daily-reviews/2026-06-26.md`）

### 为什么我写了这句话

错误思维：**"测试job不用发到Telegram，我看一下结果就行"**

这是完全错误的。测试就是要验证完整的端到端流程，包括输出到用户。只验证前半段（delegate_task跑完）不验证后半段（segment-send发出），等于没测。

### 铁律

**测试 job 的 prompt 必须模拟真实 cron 的完整流程，不能跳过任何步骤。** 特别是 segment-send 是用户看到结果的唯一通道，跳过它等于测试结论无效。

## 测试前必须清理旧状态

### 6/26教训

跑测试 cron 之前，没有清理 `~/hermes/daily-reviews/2026-06-26.md`——因为之前的主cron手动run已经写入了这个存档。导致测试job写入时覆盖了同文件，但segment-send发出的内容和新存档可能不一致。

### 铁律

**创建测试 job 之前，先检查并清理可能冲突的旧状态：**
- 删除同日期存档：`rm ~/.hermes/daily-reviews/YYYY-MM-DD.md`
- 确认没有同名的旧job还在运行：`cronjob list`
- 如果测试job deliver=origin，确认用户不会在同一时间收到多个输出

## cron 测试完整检查清单

### 创建阶段
- [ ] 用 ISO 时间戳（如 `2026-06-26T10:22:00`），不支持 `now+1m` 语法
- [ ] `next_run_at` 确认时区正确（CST+08:00）
- [ ] prompt 包含完整的 end-to-end 流程（不跳过 segment-send）
- [ ] 清理旧状态（存档、同类型job）
- [ ] deliver 设为 origin（让用户收到输出，验证端到端）

### 运行阶段
- [ ] agent.log 有 `Running job 'XXX'`
- [ ] 3 分钟内无 `stale_stream_kill` 警告（阈值180s）
- [ ] 用户收到了分段输出（segment-send.py 使用 sendRichMessage API 发送，详见 references/telegram-rich-message-api.md）

### 收尾
- [ ] `last_status: ok`
- [ ] 测试 job 已删除（`cronjob remove`）
- [ ] 学习写回 skill 或 reference
