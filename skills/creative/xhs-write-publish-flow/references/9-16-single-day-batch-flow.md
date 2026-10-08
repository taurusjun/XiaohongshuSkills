# 9/16 单日批实作（26篇 xhs + 1篇 gzh 改道）— 命令级复盘

**批次规模：** list dump（9/15+9/16 合并，123 行）→ 写稿 24 篇（7 story + 17 news）+ 2 条 AKB bullet（preselect-only）+ 1 篇 gzh 改道。renwei 0 篇 exit1、密度 33.0–90.4%、story 4 维度 review 全 ≥8。
**范围判定：** 先读当日 review 存档；用「list dump 的 `preselected==1 and rewritten_title非空`」一次遍历确认前一日（9/15）已写 23 篇，余 43 条为 C 级合并项/B 级轻量，8 个主稿 related_keys 均已齐（改动量 0）⇒ 只写当日批。

---

## 1. 素材落盘 + 体裁一览（一个脚本顶掉 N 次 curl）

```bash
mkdir -p /tmp/mat && python3 - << 'PYEOF'
import json
d=json.load(open('/tmp/list_0915_0916.json'))
rows={r['key'][:12]: r for r in d['rows']}
targets={'2fbdbc543e3a':'S 田村保乃毕业','f5299ec466c1':'rel', ...}
for k,label in targets.items():
    r=rows[k]; cj=r.get('content_ja') or ''
    open(f'/tmp/mat/{k}.txt','w').write(cj)
    print(f"{k} | {label} | ts={r['title_score']} | fmt={r['format']} lf={r['is_long_form']} | cj={len(cj)}")
PYEOF
```
list dump 的 rows **自带 `format`/`is_long_form`/`content_ja`**，所以「体裁字段判定」与「素材落盘」可以一次做完，不用逐条打 detail 端点。
**读取按 4–6 篇一组打印**：主素材全文，关联素材 `[:700~1100]` 截断。

## 2. 提交前机械自检一条龙（假名 / 新字体 / 双书名号 / 破折号 / 字数 / ##）

```bash
python3 - << 'PYEOF'
import re
newfont = re.compile(r'[発恵価壱歳嶋竜徳沢辺栄広県芸術戦争売買読選抜]')   # 与/国/教/争 不放进来，同形字会整篇误报
for k in TARGETS:
    c=open(f'/tmp/xhs_draft_{k}.md').read(); body='\n'.join(c.split('\n')[1:])
    print(k,'body=',len(re.sub(r'\s','',body)),'##=',c.count('\n## '),'###=',c.count('\n### '),
          'kana=',sum(1 for ch in c if '\u3040'<=ch<='\u309f' or '\u30a0'<=ch<='\u30ff'),
          '《《=',c.count('《《'),'dash=',c.count('——'),'newfont=',sorted(set(newfont.findall(c))))
PYEOF
```
本次命中并修复：`Aぇ！group`→`Ae！group`（片假名 ぇ）、`ほのの`→`HONONO`、`吉田綾乃クリスティー`→`吉田绫乃克里斯蒂`、`柳沢孝司`→`柳泽孝司`（沢）、`梅澤美波`→`梅泽美波`、`渋谷`→`涩谷`。

## 3. renwei：定位真实命中点（本次最大时间损耗点）

- renwei 的 L 行号 = 按 `\n` 切分的**整段**；报告预览截断 ~60 字；顿号枚举常在段落后半。
- 只改预览文本 → 同一段继续被报（252fee1347fe 连亏 2 轮）。
- 预检命令（**不要自写正则**，真条件见 SKILL.md 9/16 条）：

```bash
python3 -c "
c=open('/tmp/xhs_draft_XXX.md').read().split('\n')
for i,l in enumerate(c,1):
    if l.count('、')>=2: print(i, l[:200])"
```

- 批量跑 renwei 取退出码必须重定向取 `$?`，别走管道（管道拿到的是 tail 的码）：
```bash
for f in xhs_draft_*.md; do ALL_PROXY="" python3 ~/.hermes/skills/writing/renwei-writing/scripts/renwei-pre-commit.py "$f" > /tmp/rw_$f.out 2>&1; echo "$? $f"; done
```
本次 24 篇：exit0=17 / exit2=6（均单信号非聚集，按规则接受）/ exit1=1（修净）。

## 4. 批量入库骨架（PUT → GET 验证 → sqlite3 兜底，本次 24/24 一次过）

```python
payload={'rewritten_title':title,'rewritten_content':body,'publish_mode':'rewritten','preselected':1,'publish_xhs':0}
if rel: payload['related_keys']=','.join(km[x] for x in rel)   # 全 40 位
try:
    put(key,payload)                       # API PUT
except Exception:
    cur.execute("UPDATE news SET ... WHERE key=?", ...)   # sqlite3 兜底
g = GET(key)                               # 验：title 完全相等 + len(body)>150
if not (g['rewritten_title']==title and len(g['rewritten_content'])>150):
    sqlite3 UPDATE → 再 GET                      # 二次兜底
```
- 入库后机械复查（别靠肉眼）：`related_keys` 每个 `len==40` 且 `SELECT COUNT(*) FROM news WHERE key IN (...)` 反查存在；`rewritten_title[:1] != '#'`。
- **改稿后二次 PUT 必须重带 related_keys**，否则被清空（见 SKILL.md 9/16 条）。

## 5. gzh 改道入库（review 判「多连0发布→改道公众号」时）

```python
cur.execute("UPDATE news SET wechat_title=?, wechat_content=?, channel='gzh', wechat_publish=0, preselected=0, related_keys=? WHERE key=?", (...))
```
- 不用 API PUT（会全量覆盖 xhs 字段）；用 sqlite3 写 `wechat_` 字段。
- 验证三条：`wechat_title` 落盘、`channel=='gzh'`、`rewritten_content==''`（xhs 字段未被污染）。
- related_keys 指向同人物历史 key（本次 5 条：9/14×2、9/7、9/3、7/30），用 `SELECT key FROM news WHERE key LIKE 前缀||'%'` 解析全 40 位。
- score_dims 落盘格式：`gzh去魅通过/背景锚定通过|8.2分|三维度：标题8.0/叙事8.5/适配8.0。<一句话理由>`。

## 6. score_dims 落盘 + 复核

`UPDATE news SET score_dims=? WHERE key=?`（sqlite3 直写，API PUT 会静默失败）；复核查询必须 `WHERE substr(key,1,12) IN (...)` 或 `key LIKE 前缀||'%'`——用 12 位前缀直接 `key IN (...)` 恒返回 0 行。
本次落盘：7 story 五维分（9.1/8.8/8.7/8.3/8.2/8.1/8.0）+ 17×`news-pass` + 2×`akb-top-bullet` + 1 gzh。

## 7. 名称中文化（本批新增/确认映射）

| 日文 | 中文写法 | 来源 |
|---|---|---|
| 川越にこ | 川越仁子 | DB 标题（`search=川越` → 7/30 记录） |
| Aぇ！group | Ae！group | 罗马字 |
| ほのの（相川暖花爱称） | HONONO | 罗马字 |
| 吉田綾乃クリスティー | 吉田绫乃克里斯蒂 | 音译 |
| 槇原/柳沢/梅澤/渋谷 | 柳泽/梅泽/涩谷 | 日文新字体→简体 |
| オモウマい店 | 便宜又好吃 | 意译（节目名） |

## 8. 跳过的条目（写进汇报，不设 preselected）

同素材已发（仓木华/上坂堇/板野友美）、同角度重复（金村美玖《EX大衆》第3次・西野七濑）、字数不足（斋藤飞鸟 327・本乡柚巴 252）、无人物钩子（朋友100人榜单通稿）、非目标赛道软文（帝国酒店蛋糕）。
