# 小红书运营智能体 — 运行指南

## 前置条件

- Chrome 已启动 CDP 模式：`python scripts/chrome_launcher.py`
- Chrome 已登录小红书 + 小红书创作者中心
- `scripts/.env` 已配置 LiteLLM + 飞书凭证

## 手动执行流程

### 1. dry-run 预览（安全，不改数据）

```bash
.venv/bin/python scripts/agent_runner.py --dry-run
```

### 2. 趋势扫描

```bash
.venv/bin/python scripts/xhs_trend_scanner.py --keywords "乃木坂,AKB,日向坂,アイドル" --limit 10
```

### 3. 生成今日计划

```bash
.venv/bin/python scripts/agent_planner.py --date today --print
```

### 4. 抓取 → 翻译 → 生成 → 评分

```bash
# 并行版，无需交互，关键词+数量通过 JSON 传入
.venv/bin/python scripts/yahoo_news_auto_sqlite.py --keywords '[{"keyword":"乃木坂","max":3},{"keyword":"AKB","max":3},{"keyword":"日向坂","max":2}]'

# 不传 --keywords 则使用 DEFAULT_KEYWORDS
.venv/bin/python scripts/yahoo_news_auto_sqlite.py
```

### 5. Web UI 审核

```bash
.venv/bin/python web/app.py
# 打开 http://localhost:5000
# - 查看文章评分，低分可手动纠正或丢弃
# - 选择要发布的文章，勾选 publish_xhs
```

### 6. 飞书审批（可选）

```bash
.venv/bin/python -c "
from scripts.feishu_bot import send_card, build_daily_approval_card
from scripts.sqlite_db import query_news
articles = query_news(status='active', sort_by='content_score', limit=10)
candidates = [{'key': a['key'], 'title': a['title'], 'title_score': a['title_score'],
                'content_score': a['content_score'], 'topic': a.get('category','')} for a in articles]
card = build_daily_approval_card(candidates)
send_card('ou_606c35e10faba525d81d9ca03d386b79', card)
"
```

### 7. 发布到小红书

```bash
.venv/bin/python scripts/yahoo_news_publish.py
```

### 8. 回收实发数据（发布 4h 后）

```bash
.venv/bin/python scripts/metrics_collector.py --dry-run   # 先预览
.venv/bin/python scripts/metrics_collector.py              # 真正回收
```

### 9. 维度相关性分析（每周末）

```bash
.venv/bin/python scripts/dimension_analysis.py --min-sample 5
```

## 自动化（crontab）

```cron
# 每日 07:00 智能体主循环
0 7 * * * cd /path && .venv/bin/python scripts/agent_runner.py >> logs/agent.log 2>&1

# 每小时回收实发数据
0 * * * * cd /path && .venv/bin/python scripts/metrics_collector.py >> logs/metrics.log 2>&1
```

## 流程总览

```
07:00 agent_runner
  ├─ 阶段1 感知 → 趋势扫描（D 模块）
  ├─ 阶段2 规划 → 生成今日话题+配额（E 模块）
  ├─ 阶段3 执行 → 抓取→翻译→生成→评分→入库（自动）
  └─ 阶段4 通知 → 推送飞书审批卡片 → 运营者确认发布
       └─ 运营者确认 → 阶段5 发布 + 数据回收
```
