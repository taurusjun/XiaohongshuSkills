# 10/18 单日批实作（50 条素材 → 11 xhs + 2 gzh + 8 merged + 29 跳过）

## 启动状态
- 当日 review 存档存在（cron 01:36，50 条，S 5 / A 5 / AKB大TOP 5 / B 10 / C 25）。
- 前一日 10/17 批次已完整落地（61 条中 14 xhs + 1 gzh + 4 bullet + 10 merged，未写的 47 条全部是文档化的 B/C/bullet/merged），**无跨日漏写**。
- 当日 50 行 `preselected` 全 0，keymap 按 12 位前缀建，40/40 命中。

## 落库台账
| key12 | 体裁 | publish_method | 正文(xhs口径) | 密度 | 分数 |
|---|---|---|---|---|---|
| da8b3d09b60e | story | post | 858 | 77.4% | 8.8 |
| e845ddbd5e54 | story | post | 854 | 56.3% | 8.9 |
| 194a48404f57 | story | post | 851 | 32.0% | 8.5 |
| f77cfc6e82f2 | story | **export** | 1147 | 31.4% | 8.7 |
| ddfff1d64050 | story | post | 856 | 50.7% | 8.3 |
| ac49d4304049 | story | post | 857 | 81.3% | 8.5 |
| 52ca8059f1d9 | story | post | 868 | 72.6% | 8.9 |
| ef3c6e148e1a | story | post | 824 | 81.3% | 8.7 |
| cce8d929d701 | news | post | 470 | 70.6% | news-pass |
| 97a5d764d3e7 | news | post | 408 | 59.6% | news-pass |
| c1bd0266f8f0 | news | post | 397 | 58.7% | news-pass |
| c67ea8a2c643 | news→**gzh** | — | 1019 | — | gzh 8.2 |
| ce164a7e5ac3 | story→**gzh** | — | 1096 | — | gzh 8.3 |

gzh 改道依据：`c67ea8a2`（佐久间 6→9 人本音，review 强搁置信号「217 条 0 发布」）、`ce164a7e`（芳根京子，同剧 5 波 0 发布 → 情感号方向）。

## 跳过的条目（供次日跨日检查）
- A2 `8f2a4b8d` 岩本照×真田十勇士主题曲（review 明写等 10/23 开播日一次打透）
- A3 `5ea22b76` 道枝少女漫番组（同日道枝已 3 条，review 优先级 8 明写「并入 S5 一句提及或跳过」）
- B 级 10 条全不写；C 级 17 条（WEC 误挂 3、动漫簇 2、纯晒照 4、非目标赛道与簇内重复）

## merged 8 条
- `bf9356ab` / `57bd94fb` / `50c86eac` / `e35fb3f8` / `47c76b8b` → `e845ddbd5e54`（FIDA 大会 7 稿，保留 e845ddbd + 52ca8059 两视角）
- `8afeace5` / `e557f153` / `0f455f60c34c` → `da8b3d09b60e`（伊藤卒业 5 稿，保留 da8b3d09 + cce8d929）

## 本轮新坑 / 复验

1. **gzh draft 不能进 `batch_precheck` 的 plan.json。** 脚本按 `<dir>/gzh_draft_<key12>.md` 能识别文件，但判据仍用 DB 的 `fmt/lf`：gzh 长文正文本来带 `##`，若该 key 是 news（如 `c67ea8a2`）就会撞「news 正文不得有 `##`」FAIL。**做法：plan.json 只放 xhs key（gzh 自动 SKIP，不计 FAIL），gzh 单独校验字数/假名/顿号行/新字体/标题。** 若把 gzh 加进 plan，得先手工造一个假 fmt，得不偿失。

2. **`rep()` 里写 `assert old in c` = 整段 heredoc 静默中断（10/1 规则再犯）。** 本轮一次 `assert` 失败（原文与记忆的措辞差一个「的」），脚本在第 25 行抛错，**后面 5 篇的扩充补丁全部没执行**，但 stdout 只显示一行 traceback，容易误以为「已全部应用」。修法同 10/1：补丁 helper 一律
   ```python
   def rep(fn, old, new):
       c = open(fn).read()
       if old not in c: print('MISS', fn, old[:24]); return
       open(fn,'w').write(c.replace(old,new,1))
   ```
   跑完必须回读字数确认（本轮靠「字数没变」才发现前一轮没应用）。

3. **标题里塞英文作品名会把字数顶穿。** `86岁跳完《Heavy Rotation》，高桥南在台下红了眼` 被 `--check-title` 判 31 字（拉丁 14 字符 ×0.5 = 7，加上《》，标点与中文叠加），而中文上限 20。改成「86岁跳完那支舞，高桥南红了眼」= 15 通过。**写首行标题时就先跑 `--check-title`**，含英文曲名/节目名的标题一律先算再落笔。

4. **落笔字数仍比直觉短 20~25%。** 首批 4 篇目标 900，实测 723 / 665 / 689 / 739；扩到 850+ 平均要补 2 段。story 目标直接按 880~900 规划，别按 800 写。

5. **总账口径：merged 条本身就在 C 级清单里，不能重复计入「跳过」。** 本轮 50 = 13 写入（11 xhs + 2 gzh）+ 8 merged + 29 跳过；若把 merged 同时算进「跳过」会得出 58 > 50。汇报前先做一次加法校验。

6. **review 给出的「今日建议 3-4 篇」不是上限。** 指令要求覆盖 S+A+AKB大TOP，本轮实际 13 篇；review 的「建议成稿数」是流量判断，不是范围约束——范围以分级清单为准，跳过项必须逐条写明理由（A2/A3 的跳过理由直接引用 review 原文）。
