# Layer 4: xhs_pub_time 日期解析 & CTR 数据陷阱

## 1. xhs_pub_time 格式（7/28发现）

实际数据格式是 **`2026-07-28 17:50`**（16字符，不含秒）：

```
✅ 正确: datetime.strptime(pub[:16], '%Y-%m-%d %H:%M').replace(tzinfo=cst)
❌ 错误: datetime.strptime(pub[:19], '%Y-%m-%d %H:%M:%S')  # 会 ValueError
```

也有一种数据库存的格式是 `2026-07-28 17:50:00`（含秒）。如果 API 有时返回有时不返回秒，安全写法：

```python
# 安全写法：自动适应两种格式
dt_str = pub[:16]  # 只取到分钟
pub_dt = datetime.strptime(dt_str, '%Y-%m-%d %H:%M').replace(tzinfo=cst)
```

## 2. CTR 数据大概率全空（7/28重复观察）

`xhs_ctr` 字段在 API 返回中通常是**空字符串 `""`**，不是数字也不是 None：

```python
# ❌ 这样取不出 CTR
ctr = r.get('xhs_ctr', 0)  # 结果是 ""
ctr > 0                     # TypeError

# ✅ 安全写法
ctr_raw = r.get('xhs_ctr', '')
if ctr_raw and ctr_raw != '':
    ctr = float(ctr_raw)
else:
    ctr = None  # 标注为无数据
```

**规律（截至 7/28）：** 50+条 published 数据中，`xhs_ctr` 全部为空字符串。这说明 XHS 的 CTR 数据可能不开放给 API，或者只在特定条件下才显示。Layer 4 输出时如果 CTR 全部为空，应在存档中标注「CTR数据缺位」，不要试图解析。

## 3. 时区统一使用 CST

```python
from datetime import timezone, timedelta
cst = timezone(timedelta(hours=8))
```

**注意：** `xhs_pub_time` 本身不带时区信息，但所有发布时间都是 CST/Asia/Shanghai。解析后务必 `.replace(tzinfo=cst)` 再做时间差计算。

Layer 4 分层判断的参考时间也用 CST:

```python
from datetime import datetime
now = datetime.now(cst)  # 直接用 CST now
```

## 4. 解析失败时的 fallback

如果某个 `xhs_pub_time` 字段是意外格式（None、空字符串、纯日期无时间），跳过该条目而不是报错导致整个 Layer 4 中断：

```python
for r in rows:
    pub = r.get('xhs_pub_time', '')
    if not pub or len(pub) < 16:
        continue  # 跳过格式异常条目
    # ...继续解析
```
