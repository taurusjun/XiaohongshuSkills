# 10/1 跨日积压批实作（4 天积压 / 本会话完成 3 天 / 78 篇落地）

配套的技术坑（长度校准、plan.json 静默 SKIP、patch 锚点、顿号脚本、renwei/kana 新增项）
见 `10-01-length-and-patch-pitfalls.md`。

## 1. 启动判断：怎么发现「不是单日批，是积压批」

不要只看当日 review 存档。**跨日检查一次跑完 7 天**（用当日 `l1_raw_<date>.json` 或
`curl --noproxy '*' ".../api/news?date_from=D&date_to=D&limit=500"`）：

```python
for r in rows:
    preselected / rewritten_title / score_dims        # 三个字段全空 = 该日未写
```

10/1 实测结果：

| 日期 | total | preselected | rewritten | 结论 |
|------|-------|-------------|-----------|------|
| 9/20~9/27 | 35~63 | 10~34 | 7~24 | 已写 |
| 9/28 | 52 | 0 | 0 | **漏写** |
| 9/29 | 55 | 0 | 0 | **漏写** |
| 9/30 | 57 | 0 | 0 | **漏写** |
| 10/1 | 55 | 0 | 0 | 当日 |

最后写入日 = 9/27（`score_dims` 24 条、`publish_mode=rewritten` 8 条）。
再核对 `references/` 下有没有 9/28~9/30 的 batch-flow 文件 —— 没有就是漏写日，
全部纳入本轮。

**顺序：最新 → 最旧**（10/1 → 9/30 → 9/29 → 9/28）。**每一天走完整 A→B→C 闭环再进下一天**，
保证被会话上限截断时已完成的部分是落库的，而不是躺在 /tmp。

## 2. 规模与工具预算（重要）

四天目标量 ≈ S24 + A31 + AKB57 ≈ **112 篇**。实际一天 25~28 篇的闭环
（写稿→precheck→renwei→入库→score_dims）就要 **20~25 轮工具调用**。
本会话撞了一次工具上限（10/1 批入库+review 完成后被切断，9/30 的 4 篇 S 稿滞留在 /tmp）。
**结论：不要因为量大就停下来问用户「要不要都写」。** 用户对「接下来做 X 可以吗」这类
请示极度反感（说「继续」即授权整条流程），量大不构成提问理由。正确做法是
**按「每天一个完整闭环」推进**：写稿 → precheck → renwei → 入库 → score_dims 当天收口，
再进下一天，这样被会话上限截断时已完成的部分是落库的，不是躺在 /tmp。
只有在被工具上限**强制**切断时才报告进度，并明确写出剩余天数与条数。

## 3. 本批台账（已落库，可对账）

### 10/1（28 xhs + 13 merged，score_dims 41）
- story 13：`dfe01d764c81`(8.5) `95ff1f648dba`(8.7) `22178c8ac982`(8.7) `cb1fe978e4e8`(9.0)
  `7cd2db600bce`(9.1) `beef895f4023`(8.8) `c21aef48e09a`(8.4) `0f68079bc3e8`(9.0)
  `3349147d2f94`(8.2) `7aef0c33a256`(8.6) `897ed5b1a2c6`(8.3) `345622d0b5f0`(8.0) `f7cb19970261`(7.8)
- news 15：`51a6cdca09c1` `9a332fdc4a40` `796d3aca751d` `2323fd848f9c` `4974417bb404`
  `761d8927b64b` `0dc60fb073ef` `dc26ecc734d9` `6baa86e18149` `207213ecc335` `16c5ce9c8ae4`
  `4a967e02d07c` `e5c4a2e829ae` `aa90e54e8ca9` `c4909059bff6` → 全部 `score_dims='news-pass'`

### 9/30（25 xhs + 1 gzh + 13 merged，score_dims 39）
- story 11：`db1444086ed5`(8.6) `2d7c82cdac02`(8.6) `c8ecad4eff99`(8.5) `97d11a034b15`(8.7)
  `9fe2b17b9d5a`(8.3) `4802d6a45bcc`(8.2) `138a8a5042bb`(8.2) `092851ee474f`(8.6)
  `c533e0d86011`(8.9) `98acdffbc5c8`(8.2) + **gzh** `717804b645b4`(8.5)
- story 上限项：`411471e59190`(7.9，活动公告类天然上限)
- news 14：`885dbee17862` `5dca96fd0e81` `bef6846d0ca1` `1c0ed7fc752f` `d5c3084aa29b`
  `37062b06be4f` `f944f6b45bc3` `2c072b664e46` `817df38bbbed` `abba3fe9608c` `d4978e041589`
  `c922ab83ed60` `99db77b2667e` `b2272c99805d`

### 9/29（25 xhs + 5 merged + 1 skip，score_dims 30）
- story 11：`d992d49589ad`(9.1) `94909896771f`(8.9) `faff26d4da84`(8.7) `b7b222f6f1ed`(8.7)
  `4d13f39d45c5`(8.7) `ba5b2928912a`(8.6) `00bc33298f3e`(8.3) `ccf39c9c12e3`(8.3)
  `b114437132ca`(8.2) `e0416e22bfad`(8.1) `1b0427b9c58d`(7.8，多艺人 report 豁免)
- news 14：`e5a358d01415` `df06412d07c1` `f463093bf13a` `3962058cd552` `211ba2099a21`
  `86f9960ffba4` `07e57666cfc9` `e9ac6ee77360` `b6c3ff8e1b7b` `7b16ec07fe36` `f8bdcecad14f`
  `21931e1833b1` `55298f54b7bc` `428bd42e687d`

### 未完成
- **9/28**（S5 + A10 + AKB大TOP 17）完全未动
- 9/29 A 级 `2cafa6834887`（CDTV 10/5 出演名单，公告类）未写

## 4. 跨日去重归属（本批最高频的判断，10 组）

**规则：同一事件跨日出现 → 只在「素材最全 / 最晚一天」那一条成篇，其余挂在同一天的
主条上打 `preselected=1 + publish_xhs=0 + score_dims='merged-into-<主条key12>'`。**
跨日合并时 `merged-into` 指向另一天的主条也照写。

| 事件 | 被合并条 | 并入主条 | 主条所在日 |
|------|---------|---------|-----------|
| 新井彩永留学 | `c83e00d96c3f` `437c5db4df43` `8b46e0397b5f` | `9a332fdc4a40` | 10/1 |
| 有吉×深泽 X 骚动 | `fa5733523656` `4eb2c88d3ca4` | `7cd2db600bce` | 10/1 |
| 和田玛雅婚礼 | `e8bfad0cd921` | `51a6cdca09c1` | 10/1 |
| 增本绮良卒业 BACKS | `2e7371e6fe04` | `95ff1f648dba` | 10/1 |
| 大搜查线票房 | `1a4599c36ac7` | `9fe2b17b9d5a` | 9/30 |
| 我々は宇宙人 GP | `4022ae969d7a` | `b7b222f6f1ed` | 9/29 |
| 清水麻璃亚週プレ | `0af4a992fe25` | `c922ab83ed60` | 9/30 |
| 矢久保美绪移籍 | `c71d7a4bf8c5` | `d5c3084aa29b` | 9/30 |
| 金村美玖写真集 | `53bb4151ba6f` | `761d8927b64b` | 10/1 |
| 土屋太凤×LiSA MV（强搁置） | `417592a9e4bb` `2ac5fe44b77d` `29a3b953b1bd` `c506df2722539a` | — | `status='skipped'` |

同事件合并的**簇内主条选择**：Prime Video 十月片单簇（`345622d0b5f0` 为代表，吸收
`013c6bbe`/`2b5fbc17`/`9ebb7bcc`/`ab38d947`/`01e8dfd43035`/`2ce82f4b`）、
Snow Man《AMENITY》簇（`7aef0c33a256` 主 + `c4909059bff6` 独立短稿，`67ebdf1d` 合并）。
review 第二层的「同簇只上 1~2 条」优先于「逐条成篇」。

**被吸收为 `related_keys` 的 key 保持 `preselected=0` —— 这是预期状态，不是漏写。**
（本批如 `6352a4b722695e` 并入 `0f68079bc3e8` 后仍为 0，次日跨日检查要能一眼区分。）

## 5. 复核口径

入库后逐日核验，三个数字要自洽：

```bash
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=D&date_to=D&limit=500"
# 期望：preselected == rewritten + merged 条数（+ 已 skip 的另计）
#       score_dims 计数 == selected 的全部
```

10/1：total 55 / preselected 41 / rewritten 28 / score_dims 41 / merged 13 ✅
9/30：total 53 / preselected 38 / rewritten 25 / gzh 1 / score_dims 39 ✅
9/29：total 54 / preselected 30 / rewritten 25 / score_dims 30 ✅

未处理清单应只剩 B 级（轻量跑量池）与 C 级（跳过）—— 若出现 S/A/AKB 大TOP 的 key，
说明漏标。
