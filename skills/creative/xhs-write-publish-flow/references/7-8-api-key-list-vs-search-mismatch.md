# API key后缀不匹配陷阱 — 7/8第N次踩坑

## 问题

同一个素材在API list endpoint和search endpoint中返回的key后缀不同。

### 具体表现

| 来源 | 返回的key |
|------|-----------|
| `curl '.../api/news?limit=200'` （list） | `006b46918c6ec4c75609357e2e7f113fd7ebd065` |
| `curl '.../api/news?search=今田美樱'` （search） | `006b46918c6ec403425c87954155c3390e0b6a0f` |

**差异在后16位：** `7ebd065` ≠ `e0b6a0f`

引发的问题：从list取了key，用它对API做PUT返回`{"ok":true}`但DB数据不变。之后从search验证时找不到已写入的内容。

## 影响

入库时用list端点的key → PUT返回ok但不落盘 → 后续从search验证发现 `rewritten_title=''`。

## 解决方案

1. **写稿前从search获取key** — 不要用list端点的key做PUT。search返回的key才是正确的。
2. **入完库从search验证** — 不在list端点验证。如果key不匹配，从search拿正确key重新PUT。
3. **query.sh的key也只用search** — 不要从list端点的truncated结果去拼完整key。

## 7/8踩坑记录

- 今田美樱ECMO稿：第一次PUT用list key（`ebd065`），返回ok但没落盘。重新用search key（`e0b6a0f`）PUT才成功。
- 乙武洋匡稿：list key（`010bc1`）vs search key（`eacd3c`）。同理。
- 丘绿稿：list key（`6877986`）vs search key（`fbea4b`）。同理。
- 小栗歪头稿：list key入库失败未修。

注：已有reference `6-28-api-key-suffix-mismatch.md` 和 `6-24-api-key-suffix-mismatch.md` 记录了同样的问题，但每次还是踩。根本原因可能是DB迁移/索引重建导致同一条记录在不同查询路径下返回不同key。
