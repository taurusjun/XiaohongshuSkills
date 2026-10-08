#!/usr/bin/env python3
"""批量入库脚本模板 —— 9/12 验证：31篇xhs + 2篇gzh 一次跑完，0失败，related全部40位。

用法
----
1) 拉当日（或跨日合并）list dump：
     curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&limit=300" > /tmp/dump.json
2) 把本文件复制成 /tmp/import_MMDD.py，只改下面 4 个变量：DRAFT / DUMP / REL / GZH
3) ALL_PROXY="" python3 /tmp/import_MMDD.py

draft 文件命名约定（必须程序化生成，禁止手写位数）
-------------------------------------------------
  /<DRAFT>/xhs_draft_<key前12位>.md     首行 `## 标题`，第2行起为正文
  /<DRAFT>/gzh_draft_<key前12位>.md     同上，但写 wechat_ 字段

每一条设计都对应一次历史踩坑
----------------------------
  * full(prefix)：当日 list keymap → DB `LIKE prefix||'%'` 兜底。
    跨日/更早的 related key（前一天的旧稿、9/8 的预览稿等）不在当日 list 里也能解析（9/5 教训）。
  * API PUT 包 try/except；异常或返回非 ok 立即回落 sqlite3 UPDATE（9/11 实测偶发 HTTP 500 / 请求挂起）。
  * 入库后按 rewritten_title 精确比对 + 正文长度 > 50 验证。
    截短 key / 指向错误行的 key 会静默返回 ok 而不落盘（7/20、7/26、8/18 教训）。
  * 末尾机械断言：related_keys 每个元素 len==40（8/22 的 12 位短 key、9/1 的 42 位多字符 key 教训）。
    只查长度查不出「恰好40位但写错字符」，如需更严再加 `SELECT key FROM news WHERE key IN (...)` 反向确认。
  * gzh 稿走 sqlite3 写 wechat_title/wechat_content + channel='gzh'，preselected=0，绝不碰 rewritten_。
    （API PUT 是全量覆盖，先写 xhs 再 PUT gzh 会清空 rewritten_）
  * score_dims 的落盘不走本脚本 —— 单独用 sqlite3 UPDATE（见 xhs-content-review 步骤4.5）。
"""

import json
import os
import sqlite3
import urllib.error
import urllib.request

DB = '/Users/user/PG/XiaohongshuSkills/data/news_dev.db'
DUMP = '/tmp/dump.json'
DRAFT = '/tmp/draftsMMDD'
API = 'http://127.0.0.1:5000/api/news'

# 合并稿 / 续篇：主key -> 关联key的前缀列表（8/12/14位均可，full() 会解析成40位）
REL = {
    # '98178e6e41ed': ['4600413dd586', '762bc10d516a', '4de34411a7a5'],  # 含跨日旧稿
}

# 走公众号的 key 前缀（写 wechat_ 字段，preselected=0）
GZH = set()

conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row


def full(prefix):
    """前缀 -> 完整40位key：当日/跨日 list keymap 优先，DB LIKE 兜底。"""
    k = km.get(prefix)
    if k:
        return k
    r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (prefix,)).fetchone()
    return r['key'] if r else None


rows = json.load(open(DUMP))['rows']
km = {r['key'][:12]: r['key'] for r in rows}


def put(key, payload):
    data = json.dumps(payload).encode('utf-8')
    req = urllib.request.Request(f'{API}/{key}', data=data,
                                 headers={'Content-Type': 'application/json'}, method='PUT')
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read())


ok, gzh_ok, fail = [], [], []
for fn in sorted(os.listdir(DRAFT)):
    if not fn.endswith('.md') or not (fn.startswith('xhs_draft_') or fn.startswith('gzh_draft_')):
        continue
    p12 = fn.replace('xhs_draft_', '').replace('gzh_draft_', '').replace('.md', '')
    key = full(p12)
    if not key or len(key) != 40:
        fail.append((p12, 'KEY_NOT_FOUND'))
        continue

    lines = open(os.path.join(DRAFT, fn)).read().strip().split('\n')
    # 首行 `## 标题` -> rewritten_title（lstrip('#') 同时覆盖 `#` 与 `##`，见 7/8 教训）
    title = lines[0].strip().lstrip('#').strip()
    body = '\n'.join(lines[1:]).strip()

    if p12 in GZH:
        conn.execute("UPDATE news SET wechat_title=?, wechat_content=?, channel='gzh', "
                     "preselected=0, publish_xhs=0 WHERE key=?", (title, body, key))
        conn.commit()
        gzh_ok.append((p12, len(body)))
        continue

    rel = ','.join(filter(None, (full(x) for x in REL.get(p12, []))))
    payload = {'rewritten_title': title, 'rewritten_content': body,
               'publish_mode': 'rewritten', 'preselected': 1, 'publish_xhs': 0}
    if rel:
        payload['related_keys'] = rel

    good = False
    try:
        res = put(key, payload)
        good = isinstance(res, dict) and res.get('ok')
    except Exception:
        good = False
    if not good:
        # 回落：直接写 DB，一次成功（改稿循环内的覆盖重写也一律走这条路，8/4 教训）
        try:
            conn.execute("UPDATE news SET rewritten_title=?, rewritten_content=?, "
                         "publish_mode='rewritten', preselected=1, publish_xhs=0, "
                         "related_keys=? WHERE key=?", (title, body, rel, key))
            conn.commit()
        except Exception as e2:
            fail.append((p12, f'SQLITE_FAIL {e2}'))
            continue

    chk = conn.execute("SELECT rewritten_title, length(rewritten_content) rl, related_keys "
                       "FROM news WHERE key=?", (key,)).fetchone()
    if chk and chk['rewritten_title'] == title and chk['rl'] and chk['rl'] > 50:
        ok.append((p12, chk['rl']))
    else:
        fail.append((p12, f'VERIFY_FAIL {dict(chk) if chk else None}'))

print(f'=== xhs OK {len(ok)} ===')
for a, b in ok:
    print(f'  {a} len={b}')
print(f'=== gzh OK {len(gzh_ok)} ===', gzh_ok)
print(f'=== FAIL {len(fail)} ===')
for a, b in fail:
    print(f'  {a} {b}')

bad = []
for p12, _ in ok:
    r = conn.execute("SELECT related_keys FROM news WHERE key=?", (full(p12),)).fetchone()
    for kk in (r['related_keys'] or '').split(','):
        if kk and len(kk) != 40:
            bad.append((p12, kk))
print('=== related key len!=40 ===', bad)
print('=== 残留 /tmp draft 未入库 ===',
      [f for f in sorted(os.listdir(DRAFT))
       if f.endswith('.md') and f.replace('xhs_draft_', '').replace('gzh_draft_', '').replace('.md', '')
       not in ({p for p, _ in ok} | {p for p, _ in gzh_ok})])
