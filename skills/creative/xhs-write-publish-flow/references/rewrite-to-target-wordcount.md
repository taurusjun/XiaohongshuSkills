# 事后指定重改到目标字数（「这个按长文改到900字左右」）— 命令级流程

**触发：** 用户翻看待发队列 / 上一批汇报，挑中一篇说「这个有价值，需要按照长文在900字左右」「这篇改到900字」「按长文标准改」。

**性质：** 编辑性修剪，不是重写（见 SKILL.md「改到XX字 = 编辑性压缩」）。骨架、叙事线、风格全部保留，只动字数和结构瑕疵。

---

## 0. 先读现状（3 个字段必须看）

```bash
python3 - << 'EOF'
import sqlite3
DB='/Users/user/PG/XiaohongshuSkills/data/news_dev.db'
c=sqlite3.connect(DB)
r=c.execute("""SELECT key,title,format,is_long_form,preselected,publish_xhs,publish_mode,
                      rewritten_title,LENGTH(rewritten_content),score_dims,related_keys,
                      LENGTH(content_ja) FROM news WHERE key LIKE '<12位前缀>%'""").fetchone()
print('fmt=%s lf=%s presel=%s pub=%s mode=%s'%r[2:7])
print('rw_len=%s | score_dims=%s | rel=%r | cj=%s'%(r[8],r[9],r[10],r[11]))
print(r[7]); print('---'); print(open('/tmp/x.txt').read() if False else '')
EOF
```

- `format`/`is_long_form` → 决定改完后能不能带 `##`（story=要，news=不要）
- `rewritten_content` 现值 → 判断是压缩还是扩充
- `related_keys` → 记下来，重入库时必须原样回写

同时把主素材 `content_ja` 拉到 `/tmp/mat/<key12>.txt`（改稿时要回素材挖细节，不能凭记忆补）。

## 1. 体裁 → 结构（唯一判定标准是 DB 字段）

| DB 字段 | 用户说「长文」时的落法 |
|---|---|
| `story` + `lf=1` | 保留 / 补齐 `##` 小标题（≥2个），正文 ≥800 字；目标 900 字即 860–950 |
| `news` + `lf=0` | **段落流完整报道，正文一律不加 `##`**（9/3、9/5 两次因 news 带 ## 返工）。9/21 台风稿即此例：935 字的「长文」= 8 个自然段，0 个 `##` |

**回复里必须点明这个差别**，否则用户会以为漏了小标题。

## 2. 字数口径：只用 `scripts/xhs_word_count.py`

```bash
S=~/.hermes/skills/creative/xhs-write-publish-flow/scripts/xhs_word_count.py
python3 $S --check-title "音乐节说照常办，30多组偶像却集体辞演" 20   # ✅ 18字/20上限
```

**⚠️ 两套计数不要混用：** renwei 报告尾部的 `(xhs: N)` 与 `xhs_word_count.py` 的结果不同，后者更大。

| 稿 | 脚本 xhs_len | renwei 的 (xhs: N) |
|---|---|---|
| 秋元康 100分稿 | 941 | 918 |
| 台风稿 | 935 | 834 |

历次汇报（含本次）都用脚本口径 → **继续用脚本口径**，否则同一句「900字左右」两条稿数出来对不上。

逐段字数定位（找该砍哪段最快）：

```python
import sys; sys.path.insert(0,'/Users/user/.hermes/skills/creative/xhs-write-publish-flow/scripts')
from xhs_word_count import xhs_content_len
for l in open('/tmp/drafts/xhs_draft_XXX.md').read().strip().split('\n'):
    if l.strip(): print(f'{xhs_content_len(l.strip()):5d} {l[:34]}')
```

## 3. 压缩手法（保留清单 / 砍除清单）

**必留：** 开场金句、标题里的钩子事实、核心反差（秋元康「100分」vs AKB48「今天到此为止」）、当事人引语原话、关键数字（16首 / 70分 / 65分 / 11330日元）、支线（熊元的周边插曲、柏木父亲十年后才到场）。

**首发砍：**
- 重复的说明/致歉句（台风稿里事务局致歉与官网公告重复 → 只留一句）
- 冗余修饰（「继续北上」→「北上」）
- 次要配角发言（4 位家长的感想保留 2 位）
- **结尾的对称总结句**——红线禁概括收束，直接删，让文章收在最后一条具体事实上

## 4. 强制闭环（一步都不能省）

```bash
# ① 假名 + 新字体
python3 -c "c=open('/tmp/drafts/xhs_draft_XXX.md').read(); print(sum(1 for ch in c if '\u3040'<=ch<='\u309f' or '\u30a0'<=ch<='\u30ff'))"   # 必须 ≤5（本批两稿都为 0）
# ② 顿号预检（renwei 真条件）
#    re.search(r'([^，。！？]{2,8}[，、]){2,}[^，。！？]{2,8}(的|是|和)') AND 行内顿号≥2
# ③ renwei
ALL_PROXY="" python3 ~/.hermes/skills/writing/renwei-writing/scripts/renwei-pre-commit.py /tmp/drafts/xhs_draft_XXX.md
# ④ 重入库（sqlite3 UPDATE，related_keys 必须原样带回）
# ⑤ 密度 + 重评 + score_dims 覆盖落盘
```

重入库脚本模板（关键点：先读 rel 再写）：

```python
import sqlite3
c=sqlite3.connect(DB); cur=c.cursor()
fk=cur.execute("SELECT key FROM news WHERE substr(key,1,12)=?", (k12,)).fetchone()[0]
rel=cur.execute("SELECT related_keys FROM news WHERE key=?", (fk,)).fetchone()[0] or ''
d=open(f'/tmp/drafts/xhs_draft_{k12}.md').read().strip().split('\n')
title=d[0].strip().lstrip('#').strip(); body='\n'.join(d[1:]).strip()
cur.execute("UPDATE news SET rewritten_title=?, rewritten_content=?, publish_mode='rewritten', "
            "preselected=1, publish_xhs=0, related_keys=?, score_dims=? WHERE key=?",
            (title, body, rel, score_dims, fk))
c.commit()
# 验证：LENGTH(rewritten_content) / 每个 rel key len==40 且存在且不含自身
```

## 5. 顺手补漏：related_keys 为空的历史稿

改稿时发现该 key `related_keys` 为空 → 按「同企划主题稿 > 已发出的同系列稿 > 企划背景稿」优先级补挂 3 条。

**9/21 实例** `58925bff2975`（Cloud ten × 秋元康 × 柏木由纪，9/20 入库时漏挂）：

| 补挂 key | 内容 | 为什么选它 |
|---|---|---|
| `bcdbf6f8e1cd` | 被秋元康说「没有颜色」之后，他们找到了30种颜色 | 同企划主题稿（无色偶像），置顶 |
| `c799520655be` | 秋元康新剧场台场开张：首演成员泪洒现场 | 已发布，出道叙事时间线 |
| `c1511949d54e` | 秋元康新团 Cloud ten 开练 | 企划背景 |

找法：`search=Cloud ten` / `search=<人名>` 分别搜一次，取同企划/同人物的 presel=1 或已发记录。
