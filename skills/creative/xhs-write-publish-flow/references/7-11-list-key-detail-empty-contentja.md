# API list端点key读detail返回空content_ja的应对

## 现象

当从 `GET /api/news?date_from=...&limit=200`（list端点）拿到一批key，然后用其中某个key调 `GET /api/news/<key>`（detail端点）时，返回`content_ja: ""`。

但list端的`content_ja`列是有内容的。

## 根因

list端点和detail端使用了不同的key编码/索引。list端返回的key能查到row数据本身，但detail端点用同一key查不到对应的content_ja。

## 恢复流程

1. 确认list端点返回的完整key（40位hash）
2. 用list端点rows中的`content_ja`字段直接作为数据源（不从detail端点读）
3. 或者：尝试用list端点的key直接PUT（`{"ok": true}` 可能成功落盘——但建议先用list端的数据验证content_ja非空再写）
4. 验证：入库后用list端点 `?date_from=...&limit=200` 查rewritten_content是否更新

## 关键

**不要因为有key返回空content_ja就认为素材无内容。** list端点rows中的content_ja可能实际有数据。正确的恢复路径是用list端点取数据。

## 7/11验证

当天多次遇到此问题（板野友美、中村丽乃、ぼっちぼろまる的key），每次恢复都是用list端点title字段搜索定位到正确的key，然后用list端数据做后续处理。
