# 10/28 单日批实作（61 条 → 11 xhs + 2 bullet + 10 merged）

台账口径：`date(created_at)='2026-10-28'` 共 64 行（61 active + 3 discarded），成稿 11 篇，merged 占位 10，bullet 2，其余 38 条按 review 处置表跳过（含 C 级 17 条全弃）。

## 1. 三个新坑（本次实际付出返工成本）

### 1.1 手抄 40 位 key = 两次 AssertionError，整批回滚

批量入库脚本里把主 key 与学生成 related key 写成硬编码字符串，两处出错：
- `9b11ec2b…` 写成 `9b11ec2bfacd4177706d0704c9d7d6bc9f24fac`（漏了 `facd` 后的 `a`，39 位）
- `b59bcb18…` 中段错字符（长度 40 但字符不对，`startswith` 前缀查询才暴露）

因为 `assert len(full)==40` 抛在循环中途、`conn.commit()` 在末尾 ⇒ **前 5 篇已 UPDATE 但整体未提交，等于零写入**（重跑幂等，代价是白跑两轮）。

正确写法（本次修复后一次通过）：

```python
rows=[r['key'] for r in json.load(open('/tmp/list28.json'))['rows']]
main={p:[k for k in rows if k.startswith(p)][0] for p in P}       # 主 key 前缀解析
for rp in REL.get(p12,[]):
    cand=[k for k in rows if k.startswith(rp)] or \
         [r[0] for r in cur.execute("SELECT key FROM news WHERE key LIKE ?",(rp+'%',))]
    rel.append(cand[0])
```

配套纪律：①`assert` 只做兜底，不能替代解析；②断言失败要先确认「是否已 commit」——commit 在末尾即整批未落盘，重跑安全；③入库循环里 `os.path.exists(fn)` 必须 `raise`，不能 `continue`。

### 1.2 story 初稿只写 1 个正文 `##`（6/7 篇）

`batch_precheck` 报 `##=1 < 2`。根因：写稿时把首行 `## 标题` 算进了「我写了两个小标题」。

- 自检按 **`##` 总行数 ≥3** 数，不是凭感觉数「2 个」。
- **另一形态：插入型改稿把新 `##` 插在已有 `##` 之前 → 两个标题紧邻、中间无正文。`##` 计数照样 ≥2，precheck 不报**（本次 5d0eeada 中招，通读才发现）。查法：`grep -n '^## '` 看是否有连续两行标题。
- 删掉多余标题后 body 会跌到 799（差 1 字），必须靠素材内事实补回，不能靠加空行。

### 1.3 补字数段落 = 编造第一现场（本次 7 处）

为凑 800 字 / 凑小标题新增的段落里出现下列素材没有的句子，全部在通读时删掉：

| 编造句 | 性质 |
|---|---|
| 「节目组后来没有再回应这段争议」 | 事件后续，素材无 |
| 「熊元在旁边没接话，只叹了一口气」 | 配角反应 |
| 「市之濑加那说…把自己从上一段的呼吸里拔出来」 | 无源引语（素材只写她出席） |
| 「舞台致辞结束时…井上和还回头朝台下挥了一次手」 | 现场细节 |
| 「出道曲还会在 11 月的东京场再唱一次」 | 演出安排 |
| 「这次采访里她提到最多的是『位置』这个词」 | 人物心境 |
| 「日本国内的单独公演也办过多轮」 | 履历推断 |

machine gate（字数/假名/顿号/新字体/renwei）**一条都不拦**。唯一拦法是逐句问「信息源是 content_ja 哪一句」。删后字数跌回 791/734/769，补回的必须是素材内**另一组**未用事实（监督讲《偶像大师》出身、Nocturne 的旅行车设定、粉丝跟了二十年的原话、节目名《找出无色的偶像》），不是推断句回填。

## 2. review 建议与 DB 字数路由冲突 → 路由优先

review 对 C1 主题曲簇（`f8611087`，ja 3465）写「压字数、单点化写 800 字」（理由：载体 32 波 0 发）。**处置：路由按硬规则 `ja>3000 ⇒ export`，正文按主素材×0.31~0.33 写到 1112 字（密度 31.4%）**，不压到 800。理由：密度门以主素材为分母，800 字只有 23%，会被 `batch_density_check` 打回；载体疲劳属于「发不发」的判断，不该改写稿的字数路由。

## 3. 待发布闸门实测（11 条新稿只有 4 条过 `--strict`）

| 结果 | key | 驳回原因（tag 口径） |
|---|---|---|
| ✅ | f8611087 / 5d0eeada / 239eb7ce / 1419ac1d | 均「近 14 天 1 条」 |
| ❌ | 8e222498、a76a0903 | 道枝骏佑 / 井上和 近 14 天各 3 条（真重复） |
| ❌ | 5ff79506 | 共演 tag「大西流星」2 条（主角 n=1 未验证） |
| ❌ | 9b11ec2b | 正文引用的岩本莲加·梅泽美波 tag 2 天前发过（误伤） |
| ❌ | efd2db0c | 出身团「早安少女组」tag 3 天前发（误伤） |
| ❌ | 2b14dc7e | 脏 tag「游戏」（误伤） |
| ❌ | 0749db6e | 泛 tag「日本明星」（误伤） |

随机探索池按 §3.3 回退到 10/25–10/27 过闸候选（7ac4c6ace061 本田响矢 / 6287c590 村山彩希 / 0828ea2de383 Number_i），`random.sample` 抽中 7ac4c6ace061，与 4 篇主推无同人物/同事件冲突。

## 4. 队列字数与图集

- 随机位那篇 957 字 → 编辑性压到 891（`len(body)-换行` 口径，880–910），renwei 重跑 exit 0，`score_dims` 原值保留（候选池内压字不重评）。
- 图集：4/5 条有图（14 / 2 / 5 / 21 张），1 条 `error: 未检测到图集链接`（走封面）。**柏木稿落盘的是 13 张 `.webp` + 1 张 `article_00.jpg`**，用 `sips -s format jpeg` 转 jpg 后重写 `publish_images`（14 条），不要把 webp 路径直接交给发布侧。
- `pub_time_plan.py --apply` 的图集环节仍全 skip（`URL can't contain control characters`），排期后逐条 `POST /api/gallery-download/<40位key>` 补，再等 `done` 回填。

## 5. 收尾核对数字（本次实际值）

- 当日 64 行 / 有正文 11 / merged 10 / bullet 2
- 明日排期 5 条：08:55 post、11:53 post、14:57 post、18:07 **export**、20:06 post
- API `publish_xhs=pending` = 5，与排期条数一致；带 `publish_xhs=1` 的 merged/bullet 占位条 = 0
- 5 篇 kana=0、日文新字体 0、半译 0；`related_keys` 逐条 len==40 且不含自身
