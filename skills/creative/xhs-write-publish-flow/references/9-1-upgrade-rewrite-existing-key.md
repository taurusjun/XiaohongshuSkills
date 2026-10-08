# 升级重写（upgrade-rewrite）已有key的执行模式（9/1实作）

## 触发条件

review第3层标注同事件素材存在"纯重复风险"，且满足：
- 昨日（或更早）同事件素材已写稿**但未发布**：`preselected=1`、`publish_xhs=0`、`xhs_pub_time` 为空
- 今日素材 content_ja 明显更丰富（9/1案例：昨日弘中绫香 cj647 → 今日 cj1120）

此时review建议"升级重写"——用今日更丰富的素材**覆盖昨日key的rewritten_字段**，而不是在今日key上新建第二篇同事件稿。

## 执行步骤（9/1实测顺序）

1. **核对昨日key状态**：search API 查昨日key，确认 `rewritten_title` 非空 + `publish_xhs=0` + `xhs_pub_time` 为空 → 未发布 → 走升级重写；若已进入发布流程则跳过今日素材
2. **用今日素材写draft**：通读今日 content_ja，正常写稿（含 ## 小标题、假名自检、renwei）
3. **写入昨日key**（40位完整key，从search结果取），**今日key不写**（避免同事件两稿；今日key保持 preselected=0 或按需跳过）
4. **密度分母用今日素材的 content_ja**，不是写入key（昨日key）的 content_ja —— 昨日key的content_ja是旧素材，较薄，用它会虚高密度。9/1实作：
   ```python
   ('bdc08ac5416dad...', 'bd7b34adecaab4...', 1)  # (写入key, 密度分母key=今日素材)
   ```
5. **入库后验证昨日key**：rewritten_content 非空、preselected=1、publish_xhs=0
6. **score_dims 按写入key的DB format 判定评分门槛**：9/1昨日key是 `format=news` → 落盘 `news-pass`（即使正文是story级长文865字+##小标题）；若昨日key是 story lf=1 则按5维度评分

## 为什么不是写今日key

- 发布管道按 key 读 rewritten_content，同事件两稿=重复内容刷屏
- 昨日key已 presel=1 在队列中，覆盖它=升级队列里的稿子，不新增条目
- 今日key保留为素材记录，供后续关联检索

## 陷阱

- 假名自检必须覆盖**小标题行和正文中的日文原句**：9/1弘中稿「守りたいなら柔術やれよ」残留在 ## 小标题和正文各一处（KANA=16），预检脚本抓出后替换为「想保护就去练柔术吧」才通过
- 昨日key的 search 结果 key 与 list dump key 前缀位数可能不同，写入前用完整40位
