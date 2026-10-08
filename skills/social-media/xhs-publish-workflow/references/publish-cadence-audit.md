# 发布节奏盘点脚本（「几天没发了」/「推荐几篇」）

一次性跑完：最新发布天数 → 本月每日发布量 → 断更空档 → 待发候选池（按分排序）。

## 1. 拉全量已发布 + 断更天数

```bash
cd /tmp
ALL_PROXY="" curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?publish_xhs=published&limit=500" -o /tmp/pub1.json
ALL_PROXY="" curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?publish_xhs=published&limit=500&offset=300" -o /tmp/pub2.json
python3 - << 'PYEOF'
import json, datetime
from collections import Counter
rows = json.load(open('/tmp/pub1.json'))['rows'] + json.load(open('/tmp/pub2.json'))['rows']
seen = {}
for r in rows:
    t = r.get('xhs_pub_time') or r.get('publish_time') or ''
    if t: seen[r['key']] = (t, r)          # 按 key 去重
items = sorted(seen.values(), key=lambda x: x[0], reverse=True)
print('published(去重):', len(items))
print('--- 最近 18 条 ---')
for t, r in items[:18]:
    print(t, r['key'][:12], '|', (r.get('rewritten_title') or r.get('title') or '')[:40])
c = Counter(t[:10] for t, r in items if t >= '2026-09-01')
print('--- 本月每日发布量 ---')
for d in sorted(c): print(d, c[d])
last = items[0][0]
now = datetime.datetime.now()
lt = datetime.datetime.strptime(last[:16], '%Y-%m-%d %H:%M')
d = now - lt
print('最新发布:', last, '| 现在:', now.strftime('%Y-%m-%d %H:%M'))
print('距今: %.1f 小时 = %.1f 天' % (d.total_seconds()/3600, d.total_seconds()/86400))
PYEOF
```

**要点**
- 阅读字段是 **`xhs_views`**；`xhs_read` / `xhs_view` 不存在（探字段名会全部 None）。
- limit 上限 500 → 761 条必须 `offset=300` 再拉一次，按 key 去重（两次会有重叠）。
- **定时队列 ≠ 已发布**：`xhs_pub_time` 有值但 `publish_xhs` 未归位的条要排除（否则「最新发布」会算错一整天）。
- 定时队列单独查：`publish_xhs=1 AND (publish_time IS NULL OR publish_time='')`，或看 `xhs_pub_time` 是否在未来。

## 2. 待发候选池（按分排序）

```bash
ALL_PROXY="" curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=<今天-2>&limit=200" -o /tmp/pend.json
python3 - << 'PYEOF'
import json, re
rows = json.load(open('/tmp/pend.json'))['rows']
def sc(r):
    m = re.search(r'\|(\d+\.\d)分', r.get('score_dims') or '')
    return float(m.group(1)) if m else None
cand = [r for r in rows
        if r.get('preselected') == 1
        and (r.get('rewritten_content') or '')
        and r.get('publish_xhs') in (0, '0', None)]
print('待发候选:', len(cand))
for s, d, k, t, sd in sorted(
        [(sc(r), r['created_at'][:10], r['key'][:12], (r.get('rewritten_title') or '')[:46], (r.get('score_dims') or '')[:24]) for r in cand],
        key=lambda y: (-(y[0] or 0), y[1])):
    print(s, d, k, '|', t, '|', sd)
PYEOF
```

- 分数为 `None` 的行 = `news-pass` 或 `akb-top-bullet`，单独列。
- **不要把 `preselected=1 AND rewritten_content非空` 当作唯一判据去反推「漏写」**——gzh 稿（preselected=0 + wechat_content）与 bullet（preselected=1 + 正文空 + score_dims='akb-top-bullet'）都会被误判。

## 3. 人物历史数据（推荐的依据）

```python
import json, subprocess, urllib.parse
def q(kw, limit=200):
    u = 'http://127.0.0.1:5000/api/news?' + urllib.parse.urlencode(
        {'search': kw, 'publish_xhs': 'published', 'limit': limit})
    return json.loads(subprocess.run(['curl','-s','--noproxy','*',u], capture_output=True, text=True).stdout).get('rows', [])
for kw in ['板野友美','柏木由纪','秋元康','乃木坂','樱坂','日向坂','金村美玖','STU48','若月佑美','反町隆史']:
    rows = [r for r in q(kw) if isinstance(r.get('xhs_views'), (int, float))]
    if not rows:
        print(f'{kw}: 已发 0（新选题，无先例）'); continue
    rows.sort(key=lambda r: -r['xhs_views'])
    med = sorted(r['xhs_views'] for r in rows)[len(rows)//2]
    recent = [(r.get('xhs_pub_time') or '')[:10] + ':' + str(int(r['xhs_views']))
              for r in sorted(rows, key=lambda x: (x.get('xhs_pub_time') or ''), reverse=True)[:3]]
    print(f"{kw}: n={len(rows)} 中位={med} 最高={rows[0]['xhs_views']} 近3={recent}")
```

**注意人名写法要试两种**：`柏木由紀`（日文新字体）命中 2 条，`柏木由纪`（简体）命中 32 条 —— 用简体搜，否则会把 32 条的先例误判成 2 条。

## 4. 输出格式（用户要的形态）

1. **先给数字**：距上次发布 X 小时 / Y 天；今天几条；定时队列几条；本月每日发布量表（把 0 条的日子标粗）。
2. **再给推荐**（表格：稿 / 关键数据 / 推荐理由），每条理由必须挂上「已发条数·中位·最高」或时效事实。
3. **最后给排期动作**：哪条今天必须发（时效）、哪几条隔天发、哪些不要同日（同人物自我分流）。
4. 附 1~2 条备选补位。
