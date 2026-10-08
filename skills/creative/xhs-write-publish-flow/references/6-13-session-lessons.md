# 6/13 Session Key Lessons

## 1. Review 不走 delegate_task，走主进程

`xhs-content-review` skill 22k+字符，delegate_task 子 agent 加载后频繁超时中断（本session 5次失败，仅2次成功）。用户确认：**所有 review 在主进程完成**。

**操作变更：**
- 写稿→入库后，主进程通过 execute_code + terminal 查 DB 密度数据
- 用3维度标准在主进程评分，不放水
- 输出格式与之前一致（情绪价值/爆发点/标题吸引力 + 理由 + 综合判断）

主进程 review 已验证可行：平野紫耀→SAAYA 素材 8.0分（与本session较早的 delegate_task 版本评审一致）。

## 2. 所有稿件先入库再决定发布渠道

用户确认规则变更：不再区分小红书稿和公众号稿。所有稿件统一写入DB（rewritten_title+rewritten_content+preselected=1），发布时再决定走哪个渠道。

公众号稿的 workspace 纯文本副本保留为可选项，但主入库路径是 DB。

## 3. 同人物素材查找必须用 sqlite3

大野智案例：最初只用了两篇素材（纹身报道1305字 + 酒局报道1308字），写完后用户问有没有关联素材。用 sqlite3 查 `WHERE title LIKE '%大野智%'` 找到第3条旧素材 `b51be5dd...`（823字，含樱井翔原话：车库练舞、歌词卡揉皱）。这条素材提供了缺失的第一人称引语，使v3→v4的内容密度从29.4%提升到32.2%。

**操作变更：** 每次写稿前必须 sqlite3 搜同人物全部旧素材，不跳过。先用 curl API search 获取初步列表，再用 sqlite3 查完整key和content_ja长度。

## 4. 内容密度的正确计算方式

大野智v3 review 时误算了密度（以为原文3000字→883/3000=29.4%），后来用 sqlite3 逐篇查 content_ja 长度后发现实际是：
- 纹身报道：1305字
- 酒局报道：1308字
- 旧素材（樱井翔）：823字
- 原文总计：3436字 vs 正文1108字 → 32.2% ✅

原文密度需要用 sqlite3 精确查，不要靠估算。长文story最低30%。

## 5. 多层结构素材的价值

MEGUMI素材（两篇深度采访，大量第一人称引语）写起来最顺畅，review给分最高（约8.0）。大野智素材（第三方狗仔报道，无第一人称引语）天花板天然低（v4约7.5）。素材类型决定了天花板高度，写法只能微调。

## 6. `trigger_job` stale-job bug 修复

`cronjob(action='run')` 调用 `trigger_job()` 把 `next_run_at` 设为当前时间。但 scheduler 的 `get_due_jobs_locked()` 中的 stale-job 检测发现 `now - next_run_at > grace` 后，会把 job fast-forward 到下次而不是立即执行。

修复：`trigger_job()` 设置 `next_run_at = now + 5s`（比 now 稍晚），使 `now - next_run_at` 为负值，永远小于 grace 窗口。
