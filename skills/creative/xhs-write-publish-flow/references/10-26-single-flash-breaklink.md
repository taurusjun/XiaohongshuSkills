# 单篇快讯链路（用户临时报料：「XX 生了/结婚了，发个快讯」）

10/26 实测：横山由依生女快讯，从报料到入库+review 全流程约 8 分钟、20 次工具调用内完成。
这是「单篇」链路，不走 daily review 批量流程（当天没有 review 存档，读存档会卡死）。

## 五步

```bash
# 1) 先确认库里有没有（没有不等于素材缺失，只是没抓过）
bash query.sh --search "<人名>" --limit 20

# 2) 触发抓取 —— 一次放 2 个词，命中面比单词宽、耗时差不多
curl -s -X POST http://127.0.0.1:5000/api/trigger-fetch -H 'Content-Type: application/json' \
  -d '{"mode":"keywords","keywords":[{"keyword":"横山由依 出産","max":12},{"keyword":"横山由依 第1子","max":8}]}' --noproxy '*'
# → 2 个词 14 条新文，约 2.5 分钟全部落库

# 3) 轮询（sleep ≤45s，别一次 sleep 90 —— 前台默认 60s 超时会白打断一次）
sleep 45; curl -s --noproxy '*' http://127.0.0.1:5000/api/task-status/<task_id> | python3 -c "import sys,json;print(json.load(sys.stdin)['status'])"

# 4) 同事件多条的体量对比，挑主条
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=<今日>&limit=50" | python3 -c ...   # 打印 key/title/format/is_long_form
# 逐条 GET detail 打印 len(content_ja)，取最长的一条做写入 key（履历全、密度分母大）

# 5) 闭环：draft → batch_precheck → renwei → write-api → related_keys + merged 标记 → score_dims
```

## 坑

- **`batch_precheck.py` 只认 `xhs_draft_<key12>.md` / `gzh_draft_<key12>.md`。**
  写成 `<key12>.md` 或 `draft_<key12>.md` 都匹配不到，输出 `no draft matched ...` 但 **exit code 是 0** ——
  看着像通过，其实一个字都没检查。看到这行就当失败处理，先改文件名。
- **主条封面可能是雅虎临时图。** `image_url` 形如 `...-view.jpg?exp=10800`，抓取时 404 会让
  `~/.cache/xhs_images/<主key>/` 成为空目录。同事件的兄弟 key 一般已下到有效 `cover.jpg`：
  `cp ~/.cache/xhs_images/<兄弟key>/cover.jpg ~/.cache/xhs_images/<主key>/cover.jpg`
  即补上，不用重爬、也不用改 DB 的 `image_url`（发布流程按 key 目录取图）。
- **媒体之间数字打架时，写可核验的那个。** 10/26 后上翔太年龄：sponichi/nikkansports 写 39、
  oricon 写 40（他 10/23 刚满 40）。处理：正文只写年份（1986 年生），不写年龄。喜讯快讯的年龄
  本来不是卖点，为 1 岁之差在评论区被纠错不值。同一原则适用于「出道年份/第几期生」之类的小数字。
- **"彼女"这个代词就是性别信息。** 声明原文只写「尊く幼い彼女が…」，多家标题也写「女児」——
  快讯里直接交代「是个女儿」，比来回解释代词自然。
- **假名清理要连人名一起清。** 想把 2026 生育潮罗列进背景段时，「さとうほなみ」这类名字会带来
  假名计数爆掉。取舍：只留汉字名（星野源/新垣结衣/满岛光），把假名名字整组删掉，比硬造译名安全。
- **剧名/番组名含假名时整名省略。** 「はんなりギロリの頼子さん」直接写成「2018年首次主演连续剧」。

## 收尾口径（短 news）

- 主条：`publish_mode=rewritten`、`preselected=1`、`publish_xhs=0`（等用户确认再发）。
- 同事件其余条目：`related_keys`（40 位全键、不含主条自己）+ `preselected=0` + `score_dims='merged-into-<主条key12>'`。
- 主条评分：短 news 不套 5 维度 8 分线，`score_dims='news-pass'`（密度+格式+renwei 过即可）。
- `score_dims` 一律 sqlite3 UPDATE 落盘（API PUT 对该字段静默失败），落盘后逐条回查非空。
- 汇报里给「标题 + 40 位 key + 密度/字数/假名/renwei/precheck 一张表 + 正文全文 + 媒体数字冲突说明」。
