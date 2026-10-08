# 6/10 Session: publish_xhs 字段约定 & 验证陷阱

## publish_xhs 字段含义（用户确认）

| 状态 | publish_xhs | xhs_pub_time | 含义 |
|------|------------|-------------|------|
| 未标记 | 0 | (空) | 普通入库素材，未进入发布流程 |
| 预发 | 1 | (空) | 已标记发布但未定时间 |
| 已排期/已发 | 1 | 有值 | pipeline已处理 |

**规则：** 写稿入库时只设 `preselected=1` + `publish_mode=rewritten`。不要动 `publish_xhs`。等用户说"发"时才设。

## JSON 转义符陷阱

某些 content_ja 字段包含非法 JSON 转义符（如 `\e` 来自抓取阶段的原始数据），导致 `json.loads()` 失败：

```
json.decoder.JSONDecodeError: Invalid \escape: line 4 column 19699 (char 19999)
```

**这不是API挂了也不是数据丢失**，只是某个 content 字段的转义问题。

**正确解码方式（替代 sys.stdin.read()）：**

```bash
curl -s "http://127.0.0.1:5000/api/news/<key>" | python3 -c "
import sys, json
d = json.loads(sys.stdin.buffer.read().decode('utf-8', 'replace'))
print(d.get('preselected'), d.get('publish_mode'), d.get('rewritten_title',''))
"
```

关键点：`sys.stdin.buffer.read().decode('utf-8', 'replace')`。

## 批量验证的容错策略

当批量验证多篇（10+篇）时，第一轮遇到 JSONDecodeError 就中断了。建议：

1. 用 SQLite 直接查关键字段（preselected, publish_mode, rewritten_title）
2. 或者每条单独用 `python3 -c` 语法（不跨多行）来执行，这样单条失败不中断整体

```bash
for k in "key1" "key2"; do
  curl -s "http://127.0.0.1:5000/api/news/$k" | python3 -c "import sys,json; d=json.loads(sys.stdin.buffer.read().decode('utf-8','replace')); print(d.get('preselected'), d.get('publish_mode'), d.get('rewritten_title',''))"
done
```
