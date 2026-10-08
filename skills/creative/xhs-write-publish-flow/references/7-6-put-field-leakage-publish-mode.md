# PUT 字段残留导致 publish_xhs=1 的无声泄漏（7/6经验）

## 问题场景

用 urllib PUT 写入 rewritten_content 时，payload 只传了 `rewritten_title`、`rewritten_content`、`preselected`，**没有显式设 `publish_xhs` 和 `publish_mode`**。

结果：API PUT是「合并更新」（只更新payload中出现的字段），旧数据的 `publish_xhs` 或 `publish_mode` 值会保留。如果旧数据中有 `publish_xhs=1`（之前测试或pipeline写入的残留），新稿写入时这个值**不会被清掉**。

## 具体案例（7/6）

大岛麻衣合并稿入库时没设 `publish_xhs=0` → 从旧数据继承了 `publish_xhs=1`。用户指出后才发现。

百合川美织已设 `publish_xhs=0`（因为之前PUT有传），但 `publish_mode=rewritten` 是旧数据自带的。

## 修复命令

```bash
curl -s --noproxy '*' -X PUT 'http://127.0.0.1:5000/api/news/<KEY>' \
  -H 'Content-Type: application/json' \
  -d '{"publish_xhs": 0, "publish_mode": "rewritten"}'
```

## 应设字段组合（每次PUT必须显式设）

```python
payload = json.dumps({
    'rewritten_title': tl,      # 改写标题
    'rewritten_content': bt,    # 改写正文
    'preselected': 1,          # 标记预选
    'publish_xhs': 0,          # ⚠️ 必须显式设0，不设可能继承旧值1
    'publish_mode': 'rewritten', # ⚠️ 必须设，告知pipeline这是改写文
    'related_keys': '...',     # 有关联素材时
}).encode('utf-8')
```

## 预防

入库后立即 GET 验证：
```python
d = json.loads(urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:5000/api/news/{key}')).read())
assert d['publish_xhs'] == 0
assert d['publish_mode'] == 'rewritten'
```
