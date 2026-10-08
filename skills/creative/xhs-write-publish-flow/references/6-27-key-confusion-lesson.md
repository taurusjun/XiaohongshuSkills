# 6/27 素材key查错教训

## 症状
用户说拉乌尔稿"很单薄"、"tmd"——重写后发现查错了key。

## 错误过程
1. 以为拉乌尔素材的key是 `20260626_xhs_hot_2687`（从query.sh或cron review输出中看到）
2. curl查这个key → content_ja=0
3. 因为content_ja=0，误以为素材没有日文原文，凭记忆和摘要写稿
4. 写的稿件只有730字，"30次落选→走秀"一条线，米兰社交、背法语、俳优获奖、stadium live全缺
5. 用户指出单薄

## 正确key
实际正确的拉乌尔素材key（content_ja=1961字）：
`93ed6382b471cde58abb82ea162033bf011c66e0` — "拉乌尔：30次失败是30次入场券"

## 根因
- `20260626_xhs_hot_2687` 是一个wrapper/汇总key，content_ja为空
- query.sh和cron review输出的key列只显示前16位，容易拿错
- 没有用 `get-key.sh` 先确认content_ja长度 > 0 再动笔

## 修复措施
1. 每次写稿前必须用 `bash ~/.hermes/scripts/get-key.sh <关键词>` 先查
2. 看第三列（content_ja长度）> 0 才能用这个key
3. content_ja=0的key说明没抓到日文原文，换关键词重查或弃用
