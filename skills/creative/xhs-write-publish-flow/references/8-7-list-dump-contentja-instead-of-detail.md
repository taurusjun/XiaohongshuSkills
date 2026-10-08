# List dump 提取 content_ja（detail 端点 404 时的恢复路径，8/7 实测）

## 场景

批量写稿启动时，用 daily review 里的**短 key**（12位，如 `92e8b7608980`）去
`GET /api/news/<key>` 拉 content_ja，结果**全部 32 个 key 都 404**。

## 根因

- review 存档里写的是短 key（前12位）
- API detail 端点需要完整 40 位 SHA1 key
- list 端点 rows 里的 key 是完整 40 位，且 **rows 本身已带 `content_ja` 字段**——不需要 detail 端点

## 正确做法（一步到位）

1. 用 `date_from=<当天>&limit=200` 拉全量 list dump（含 `content_ja`）
2. 用 **`startswith(短key)` 前缀匹配** 在 rows 中找完整 key——**不要用 `dict.get(短key)` 精确匹配**（短 key 不是 dict 的 key，会全部 NOTFOUND）
3. 从 rows 直接取 `content_ja` 写文件，无需再调 detail 端点

```python
d7 = json.load(open('/tmp/list_0807.json'))
allrows = d7['rows']  # key 是完整 40 位
for short, name in targets.items():
    m = [r for r in allrows if r['key'].startswith(short)]
    if not m:
        print(f"NOTFOUND {name} {short}"); continue
    r = m[0]
    open(f'/tmp/ja/{name}.ja.txt', 'w').write(r.get('content_ja') or '')
```

## 关键陷阱

- `allrows.get(short_key)` 精确查找 → 全部 NOTFOUND（本 session 第一次尝试踩坑）
- 短 key 前缀匹配返回的 `r['key']` 就是完整 40 位 key，**后续 PUT 必须用这个完整 key**（沿用 7/20/7/29 教训）
- 如果当天 + 前一天都拉过 dump，把两个 list 的 rows 合并后统一匹配（跨日批次场景）

## 与已有 reference 的关系

- `7-11-list-key-detail-empty-contentja.md`：detail 返回空 content_ja
- `7-20-list-key-vs-detail-key-mismatch-reverse.md`：list key vs detail key 后缀不一致
- 本篇补充：**detail 整体 404 时直接放弃 detail，从 list dump rows 拿 content_ja**，且前缀匹配是唯一可靠方式
