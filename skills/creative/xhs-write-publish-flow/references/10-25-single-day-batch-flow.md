# 10/25 单日批实作：56 条 → 9 xhs（story 5 / news 4）+ 1 gzh + 1 merged

**结论先行：本批零返工（batch_precheck FAIL 0，renwei exit 1 = 0，story 5/5 ≥8 分），
但入库阶段踩了一个「静默跳过」型 bug，靠逐条复核 DB 才揪出。写稿环节没出问题，
问题全部在「key 位数」和「文件名」这条链上。**

## 一、致命坑：review 存档混用 key 前缀位数 → 静默跳过

### 症状
- 导入脚本打印 `imported 9`（全部成功的样子），DB 里却只有 8 篇 xhs + 1 gzh。
- 缺的是 `d3375638`（萩原心花）。它的 `rewritten_content` 后来是 1955 字，
  但 `publish_mode='normal'`、`preselected=0`、`publish_method='post'`
  ——所有 flag 都没设上，只有正文被二次脚本误写进去了。

### 根因（两条，同源）
1. **review 存档里这条 key 是 14 位**（`d3375638711580`），其余是 12 位。
   我直接把它当 dict 键写 draft：`D['d3375638711580'] = "..."`。
   落盘文件名 = `xhs_draft_d3375638711580.md`（14 位）。
2. 导入循环遍历的是 `FULL = {fullkey[:12]: fullkey}`，拼路径 `f'xhs_draft_{k12}.md'`
   → `xhs_draft_d33756387115.md` → **文件不存在** → 循环里写了
   `if not os.path.exists(fn): continue` → 静默跳过，不报错、不计入失败。
3. 同一个 14 位 key 还写进了 `METHOD = {...'d3375638711580':'export'...}`，
   而循环用 `METHOD.get(k12, 'post')` 查 12 位，**取默认值 post**
   → 长文被错路由成图文。（后来单独 `UPDATE publish_method='export'` 修回。）

### 修法（写进脚本，别靠记性）
```python
D = {}                                    # 键一律用你从 list dump 拿到的完整 40 位 key
D[FULL_40] = """## 标题\n\n正文..."""

for full in D:                            # 遍历完整 key，不遍历任何前缀
    k12 = full[:12]                       # 唯一的切片点，只有这一处
    fn = f'{DRAFT}/xhs_draft_{k12}.md'
    method = 'export' if ja_len > 3000 else 'post'   # 由 cj 长度算，不查 dict
    ...
```
- **文件名与 METHOD 一律从同一个 `full[:12]` 派生**，禁止从 review 存档抄前缀。
- **导入循环里「文件不存在」必须报出来**：`print('NOFILE', k12); n_fail += 1`
  或直接 `raise`。`continue` 是本批唯一的致命写法——它让「8/9 成功」看起来像
  「9/9 成功」，且末尾的 `imported 9` 计数器不含跳过项。
- 收尾核对必须双数对齐：**`imported 数` == draft 目录文件数 == DB 里当天 rewritten 非空条数**。

### 复核命令（本批就是靠这一条揪出来的）
```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
 "SELECT substr(key,1,12), length(rewritten_content), publish_mode, publish_method,
         preselected, publish_xhs, substr(score_dims,1,12)
    FROM news WHERE created_at LIKE '2026-10-25%'
     AND (rewritten_content<>'' OR wechat_content<>'') ORDER BY 1;"
```
`publish_mode='normal'` + `preselected=0` + 正文非空 = 典型的「被二次脚本写到、
但首次导入没管到」的孤儿行。

## 二、DB 没有 `date` 列（API 的 `date` 是计算字段）

- `sqlite3 ... "WHERE date='2026-10-25'"` → `Error: no such column: date`。
- `PRAGMA table_info(news)` 里只有 `created_at` / `updated_at` / `pub_time` / `xhs_pub_time`。
- API list 的 `rows[].date` 是服务端算出来的。**直接查库一律用
  `date(created_at)='YYYY-MM-DD'` 或 `created_at LIKE 'YYYY-MM-DD%'`。**

## 三、gzh 稿的文件名也必须用 `gzh_draft_` 前缀

- 本批把公众号稿命名为 `xhs_draft_57de08691810.md`，结果被 xhs 批量的
  **标题 ≤20 字检查**扫到（gzh 标题天然更长，本批 23 字）→ 报 `❌ 超了! 标题23字/20上限`。
- gzh 标题不受 xhs 的 20 字限制，也不受「news 正文禁 ##」约束（gzh 本来带 ##）。
- **规则：`gzh_draft_<k12>.md`**。这样 `--dir` 扫描、标题检查、plan.json 都天然隔离
  （与 10/18「gzh 稿一律不进 plan.json」同源）。

## 四、plan.json 可以带 `name` 消掉人名 WARN

`batch_precheck.py` 本批对 9 篇全部输出
`! plan 未给 name，人名未校验（可选；人名非必须项）`。
plan 条目加 `'name':'岩本莲加'` 即做人名校验（防「写A素材把B人名写进去」的串味）。
可选，但批量 ≥10 篇时值得填。

## 五、export 篇数与「每日 1 篇」配额的冲突（要主动披露）

本批 9 条新稿里 4 条 `cj>3000` → export（真楪伶 4593 / 萩原心花 5673 / 岩本莲加 5858 /
北村优羽 4531）。过闸的 7 条里 4 条是 export，**硬压到 1 篇 export 就必须换掉三篇强稿**。

处理方式（本批采用）：
1. 上限放到 **2 篇 export**，post 插在中间两个时段做缓冲（09 export / 12 post / 15 export / 18 post）；
2. 随机探索位若又抽到 export，就在回复里**明说「5 篇里 3 篇长文、需三次浏览器确认」**，
   并给出「要压到 2 篇我可以换哪两篇」的具体替换项。
3. **不能默默排 3 篇 export 不吭声**——用户次日是要手动点确认的。

## 六、本批数字（可作下次校准基线）

| 项 | 值 |
|---|---|
| 素材 | 56（S5/A7/AKB TOP10/B18/C16），DB `created_at` 计 57（迟到的行） |
| 成稿 | xhs 9（story 5 + news 4）+ gzh 1 + merged 1 |
| 字数 | story export：1839 / 2143 / 1868 / 1489；story post：891；news：325-403 |
| 密度 | 31.4% / 46.7% / 32.9% / 32.9% / 84.1% / 49.2% / 56.2% / 51.5% / 50.2% |
| 评分 | 9.3 / 8.7 / 8.6 / 8.4 / 8.3 + 4×news-pass + gzh 8.5 |
| precheck | FAIL 0；renwei exit1 = 0，exit2 = 1（单处格言公式误判，商品级可忽略） |
| 弃用 | 11 条（同事件多 key 0 发 / 宣发连 0 发 / 纯重复已发 / 过期 8 天 / 素材过薄） |

**写长文时的落笔校准（本批实测）：** 目标 `ja×0.32` 的稿子，首轮中文落笔普遍
比目标短 15~20%（5858→1839 需补一段；5673→1868 补两处；4531→1489 刚好）。
按 `ja×0.32` 规划、写完立刻跑 `xhs_word_count.py`，一篇补一段即可到线，不必三轮。
