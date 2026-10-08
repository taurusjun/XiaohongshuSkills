# 高日量素材Review处理模式

当单日素材>50条时（常见于周末后累积或多个抓取源同时输出），
标准 1a→1b→1c→1d 流程需要加速处理。

## 核心问题

70+条素材逐条看content_ja不现实。需要批量过滤+分层深入。

## 加速流程

### Step 1: 做两遍 API 调用获取轮廓

```
# 第1次：按 title_score 降序拉全量（获取高评分素材轮廓）
bash query.sh --date_from YYYY-MM-DD --limit 200 --sort_by title_score --sort_dir DESC

# 第2次：用内联 Python 统计 fetch_by 和 category 分布
bash query.sh --date_from YYYY-MM-DD --limit 200 | python3 -c "
from collections import Counter
...
"
```

### Step 2: 按 fetch_by 分组，快速标记可跳过组

**可跳过信号（来自7/7经验）：**
- `fetch_by=グラビア` = 写真模特，非目标赛道 → 直接跳
- `fetch_by=セクシー女優` = 成人向 → 直接跳
- `category=经济` 且非偶像/娱乐人物 → 直接跳

**需深读信号：**
- `fetch_by` in [AKB, 乃木坂, 櫻坂, 日向坂, アイドル]
- Snow Man / なにわ男子（公众号方向）
- 任何含偶像/艺人名的 story 长文

### Step 3: 聚类后只读关键素材 content_ja

对S级候选和聚类主条目通过 API /news/<key> 获取 content_ja 全文。
同一事件的多条素材只需读1-2条最丰富的。

### Step 4: 直接在存档中完成分级

高日量日不需要逐条输出标题——用表格按来源分组列出即可。
存档内的聚类分析写在全量列表之后。
