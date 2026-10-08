# 操作指南（命令示例 + 阶段细节）

原 SKILL.md 中所有内联命令和阶段操作步骤汇总于此。

---

## 阶段1：写前准备

### 1a. 确认key并通读content_ja

```bash
# 第1步：用get-key.sh确认key正确且content_ja>0
bash ~/.hermes/skills/creative/xhs-write-publish-flow/scripts/get-key.sh <关键词>
# 输出：完整40位key + content_ja长度 + preselected + publish_xhs
# content_ja=0 → 弃用此key，换关键词重查

# 第2步：通读全文
```

**⚠️ 关键陷阱（7/10验证）：`GET /api/news/<key>` 详情端点经常返回空！**  
**⚠️ 7/13补充验证：5/5个key全部detail为空但list端点有数据，100%命中率。建议写稿前批量从list端点rows取content_ja。**
**⚠️ 关键陷阱（7/10验证）：`GET /api/news/<key>` 详情端点经常返回空！**  \n**⚠️ 7/13补充验证：大量素材的detail端点空但list端点有数据。写稿前应从list端点rows获取content_ja，而非依赖detail端点。**\nAPI详情端点和全量列表端点返回的key可能后缀不同（已知bug）。**永远优先从全量列表端点的row数据中获取content_ja，而不是详情端点。** 如果详情端点返回空content_ja，不代表素材没有内容——换成从列表端点读取。

**更可靠的content_ja获取方式：**

```bash
# 从列表端点的rows中直接读取（不依赖详情端点）
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=YYYY-MM-DD&limit=200" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
for r in d['rows']:
    if '关键词' in (r.get('title','') or '') + (r.get('content_ja','') or '')[:200]:
        ja = r.get('content_ja','') or ''
        print(f'key={r[\"key\"]} | cj_len={len(ja)}')
        with open('/tmp/cj_关键词.txt', 'w') as f:
            f.write(ja)
        print(f'Saved to /tmp/cj_关键词.txt')
        break
"
```

**通读规则：** ≥1500 chars的素材必须读完全文，不能只读前500字。分析评论类素材后半段通常是核心论点。

### 1b. 体裁判断（根据DB字段，必须用 `sqlite3` 查询 `format` + `is_long_form`）

在通读content_ja之前先查DB体裁字段：

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT format, is_long_form, story_type FROM news WHERE key='<完整40位key>'"
```

输出示例：
- `story|1|` → **长文story**，写1000-1500字，必须`##`小标题分段
- `news|0|` → **短news**，写约900字，不强求小标题

| DB字段值 | 体裁 | 处理 |
|----------|------|------|
| `format='story'` 且 `is_long_form=1` | **长文story** | 写1000-1500字，必须`##`分段，5维度评分需≥8分 |
| `format='news'` 或 `is_long_form=0` | **短news** | 写约900字，密度+格式通过即可 |

**⚠️ 不得用正文xhs字数反推体裁** — 体裁由DB字段决定，不是正文写多少字决定。
- DB标记为 `story` 的素材即使正文写少了（例如600字），review仍按长文story标准（需≥8分+##分段）
- DB标记为 `news` 的素材即使正文写到1000+字，review仍按短news标准（不强求8分）
- 禁止在写稿阶段就说"这篇正文不到700字所以按短news写"——先查DB字段，再定体裁

详见 `references/content-routing.md`，`references/short-news-to-longform-expansion.md`。

### 1c. 同事件多key选主素材（7/1经验）

同一事件/同人物可能有多条DB记录（跨来源如エンタメ総合+音楽ナタリー+THE FIRST TIMES）。选主素材key的优先级：

| 优先级 | 依据 | 示例（7/1 目黑莲案） |
|--------|------|---------------------|
| 1st | `format=story` + `is_long_form=1` + 最长content_ja | `1da3...`(1559字, story, lf=1) |
| 2nd | `format=story` + `is_long_form=1` | `6389...`(1513字, story, lf=1) |
| 3rd | `format=news` + 最长content_ja | 仅作关联素材，不写story |

选主素材后，其余同事件key设为`related_keys`。不要在story素材存在时选news素材当作主key写稿——news的review标准低但产出质量也低。

### 1d. 关联素材查找

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT key, title FROM news WHERE title LIKE '%关键词%' ORDER BY id"
```

逐个读取每个素材的content_ja，合并作为完整参考素材库。

---

## 阶段3：编写

### 字数目标

```bash
SCRIPT=~/.hermes/skills/creative/xhs-write-publish-flow/scripts/xhs_word_count.py

# 正文字数（所有字符×1，换行不计）
python3 $SCRIPT "正文内容"                      # → 920字
python3 $SCRIPT --check "正文内容" 1000         # → ✅  920字/1000上限

# 标题长度（CJK×1，英文/数字/假名×0.5）
python3 $SCRIPT --check-title "标题文字" 20     # → ✅  标题19字/20上限  （story 同限，无 64 例外）
```

正文目标：story（lf=1）≥800字（ja>3000 按 `max(800, ja×0.31~0.33)` 写足）；**news（lf=0）按素材体量写，不要硬凑 900 字** —— 门禁是「密度≥30%」而非字数，本批 news 成稿 223–486 字（ja 283–823）、密度 45–79% 全部通过，从 283 字素材硬写 900 字＝注水＝编造。标题：**一律 ≤20字（news 与 story 同限，无例外）**，写标题时就跑 `--check-title "<标题>" 20`（超限最常发生在 21–23 字；10/6 待发队列 6 条里 4 条 22–26 字被整批退回）。

### 三输出格式

一次LLM调用输出三部分：
1. **中文稿**：rewritten_title + rewritten_content
2. **英文稿**：en_title + en_content  
3. **英文推文**：en_tweet（≤280 chars，纯英文，2-4个具体hashtag）

推文规则详见 `references/en-tweet-rules.md`。

### Draft文件规范

```
/tmp/xhs_draft_<关键词>.md 的格式：
第一行 = 标题（入库时单独提取为rewritten_title）
第二行起 = 正文（入库时提取为rewritten_content）
```

入库时：
```python
lines = open("/tmp/xhs_draft_xxx.md").read().strip().split('\n')
title = lines[0].strip()
body = '\n'.join(lines[1:]).strip()
```

⚠️ 整块 `open().read()` 写入会把标题行污染进rewritten_content。

---

## 阶段4：入库

### 推荐方案：ALL_PROXY="" python3 内联

```python
# 完整入库示例（推荐）
ALL_PROXY="" python3 -c "
import json, urllib.request
lines = open('/tmp/xhs_draft_xxx.md').read().strip().split('\n')
title = lines[0].strip()
body = '\n'.join(lines[1:]).strip()
data = json.dumps({
    'rewritten_title': title,
    'rewritten_content': body,
    'en_title': 'English Title',
    'en_content': 'English content...',
    'en_tweet': 'Tweet...',
    'publish_mode': 'rewritten',
    'preselected': 1
}).encode()
req = urllib.request.Request(
    'http://127.0.0.1:5000/api/news/<完整40位key>',
    data=data, method='PUT',
    headers={'Content-Type': 'application/json'}
)
resp = urllib.request.urlopen(req)
print(resp.read())
"
```

公众号入库（channel=gzh）：
```python
data = {
    "wechat_title": "公众号标题",
    "wechat_content": "公众号正文...",
    "channel": "gzh",
    "preselected": 0,
    "wechat_publish": 0
}
```

详见 `references/python-urllib-inline-write.md`。

### 三步验证（入库后必做）

```bash
# 1. 基本验证
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT rewritten_title, LENGTH(rewritten_content), related_keys FROM news WHERE key='<完整40位key>'"

# 2. 检查正文是否混入标题行
ALL_PROXY="" curl -s --noproxy '*' "http://127.0.0.1:5000/api/news/<key>" \
  | python3 -c "
import sys, json
d = json.load(sys.stdin)
rt = d.get('rewritten_title','')
rc = d.get('rewritten_content','')
print('标题撞正文:', rc[:len(rt)] == rt)  # True = 有bug
print('正文前30字:', rc[:30])
"

# 3. 用 scripts/verify_rewrite.sh 综合验证
bash ~/.hermes/skills/creative/xhs-write-publish-flow/scripts/verify_rewrite.sh <key>
```

---

## 阶段5：Review

**密度检查通过后，加载 `xhs-content-review` skill（creative分类）执行5维度评分。**

### 内容密度检查（第一步，否决项）

```python
# 收集所有素材content_ja：主素材 + 每个关联素材
import urllib.request, json
keys = ['<主key>', '<关联key1>', '<关联key2>', ...]
all_ja = ''
for k in keys:
    d = json.loads(urllib.request.urlopen(f"http://127.0.0.1:5000/api/news/{k}").read())
    all_ja += (d.get('content_ja', '') or '') + '\n'

import re
sents = [s.strip() for s in re.split(r'[。！？]', all_ja) if s.strip()]
dedup = list(dict.fromkeys(sents))
dedup_ja = '。'.join(dedup)

# 计算密度：用 xhs_word_count.py 脚本，不手写xhs_len函数
import subprocess
body = d.get('rewritten_content', '') or ''

r_body = subprocess.run(['python3', 'scripts/xhs_word_count.py', body], capture_output=True, text=True, cwd='~/.hermes/skills/creative/xhs-write-publish-flow')
r_base = subprocess.run(['python3', 'scripts/xhs_word_count.py', dedup_ja], capture_output=True, text=True, cwd='~/.hermes/skills/creative/xhs-write-publish-flow')

import math
body_xhs = int(r_body.stdout.replace('字',''))
base_xhs = int(r_base.stdout.replace('字',''))
density = body_xhs / max(base_xhs, 1) * 100
print(f"密度: {density:.1f}% (正文{body_xhs}字 / {len(keys)}素材联合去重{base_xhs}字)")
# <30% → 退回，不进评分
```

⚠️ 禁止手写xhs_len函数。禁止只用主素材content_ja做基准。禁止用 `len(c_ja)` 当分母。

例外条款：正文≥800字且内容扎实但素材结构松散（大量重复流程描述/榜单数据）→ 阈值放宽至25%。见 `references/density-exception-flow-description.md`。\n多素材合并（≥8条，总原文13K+）：用主素材content_ja为基准。见 `references/multi-material-merge-density-edge-case.md`。\n歌单/榜单/曲目列表类素材（content_ja 90%+为列表噪声）：豁免密度计算，改判断信息增量。见 `references/playlist-tracklist-density-edge-case.md`。

### 5维度评分

```
爆发点（1-2）：开头钩子，第一段抓住读者
情绪价值（1-2）：读完后的情绪反应（感动/愤怒/惊讶/共鸣）
信息增量（1-2）：比日文原文多了什么新角度/深度
内容深度（1-2）：背景铺垫、人物关系、事件因果，路人能看懂
标题吸引力（1-2）：数字+矛盾+具体名词，去掉人名后路人仍有好奇心
```

评分诚信：每个维度认真想"为什么扣分"，8分已是很好，不给虚高分。
详见 `references/chinese-review-criteria.md`（供 `xhs-content-review` skill 参考）。

### 改稿循环

改稿逻辑由 `xhs-content-review` skill 内部处理（最多3轮）：**只修最低维度 → 重跑renwei → 重入库 → 重跑review**。

改稿时从素材content_ja挖已有引语/细节，不凭空生成。

---

## 续篇写作

同一事件第二波素材到来时，四种模式：

| 模式 | 适用 | 操作 |
|------|------|------|
| A：增量续篇 | 第二波是新进展（当事人新推/新声明/新转折） | 写增量篇，开篇不重复事件经过 |
| B：完整报道合并 | 第二波是行业分析/背景挖掘 | 以分析素材为主key，整合事件经过进开头，写独立完整报道 |
| C：合并更新旧稿 | 旧稿未发+新素材是补充细节（≤3个信息点） | 见 `references/merge-update-existing-draft.md` |
| **D：时间线回溯续篇** | 已发新稿覆盖了最新时间点，但旧素材（更早时间线）尚未被写成完整稿 | 用**旧素材的key**写入，设`related_keys=已发新稿的key`形成前后呼应 |

模式B选择指标（满足任意一条）：
- 第二波是评论/分析/法律解读类型
- 旧稿已发布（publish_xhs=1）
- 第二波无新的「事件进展」

**模式D实操要点（6/28案例：能条爱未）：**
- 已发稿：`ba4d2dde55e1...`（6/28「脚悬在空中」— 婚后一个月回顾）
- 续写key：`bf1fbd97b40d...`（6/6入库的「首秀」素材 — 婚礼后第6天的故事）
- 方向：从更早的时间点切入，为已发稿形成「前传」
- related_keys设成**已发新稿的key**（不是旧稿），形成时间线正向链接
- 续写时先读已发稿的rewritten_content，确保不重复已覆盖的内容

---

## 完整key获取（每次操作前必做）

```bash
# 方法A：sqlite3直接查（推荐）
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db \
  "SELECT key, title FROM news WHERE title LIKE '%关键词%'"

# 方法B：get-key.sh
bash ~/.hermes/skills/creative/xhs-write-publish-flow/scripts/get-key.sh <关键词>
```

⚠️ query.sh显示的key只有前16位，不跨session复用。
⚠️ API全量列表返回的key可能与DB实际key后缀不同（见`references/6-24-api-key-suffix-mismatch.md`）。
