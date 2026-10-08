# 8/18 教训：layer23 把「定时发布队列」误报为「已发布」——主进程需 API 直查核实

## 现象

2026-08-18（当日66条，凌晨02:30东京时间跑）第2~3层子 agent 报告青叶坂46（#835b1ed42975）关联分析时声称：
「昨日已入库 d753bf32eca0（created 8/17，rew=青叶坂46是什么？神秘CM引爆三团粉丝：8/18晚9点揭晓，**且今日11:10已发布**）——纯重复，跳过」。

实际数据（主进程核实）：
- d753bf32eca0 创建于 8/17 00:33，`xhs_pub_time=2026-08-18 11:10`，publish_mode=normal，rewritten_title 已有——是**定时发布队列**（未来 pub_time），凌晨 02:30 运行时尚未发布，0 阅读属预期（#112/#114 口径）。
- layer4 子 agent 口径正确：把它列入「定时发布队列（4条）」而非已发布。

## 影响

结论未受影响（两条线都建议跳过该素材，因为主题已被覆盖），但**存档措辞**不同：「已发布」vs「已排定今日11:10定时发布」。定时队列条目实际发布前 0 阅读/0 互动，若下游按「已发布」理解，会把定时队列的 0 数据误读为发布后冷门。

## 核实方法（主进程汇总时）

1. 关键词 search 定位条目，看 xhs_pub_time 是否 > now：
```bash
curl -s --noproxy '*' -G "http://127.0.0.1:5000/api/news" \
  --data-urlencode "sort_by=created_at" --data-urlencode "sort_dir=DESC" \
  --data-urlencode "limit=50" --data-urlencode "search=青葉坂" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'命中: {d.get(\"total\",0)}条')
for r in d.get('rows',[]):
    print(f'{r[\"key\"][:12]} | created={r.get(\"created_at\",\"\")[:16]} | pub={r.get(\"xhs_pub_time\",\"\") or \"(未发)\"} | mode={r.get(\"publish_mode\",\"\")} | rew={(r.get(\"rewritten_title\",\"\") or \"\")[:45]}')
"
```

2. 全量定时队列核查（未来 pub_time 过滤，与 layer4 报告对账）：
```bash
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?publish_xhs=published&sort_by=xhs_pub_time&sort_dir=DESC&limit=50" | python3 -c "
import sys, json
from datetime import datetime
now = datetime.now()
for r in json.load(sys.stdin).get('rows',[]):
    pub = r.get('xhs_pub_time','') or ''
    if pub:
        try:
            if datetime.strptime(pub[:16], '%Y-%m-%d %H:%M') > now:
                print(f'{r[\"key\"][:12]} | pub={pub} | rew={(r.get(\"rewritten_title\",\"\") or \"\")[:50]}')
        except Exception: pass
"
```
（8/18 实测输出 4 条 = 小嶋阳菜09:12 / 青叶坂11:10 / 宫泽佐江15:12 / 八木爱月18:08，与 layer4 报告完全一致。）

## 纪律

- layer23/任何子 agent 声称「已发布」时，若素材是昨日/更早入库且当日凌晨跑 review，先怀疑是定时发布队列。
- 存档中统一措辞：「已排定今日 HH:MM 定时发布」（时间来自 xhs_pub_time），不写「已发布」。
- 跳过判定可不受影响，但措辞必须准确，避免污染第4层「已发布数据」口径。
