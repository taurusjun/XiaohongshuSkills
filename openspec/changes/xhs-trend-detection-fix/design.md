## Context

`scan_topic_trends()` 每日 07:00 执行，为每个话题：
1. 调用 `search_feeds(keyword, sort=?)` 抓取搜索结果
2. 计算 baseline_saves、is_fresh、image_ratio 等信号
3. 调用 `upsert_topic_performance()` 写入 DB

当前问题：单路扫描无法同时满足"质量基线"和"活跃度"两个不同目标，且写入路径污染了真实帖子的 avg_saves。

## Goals / Non-Goals

**Goals:**
- 双路扫描：综合排序取质量基线，最新排序取活跃度
- 完全隔离 trend scanner 写入与真实帖子表现数据
- is_fresh 正确解析 XHS 所有时间字符串格式
- is_fresh 在规划层产生更大效用

**Non-Goals:**
- 不修改 metrics_collector 的数据回收逻辑
- 不增加每个话题的扫描次数超过 2 次（2 路 × N 话题）

## Decisions

### D1: `_is_recent(text, hours=48) -> bool`

新增独立辅助函数，处理 XHS 所有时间格式：

```python
def _is_recent(text: str, hours: int = 48) -> bool:
    """判断 XHS pub_time 字符串是否在 hours 小时内"""
    if not text: return False
    t = str(text).strip()
    # 相对时间
    if t in ("刚刚",): return True
    m = re.match(r"(\d+)\s*分钟前", t)
    if m: return int(m.group(1)) <= hours * 60
    m = re.match(r"(\d+)\s*小时前", t)
    if m: return int(m.group(1)) <= hours
    if t == "昨天": return hours >= 24
    m = re.match(r"(\d+)\s*天前", t)
    if m: return int(m.group(1)) * 24 <= hours
    # ISO 日期
    try:
        from datetime import datetime, timedelta
        pub = datetime.strptime(t[:10], "%Y-%m-%d")
        return (datetime.now() - pub).total_seconds() / 3600 <= hours
    except Exception:
        return False
```

### D2: 双路扫描结构

`scan_topic_trends` 重构为：

```python
# Pass 1: 综合排序 → 质量基线
feeds_general = publisher.search_feeds(keyword, sort="general")
# 计算 baseline_saves（截尾均值）、image_ratio、avg_title_len、recommended_keywords

# Pass 2: 最新排序 → 活跃度
feeds_newest = publisher.search_feeds(keyword, sort="newest")
# 计算 is_fresh（用 _is_recent 判断）
```

两次调用总耗时约 10-15 秒/话题，5 个话题约 50-75 秒，在 07:00 的 Phase 1 时间预算内。

### D3: baseline_saves 截尾均值

去掉最高值后取均值（trimmed mean，对 N=20 样本足够）：

```python
saves_sorted = sorted(saves)
trim_saves = saves_sorted[:-1] if len(saves_sorted) > 3 else saves_sorted
baseline_saves = sum(trim_saves) / len(trim_saves)
```

### D4: upsert_topic_performance 调用分离

trend scanner 调用时：
```python
upsert_topic_performance(
    topic,
    saves=0,        # 不写 avg_saves
    comments=0,     # 不写 avg_comments
    topic_baseline_saves=baseline_saves,
    topic_baseline_comments=baseline_comments,
    trend_signal={...},
    ...
)
```

`upsert_topic_performance` 内部：当 `saves=0 and comments=0` 且存在 `topic_baseline_saves` 时，跳过 rolling average 更新，只写 baseline 和 trend_signal 字段。或者更简洁：新增 `trend_only=True` 参数控制此分支。

### D5: recommend_post_times 按 is_fresh 排序

```python
def recommend_post_times(topics: list[TopicQuota], date: str) -> list[str]:
    defaults = get_config("default_post_times", ...)
    # is_fresh=True 的话题排前面，分配最早时段
    sorted_topics = sorted(topics, key=lambda t: (0 if t.is_fresh else 1))
    return defaults[:len(sorted_topics)]
```

返回值改为 `list[str]`（已有），各 TopicQuota 通过 `DailyPlan.post_times[i]` 对应。

### D6: explore 槽优先 fresh 话题

```python
explore_candidates = sorted(
    [t for t in historic_topics if ... and t.get("discard_count", 0) < 3],
    key=lambda t: (0 if _topic_is_fresh(t) else 1, -t.get("engagement_score", 0))
)
```

## Risks / Trade-offs

| 风险 | 影响 | 缓解 |
|------|------|------|
| 双路扫描使 Phase 1 耗时翻倍 | 07:00 Phase 1 延长 ~1min | 5话题合计 <2min，可接受 |
| 最新排序内容质量差影响 is_fresh 判断 | 误判（低质量发帖频繁 → is_fresh=True） | 阈值 40% 而非 50%，留有余量 |
| trend_only=True 路径误用导致 avg_saves 从不更新 | 话题学习失效 | 单元测试验证两路写入互不干扰 |
