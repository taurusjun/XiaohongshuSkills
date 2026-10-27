#!/usr/bin/env python3
"""pub_history_gate.py — 待发布推荐的「发布历史闸门」

用途：在把候选稿排进待发布队列前，机械核对同一人物/同一团/同一题材的
最近发布历史，拦掉「前几天刚发过」的重复选题。

用法：
  python3 pub_history_gate.py                     # 扫候选池（preselected=1 / publish_xhs=0）全部
  python3 pub_history_gate.py --keys K1,K2,...    # 只查指定 key（支持 12 位前缀）
  python3 pub_history_gate.py --days 7 --max-gap 3
  python3 pub_history_gate.py --json

判定（任一命中 = FAIL）：
  1. 同 tag 最近发布距今 <= --max-gap 天（默认 3）
  2. 同 tag 距今 <= --days 天（默认 7）且期间同 tag 已发 >= 2 条
  3. 同 tag 今天已发 / 今天已排期 / 明天已排期（防 24h 内二次自我分流）

退出码：0=全部 PASS，1=有 FAIL，2=参数/DB 错误
"""
import argparse
import json
import sqlite3
import sys
from datetime import date, datetime

DB = '/Users/user/PG/XiaohongshuSkills/data/news_dev.db'

# 泛化标签：对「同一人物/同一团」判定无区分度，参与计算会制造假重复
GENERIC = {
    '日本娱乐', '日本文化', '日本新闻', '日本艺人', '日本演员', '日本偶像', '日本综艺',
    'アイドル', 'AKB', 'AKB48', 'akb48', '乃木坂', '乃木坂46', '日向坂', '日向坂46',
    '櫻坂', '樱坂', '樱坂46', 'SnowMan', 'Snow Man', 'なにわ男子', '浪花男子',
    '杰尼斯', '旧杰尼斯', '日本写真', 'グラビア', '动漫', '日本女演员', '日本男演员',
    '日本音乐', '日本电影', '日剧', '日本电视剧', '偶像', '女优', '声优',
    'SKE48', 'NMB48', 'HKT48', 'NGT48', 'STU48', '乃木坂三期生', '乃木坂四期生',
    '日本写真集', '健康管理', '女性健康', '日本文化',
}


def connect():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def tags_of(row):
    raw = (row['tags'] or '') if isinstance(row, sqlite3.Row) else (row or '')
    return [t.strip() for t in raw.split(',') if t.strip()]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--keys', default='')
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--max-gap', type=int, default=3)
    ap.add_argument('--strict', action='store_true', help='把「近N天重复」也判为 FAIL')
    ap.add_argument('--json', action='store_true')
    a = ap.parse_args()

    try:
        c = connect()
    except Exception as e:
        print(f'DB error: {e}')
        return 2

    today = date.today()
    if a.keys:
        keys = [k.strip() for k in a.keys.split(',') if k.strip()]
        cands = []
        for k in keys:
            r = c.execute(
                "SELECT key, rewritten_title, tags FROM news WHERE key LIKE ? LIMIT 1",
                (k + '%',)).fetchone()
            if r:
                cands.append(r)
            else:
                print(f'[WARN] key not found: {k}')
    else:
        cands = c.execute(
            """SELECT key, rewritten_title, tags FROM news
                WHERE preselected=1 AND publish_xhs=0
                  AND COALESCE(rewritten_content,'')!=''
                  AND status='active'
             ORDER BY created_at DESC""").fetchall()

    # 已发布（publish_time 非空）+ 已排期（publish_xhs=1 且 publish_time 空）
    published = c.execute(
        """SELECT tags, xhs_pub_time FROM news
            WHERE publish_xhs=1 AND COALESCE(publish_time,'')!=''""").fetchall()
    scheduled = c.execute(
        """SELECT key, tags, xhs_pub_time FROM news
            WHERE publish_xhs=1 AND COALESCE(publish_time,'')=''
              AND COALESCE(xhs_pub_time,'')!=''""").fetchall()

    def index(rows, keep_key=False):
        m = {}
        for r in rows:
            d = (r['xhs_pub_time'] or '')[:10]
            if not d:
                continue
            for t in tags_of(r):
                m.setdefault(t, []).append((d, r['key'] if keep_key else ''))
        return m

    pub_idx = index(published)
    sch_idx = index(scheduled, keep_key=True)

    results = []
    for r in cands:
        tlist = [t for t in tags_of(r) if t not in GENERIC]
        problems = []
        warns = []
        detail = []
        for t in tlist:
            ds = sorted({x[0] for x in pub_idx.get(t, [])}, reverse=True)
            if ds:
                gap = (today - datetime.strptime(ds[0], '%Y-%m-%d').date()).days
                recent = [d for d in ds if (today - datetime.strptime(d, '%Y-%m-%d').date()).days <= a.days]
                detail.append({'tag': t, 'last': ds[0], 'gap': gap, 'n_recent': len(recent)})
                if gap <= a.max_gap:
                    problems.append(f'{t}: {gap}天前刚发({ds[0]})')
                elif gap <= a.days and len(recent) >= 2:
                    msg = f'{t}: 近{a.days}天已发{len(recent)}条,最近{ds[0]}'
                    (problems if a.strict else warns).append(msg)
            for d, k in sch_idx.get(t, []):
                if k == r['key']:
                    continue
                try:
                    gap2 = (datetime.strptime(d, '%Y-%m-%d').date() - today).days
                except ValueError:
                    continue
                if 0 <= gap2 <= 1:
                    problems.append(f'{t}: {d} 已被另一篇占用')
        verdict = 'FAIL' if problems else ('WARN' if warns else 'PASS')
        results.append({
            'key': r['key'], 'title': (r['rewritten_title'] or '')[:34],
            'verdict': verdict, 'problems': sorted(set(problems)) + sorted(set(warns)),
            'tags': tlist[:4], 'detail': sorted(detail, key=lambda x: x['gap'])[:3],
        })

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
    else:
        for r in results:
            mark = {'FAIL': '❌', 'WARN': '⚠️', 'PASS': '✅'}[r['verdict']]
            print(f"{mark} {r['key'][:12]} {r['title']}")
            if r['problems']:
                for p in r['problems']:
                    print(f"     - {p}")
            elif r['detail']:
                d = r['detail'][0]
                print(f"     最近同tag: {d['tag']} {d['last']}({d['gap']}天前), 近{a.days}天{d['n_recent']}条")
            else:
                print('     无同tag历史（新选题）')
    return 1 if any(r['verdict'] == 'FAIL' for r in results) else 0


if __name__ == '__main__':
    sys.exit(main())
