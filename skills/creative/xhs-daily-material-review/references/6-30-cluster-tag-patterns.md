# 6/30 Cluster Detection Technique — Tags + Title Based Clustering

## 问题

当单日素材量达72条时，手动逐条聚类效率低。需一种可复用的聚类方法，能快速识别跨来源的同事件集群。

## 本日实践

使用 Python 脚本，基于 `tags` 字段 + `title` 关键字做自动聚类判断：

```python
clusters = {}
for r in rows:
    t = r.get('title', '') or ''
    tags_str = ','.join(r.get('tags', [])[:5])
    
    cluster = '其他'
    
    if any(kw in t or kw in tags_str for kw in ['龟梨', '田中皆实', 'KAT-TUN']):
        cluster = '龟梨和也结婚'
    elif any(kw in tags_str for kw in ['金川纱耶', '乃木坂']) and '金川' in t:
        cluster = '金川纱耶'
    elif '佐久间' in t or '佐久間' in t:
        cluster = '佐久间声优'
    elif '坂道' in t or '对决' in t or '舞蹈对' in t or '6事务所' in t:
        cluster = '坂道vsLDH舞蹈对决'
    # ... more rules
```

## 聚类判断优先级设计

1. **人物名（tags 字段）** — tags 字段比 title 更准确（title 可能省略人物名）。用 `any(kw in tags_str ...)` 检测。
2. **事件关键词（title 匹配）** — 同人物多事件时，通过标题关键词分流（如龟梨结婚 vs 龟梨酿酒）。
3. **跨来源合并** — 同一事件可能来自エンタメ総合/モデルプレス/音楽ナタリー等不同来源，title 各不相同。聚类判断不要按来源分组。

## 关键模式

- 人物名 + 事件关键词 → 同集群
- 人物名（多来源）→ 同集群（跨来源合并）
- 标题含相同特殊词汇（声优/剃头/对决）→ 同集群
- 先 tags 分流 → 再 title 分流：同人物多事件时用 title 关键词做二级分流

## 本日验证

72条素材成功聚类为：
- 龟梨结婚 7条（保留3条，合并4条）
- 金川纱耶 6条（保留3条，合并3条）
- 佐久间声优 6条（保留2条，合并4条）
- 坂道vsLDH 3条（保留1条，合并2条）
- AKB个人 4条（各自独立，不合并）
- 其他 39条（多数C级跳过）
