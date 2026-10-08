# 7/12教训：subagent 错误断言「search 不查历史数据」

## 现象

Layer23 子 agent 在跨时间关联环节使用 API search 后，在报告中写道：

> 「当前API的search参数仅支持查询active状态（今日入库）素材，published/archived状态的589条历史记录无法通过search检索。」

这是**错误的**。

## 验证

主进程在汇总阶段用同样的 search API 验证：

```bash
# 上白石萌音 — 返回9条，含6/22已发布记录
curl -s --noproxy '*' -G "http://127.0.0.1:5000/api/news" \
  --data-urlencode "sort_by=created_at" \
  --data-urlencode "sort_dir=DESC" \
  --data-urlencode "limit=5" \
  --data-urlencode "search=上白石萌音" | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(f'Total matches: {d.get(\"total\",0)}')
for r in d.get('rows',[])[:5]:
    pub = r.get('xhs_pub_time','') or '(未发)'
    print(f'{r[\"key\"][:12]} | created={r.get(\"created_at\",\"\")[:16]} | pub={pub} | {r[\"title\"][:50]}')
"
```

输出：
```
Total matches: 9
2d85a2f2f7ed | created=2026-07-12 00:52 | pub=(未发) | 上白石萌音第一集床戏晨间剧女主形象崩塌？
9f8265993620 | created=2026-07-09 00:48 | pub=(未发) | 川口春奈代言企业数第一
9f36bb57ec63 | created=2026-06-22 09:05 | pub=2026-06-22 12:06 | 川荣李奈从‘笨蛋’到吉卜力主演
d0b72679d05f | created=2026-06-21 18:23 | pub=(未发) | 上白石萌音是第一酒豪
b6a2e50f44cc | created=2026-06-20 09:16 | pub=(未发) | ORANGE RANGE
```

**包含6/22已发布(pub有日期)的记录** — 说明search确实查全库。

## 根因

子 agent 搜索了 10 个 S 级人物的关键词，各返回 0 条「直接关联的旧记录」（即同人物完全相同的新闻事件）。子 agent 错误地将「同批无关联」结论扩大为「search 不查过去数据」。

**因果谬误：** 「没有命中旧记录 ≠ search 不查旧记录」。子 agent 没有区分「search 返回了 0 条」和「search 不可用」这两个不同的事实。

## 应对

1. **Layer23 skill 已追加 pitfall**：明确要求子 agent 不要断言 search 不查历史
2. **主进程验证责任**：如果子 agent 报告负面技术断言（功能不可用、特性不支持等），主进程应当自行验证，不要直接采信
3. **宽松搜索策略**：当 search 返回 0 时，先用宽泛关键词（如「龟梨」而非「龟梨和也被整蛊」）再试一次，同时查看 total 字段
