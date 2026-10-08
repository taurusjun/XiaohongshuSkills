# xhs-gzh双通道写稿（7/7验证）

## 适用场景

同一素材需要同时出xhs版和gzh版。xhs版走`rewritten_`字段（API PUT），gzh版走`wechat_`字段（sqlite3 UPDATE）。

## 关键约束

- **API PUT是全量覆盖**：如果先写了xhs的rewritten字段，再用API PUT写wechat字段，第二个PUT会清空rewritten字段
- 正确做法：xhs版走API PUT → gzh版走sqlite3 UPDATE（只更新wechat_字段，不影响rewritten字段）

## 操作流程

### 第一步：写xhs版
按正常流程：写draft → renwei → 算密度 → API PUT入库 → review

```bash
ALL_PROXY="" python3 ~/.hermes/skills/creative/xhs-write-publish-flow/scripts/write-api.py \
 <完整40位key> \
 "xhs标题" \
 /tmp/xhs_draft_xxx.md
```

### 第二步：写gzh版
写gzh长文draft（不限字数，走公众号审读流程）。

### 第三步：写入wechat_字段（sqlite3 UPDATE）

```python
import sqlite3

db = '/Users/user/PG/XiaohongshuSkills/data/news_dev.db'
with open('/tmp/gzh_draft_xxx.md') as f:
    content = f.read()

lines = content.strip().split('\n')
title_line = lines[0].replace('## ', '').strip()

key = '<完整40位key>'

conn = sqlite3.connect(db)
c = conn.cursor()
c.execute('UPDATE news SET wechat_title=?, wechat_content=?, channel=? WHERE key=?', 
          (title_line, content, 'gzh', key))
conn.commit()
conn.close()
```

### 第四步：验证

```bash
curl -s --noproxy '*' http://127.0.0.1:5000/api/news/<KEY> | python3 -c "
import json,sys
d = json.load(sys.stdin)
print('wechat_title:', d.get('wechat_title'))
print('wechat_len:', len(d.get('wechat_content','')) if d.get('wechat_content') else 'EMPTY')
print('channel:', d.get('channel'))
print('rewritten_title:', d.get('rewritten_title')[:50])
print('rewritten_len:', len(d.get('rewritten_content','')) if d.get('rewritten_content') else 'EMPTY')
"
```

## 验证结果

两版在API中同时可见：
- `rewritten_title`/`rewritten_content` = xhs版
- `wechat_title`/`wechat_content` = gzh版
- `channel='gzh'`（注意：channel被gzh覆盖了，xhs发时先改回'xhs'）

## 7/7验证的稿件

- 小栗有以恋爱观：key=f90de2... → xhs 938字 ✅ + gzh 1967字 ✅
- 井上和配音玩具5：key=e5b6ca... → xhs 863字 ✅ + gzh 2720字 ✅
