# 密度计算基准修正（6/27）

## 问题

operations-guide.md 的密度检查示例代码用 `len(c_ja)` 当分母：

```python
ja_len = len(d.get('content_ja', '') or '')
density = body_xhs / max(ja_len, 1) * 100
```

`len()` 对日文原文不合适——日文 content_ja 含大量半角英数（URL、西历、スコア等），`len()` 把它们都按×1算，而 xhs_len 规定半角字符只算0.5。结果分母虚高，密度算出来偏低。

## 正确做法

**正文和基准都用 `xhs_len()` 计算。**

```python
# 从脚本导入
from xhs_word_count import xhs_len

# 或内联定义
import math
def xhs_len(t):
    wide = sum(1 for c in t
               if '\u4e00'<=c<='\u9fff'
               or '\u3000'<=c<='\u303f'
               or '\uff00'<=c<='\uffef')
    return math.ceil(wide + (len(t)-wide)*0.5)

d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:5000/api/news/{key}").read())
body = xhs_len(d.get('rewritten_content', '') or '')
base = xhs_len(d.get('content_ja', '') or '')
density = body / max(base, 1) * 100
```

## 常见的错误做法

| 做法 | 后果 | 实际数据 |
|------|------|---------|
| `len(content_ja)` 当分母 | 半角字母数字算×1，分母偏大，密度虚低 | 6/27案例：len=2735 vs xhs_len=1904 |
| `re.findall(r'[\u4e00-\u9fff]')` 筛纯汉字 | 漏日文假名，分母偏小，密度虚高 | 6/27案例：筛出885 vs xhs_len=1904 |
| 正文也按纯汉字算 | 两头都错，偏差抵消但结果无意义 | — |

## 验证

用 `scripts/xhs_word_count.py` 确认数值后再汇报密度。
