# Batch Operation Commands (7/26经验)

适用于≥5篇批量的高效命令模板。

## 批量renwei审读

```bash
for f in draft1 draft2 draft3; do
  echo "=== $f ==="
  ALL_PROXY="" python3 ~/.hermes/skills/writing/renwei-writing/scripts/renwei-pre-commit.py /tmp/${f}.md 2>&1 | tail -3
  echo ""
done
```

exit 1（拒绝入库）的单独查详情：

```bash
ALL_PROXY="" python3 ~/.hermes/skills/writing/renwei-writing/scripts/renwei-pre-commit.py /tmp/gzh_s2_odagiri.md 2>&1 | grep -E '(マーク|命中|信号)' | head -20
```

## 批量日文假名检查

```bash
for f in draft1 draft2 draft3; do
  jap=$(python3 -c "c=open('/tmp/${f}.md').read(); print(sum(1 for ch in c if '\u3040'<=ch<='\u309f' or '\u30a0'<=ch<='\u30ff'))")
  echo "${f}: jap=${jap}"
done
```

## 批量入库(xhs版) — Python heredoc模式

```python
python3 << 'PYEOF'
import urllib.request, json, os

entries = [
    ("<full40bitsha1>", "/tmp/draft.md", "素材名"),
]

tmp_dir = '/tmp'
for key, fname, name in entries:
    path = os.path.join(tmp_dir, fname)
    with open(path) as f:
        lines = f.read().strip().split('\n')
    draft_title = lines[0].lstrip('#').strip()
    draft_content = '\n'.join(lines[1:]).strip()

    payload = json.dumps({
        'rewritten_title': draft_title,
        'rewritten_content': draft_content,
        'publish_mode': 'rewritten',
        'preselected': 1,
        'publish_xhs': 0,
    }).encode('utf-8')

    req = urllib.request.Request(
        f'http://127.0.0.1:5000/api/news/{key}',
        data=payload,
        headers={'Content-Type': 'application/json'},
        method='PUT'
    )
    urllib.request.urlopen(req, timeout=10)

    # 验证
    with urllib.request.urlopen(f'http://127.0.0.1:5000/api/news/{key}', timeout=10) as vr:
        vd = json.loads(vr.read())
    rc = (vd.get('rewritten_content', '') or '')
    print(f"{'OK' if len(rc) > 50 else 'SHORT'}: {name} ({len(rc)})")
PYEOF
```

## 批量入库(gzh版)

wechat_字段写法和xhs版本不同：

```python
payload = json.dumps({
    'wechat_title': draft_title,
    'wechat_content': draft_content,
    'channel': 'gzh',
}).encode('utf-8')
```

双通道（同一key已有xhs版，加写gzh版）：走sqlite3 UPDATE，不用API PUT

```python
import sqlite3
db = '/Users/user/PG/XiaohongshuSkills/data/news_dev.db'
conn = sqlite3.connect(db)
conn.execute("UPDATE news SET wechat_title=?, wechat_content=?, channel='both' WHERE key=?",
    (gzh_title, gzh_content, full_40bit_key))
conn.commit()
```

## 批量密度检查

```python
python3 << 'PYEOF'
import urllib.request, json

checks = [
    ("<full40bitsha1>", "素材名"),
]
for key, name in checks:
    with urllib.request.urlopen(f'http://127.0.0.1:5000/api/news/{key}', timeout=10) as r:
        d = json.loads(r.read())
    rc = (d.get('rewritten_content', '') or '')
    cj = (d.get('content_ja', '') or '')
    fmt = d.get('format', '?')
    lf = d.get('is_long_form', 0)
    density = f"{len(rc)/max(len(cj),1)*100:.0f}%" if len(cj) > 0 and fmt == 'story' and lf == 1 else f"(news/{fmt})"
    print(f"{name:25s} rc={len(rc):>4d} cj={len(cj):>4d} density={density}")
PYEOF
```

## 关键陷阱

1. **key映射验证**：构建entries列表时，每行的key必须和draft的素材一致。在写入前用 `curl GET /api/news/<KEY>` 读title确认。
2. **截短key**：list端点返回的key是完整40位SHA1，不要截取前16位。
3. **双通道写入顺序**：xhs写rewritten_用API PUT，gzh加写wechat_用sqlite3 UPDATE——顺序不可颠倒，否则PUT会覆盖已写入的wechat_字段。
