"""发布历史闸门 —— 排期前机械核对「同一人物/同一团/同一题材」的最近发布史。

迁移自 skill: xhs-write-publish-flow/scripts/pub_history_gate.py（10/22 事故后立规）。
key 一律 40 位（AGENTS.md 硬规则）。

判定（任一命中 = FAIL）：
  1. 硬重复：同 tag 最近一次发布距今 <= max_gap 天（默认 3）
  2. 题材疲劳：同 tag 距今 <= days 天（默认 14）且期间已发 >= 2 条
  3. 排期撞车：同 tag 已被另一篇排在今天/明天
泛化标签（团名/品类）不参与判定。
"""
import sqlite3
from datetime import date, datetime

from services import paths

__all__ = ["GENERIC", "check_keys", "gate_filter", "main"]

GENERIC = {
    '日本娱乐', '日本文化', '日本新闻', '日本艺人', '日本演员', '日本偶像', '日本综艺',
    'アイドル', 'AKB', 'AKB48', 'akb48', '乃木坂', '乃木坂46', '日向坂', '日向坂46',
    '櫻坂', '樱坂', '樱坂46', 'SnowMan', 'Snow Man', 'なにわ男子', '浪花男子',
    '杰尼斯', '旧杰尼斯', '日本写真', 'グラビア', '动漫', '日本女演员', '日本男演员',
    '日本音乐', '日本电影', '日剧', '日本电视剧', '偶像', '女优', '声优',
    'SKE48', 'NMB48', 'HKT48', 'NGT48', 'STU48', '乃木坂三期生', '乃木坂四期生',
    '日本写真集', '健康管理', '女性健康',
}


def _tags(raw):
    return [t.strip() for t in (raw or "").split(",") if t.strip()]


def _index(rows, keep_key=False):
    m = {}
    for row in rows:
        tags, t = row[0], row[1]
        k = row[2] if keep_key else ""
        d = (t or "")[:10]
        if not d:
            continue
        for tag in _tags(tags):
            m.setdefault(tag, []).append((d, k))
    return m


def check_keys(keys, db=None, days=14, max_gap=3, today=None):
    """返回 {key: {verdict, problems, title, tags, detail}}；FAIL = 不允许进待发布。"""
    today = today or date.today()
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(news)")}
        has = lambda c: c in cols          # noqa: E731
        t_col = "COALESCE(tags,'')" if has("tags") else "''"
        ti_col = "COALESCE(rewritten_title,'')" if has("rewritten_title") else "''"
        px = "COALESCE(publish_xhs,0)" if has("publish_xhs") else "0"
        pt = "COALESCE(publish_time,'')" if has("publish_time") else "''"
        xt = "COALESCE(xhs_pub_time,'')" if has("xhs_pub_time") else "''"

        cands = []
        for k in keys:
            r = conn.execute(
                f"SELECT key, {ti_col}, {t_col} FROM news WHERE key=?", (k,)).fetchone()
            if r:
                cands.append(r)
        published = conn.execute(
            f"SELECT {t_col}, {xt} FROM news WHERE {px}=1 AND {pt}!=''").fetchall()
        scheduled = conn.execute(
            f"SELECT {t_col}, {xt}, key FROM news WHERE {px}=1 AND {pt}='' AND {xt}!=''").fetchall()
        pub_idx = _index(published)
        sch_idx = _index(scheduled, keep_key=True)

        out = {}
        for key, title, tags in cands:
            tlist = [t for t in _tags(tags) if t not in GENERIC]
            problems, detail = [], []
            for t in tlist:
                ds = sorted({x[0] for x in pub_idx.get(t, [])}, reverse=True)
                if ds:
                    gap = (today - datetime.strptime(ds[0], "%Y-%m-%d").date()).days
                    recent = [d for d in ds
                              if (today - datetime.strptime(d, "%Y-%m-%d").date()).days <= days]
                    detail.append({"tag": t, "last": ds[0], "gap": gap,
                                   "n_recent": len(recent)})
                    if gap <= max_gap:
                        problems.append(f"{t}: {gap}天前刚发({ds[0]})")
                    elif gap <= days and len(recent) >= 2:
                        problems.append(f"{t}: 近{days}天已发{len(recent)}条,最近{ds[0]}")
                for d, k2 in sch_idx.get(t, []):
                    if k2 == key:
                        continue
                    try:
                        gap2 = (datetime.strptime(d, "%Y-%m-%d").date() - today).days
                    except ValueError:
                        continue
                    if 0 <= gap2 <= 1:
                        problems.append(f"{t}: {d} 已被另一篇占用")
            out[key] = {
                "verdict": "FAIL" if problems else "PASS",
                "problems": sorted(set(problems)),
                "title": (title or "")[:34],
                "tags": tlist[:4],
                "detail": sorted(detail, key=lambda x: x["gap"])[:3],
            }
        return out
    finally:
        conn.close()


def gate_filter(keys, db=None, **kw):
    """返回 (可通过 keys, [(key, 原因)])。"""
    res = check_keys(keys, db=db, **kw)
    ok, bad = [], []
    for k in keys:
        r = res.get(k)
        if not r:
            bad.append((k, "key 不存在"))
        elif r["verdict"] == "FAIL":
            bad.append((k, "历史闸门 FAIL: " + "；".join(r["problems"])[:160]))
        else:
            ok.append(k)
    return ok, bad


def main(argv=None):
    import argparse
    import sys
    ap = argparse.ArgumentParser(description='发布历史闸门（候选池或指定 key）')
    ap.add_argument('--keys', default='', help='逗号分隔的完整 40 位 key')
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--max-gap', type=int, default=3)
    ap.add_argument('--db', default=None)
    a = ap.parse_args(argv)

    conn = sqlite3.connect(a.db or paths.sqlite_path())
    try:
        if a.keys:
            keys = [k.strip() for k in a.keys.split(',') if k.strip()]
        else:
            keys = [r[0] for r in conn.execute(
                "SELECT key FROM news WHERE preselected=1 AND COALESCE(publish_xhs,0)=0 "
                "AND COALESCE(rewritten_content,'')!='' ORDER BY created_at DESC")]
    finally:
        conn.close()

    res = check_keys(keys, db=a.db, days=a.days, max_gap=a.max_gap)
    fails = 0
    for k in keys:
        r = res.get(k)
        if not r:
            print(f"⚠️ {k} 不存在")
            continue
        if r["verdict"] == "FAIL":
            fails += 1
            print(f"❌ {k} {r['title']}")
            for p in r["problems"]:
                print(f"     - {p}")
        else:
            print(f"✅ {k} {r['title']}")
            if r["detail"]:
                d = r["detail"][0]
                print(f"     最近同tag: {d['tag']} {d['last']}({d['gap']}天前), 近{a.days}天{d['n_recent']}条")
    return 1 if fails else 0


if __name__ == '__main__':
    import sys
    sys.exit(main())
