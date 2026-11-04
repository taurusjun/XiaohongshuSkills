# 双跑观察（生产 vs 容器）

> 观察期：2026-11-05 → 2026-11-11（7 天，第 7 天盲评后决定是否延长）

## 日报

- 文件：`~/PG/xhs-storage/logs/dual_run/YYYY-MM-DD.md`
- 页面：`http://127.0.0.1:5000/dual-run`（生产 UI）
  或 `http://127.0.0.1:15000/dual-run`（容器 UI）
- 定时：`com.xhs.dual-run-report`，每天 **05:30** 生成前一天
- 任务日志：`~/PG/xhs-storage/logs/dual_run_report.log`

手动生成：

```bash
cd ~/PG/xhs-docker-src
~/PG/XiaohongshuSkills/.venv/bin/python ops/dual_run_report.py --date 2026-11-04
~/PG/XiaohongshuSkills/.venv/bin/python ops/dual_run_report.py --days 7   # 趋势
```

## 指标

| 区块 | 看什么 |
|---|---|
| 覆盖 | 当日入库 / 成稿 / 交集 / 各自独有 |
| 标题 | 平均字数、>20 字、空标题、对比/争议命中率 |
| 正文 | 平均字数、密度、假名、顿号、story 缺 `##` |
| 评分 | 口径不同，只各自看趋势（容器 write_score 0-10） |
| 交集抽样 | 同 key 两边标题/正文字数 |
| 容器运行 | write_failures、token 用量 |
| 异常 | >20 字 / 空标题 / 缺 `##` / 顿号行 |

## Go / No-Go

1. 容器覆盖率 ≥ 生产
2. 容器平均分趋势不低于生产（口径不同，看相对变化）
3. 标题 >20 字 = 0、空标题 = 0
4. 无 DB 锁 / 写失败异常
5. 盲评：容器胜或平 ≥ 70%
6. **容器发布链路验收通过（硬门槛）**

## 注意

- 容器与生产写的是两份独立 DB，互不影响发布
- 生产发布仍是唯一发布链路；容器未验收前不发布
