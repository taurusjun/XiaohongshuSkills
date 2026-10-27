"""待发布推荐（机械，对齐原 skill 阶段6）。

- 选篇：**4 篇数据驱动**（时效 + 人物历史发布数据 + 分数）+ **1 篇随机探索**；+ 2 条备选。
- 数据总览：断更天数 / 本月每日发布量 / 候选池规模 / 待发队列条数。
- 冲突检查：避开明天已排期的人/题材；同人物/同事件不挤同一天。
- 只选篇 + 给时段；**不发布**（写库由 services.schedule.apply_plan 完成）。
"""
import datetime as dt
import random
import sqlite3
import statistics

from services import paths
from services import pub_history_gate as _gate

JST = dt.timezone(dt.timedelta(hours=9))
_SEARCH_OR = "title LIKE ? OR title_ja LIKE ? OR content_ja LIKE ? OR rewritten_title LIKE ?"

__all__ = ["candidates", "historical_stats", "priority", "stage6", "data_overview", "tomorrow_busy"]


def _day(s):
    s = str(s or "").strip().replace(".", "-")[:10]
    try:
        return dt.date.fromisoformat(s)
    except Exception:  # noqa: BLE001
        return None


def candidates(days=2, db=None):
    """待推荐候选池：preselected=1 且 publish_xhs=0 且已有 rewritten_content。"""
    import sys
    sys.path.insert(0, str(paths.REPO_ROOT / "scripts"))
    from sqlite3 import connect
    cutoff = (dt.datetime.now(JST).date() - dt.timedelta(days=days)).isoformat()
    conn = connect(db or paths.sqlite_path())
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT key,title,rewritten_title,created_at,title_score,format,is_long_form,"
            "related_keys,score_dims FROM news "
            "WHERE preselected=1 AND COALESCE(publish_xhs,0)=0 AND COALESCE(rewritten_content,'')!='' "
            "AND substr(created_at,1,10)>=? ORDER BY created_at DESC", (cutoff,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def historical_stats(entity, db=None):
    """某人物/实体的历史发布表现：已发条数 / 中位 xhs_views / 最高 / 最近3条。"""
    if not entity or len(str(entity)) < 2:
        return {"n": 0, "median": 0, "max": 0, "recent": []}
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        like = f"%{entity}%"
        rows = conn.execute(
            "SELECT xhs_views, publish_time FROM news WHERE substr(publish_time,1,4)>='2000' "
            f"AND ({_SEARCH_OR}) ORDER BY publish_time DESC LIMIT 200",
            (like, like, like, like)).fetchall()
    finally:
        conn.close()
    views = [int(r[0] or 0) for r in rows]
    recent = [(r[1] or "")[:16] for r in rows[:3]]
    return {"n": len(rows), "median": statistics.median(views) if views else 0,
            "max": max(views) if views else 0, "recent": recent}


def _merge_stats(stats):
    n = sum(s["n"] for s in stats)
    med = max([s["median"] for s in stats], default=0)
    mx = max([s["max"] for s in stats], default=0)
    return {"n": n, "median": med, "max": mx}


def priority(cand, stat, today):
    """数据驱动优先级：时效(过夜打对折) × (1 + 人物历史) + 分数微调。"""
    d = _day(cand.get("created_at"))
    days_old = max((today - d).days, 0) if d else 9
    fresh = 1.0 / (2 ** days_old)
    hist = stat.get("n", 0) + stat.get("median", 0) / 1000.0
    return round(fresh * (1 + hist) + 0.05 * (cand.get("title_score") or 0), 4)


def _reason(cand, stat, today):
    d = _day(cand.get("created_at"))
    days_old = max((today - d).days, 0) if d else 9
    parts = ["当天时效" if days_old == 0 else f"{days_old}天前（时效折半）"]
    if stat.get("n"):
        parts.append(f"人物历史 已发{stat['n']}条/中位{int(stat['median'])}/最高{stat['max']}")
    else:
        parts.append("人物无历史发布记录")
    if cand.get("title_score") is not None:
        parts.append(f"ts={cand['title_score']}")
    return "；".join(parts)


def tomorrow_busy(tomorrow, db=None):
    conn = sqlite3.connect(db or paths.sqlite_path())
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT key,title,title_ja,content_ja,rewritten_title,xhs_pub_time FROM news "
            "WHERE substr(COALESCE(xhs_pub_time,''),1,10)=?", (tomorrow.isoformat(),))]
    finally:
        conn.close()


def stage6(pool, entities_by_key, busy_entities=None, n=5, seed=None, today=None, db=None):
    """返回 (picks, backups, stat_by_key)。picks = n-1 数据驱动 + 1 随机。"""
    today = today or dt.datetime.now(JST).date()
    stat_by_key = {}
    for r in pool:
        ents = entities_by_key.get(r["key"], []) or []
        stat_by_key[r["key"]] = _merge_stats([historical_stats(e, db) for e in ents])
    busy_ents = set(busy_entities or [])
    # 发布历史闸门（10/22 事故后立规）：硬重复/题材疲劳/排期撞车的候选直接不进推荐
    _g = _gate.check_keys([r["key"] for r in pool], db=db, today=today)
    _passed = [r for r in pool if (_g.get(r["key"]) or {}).get("verdict") != "FAIL"]
    if len(_passed) < len(pool):
        print(f"  [闸门] 剔除 {len(pool)-len(_passed)} 篇（近 3 天同 tag 已发 / 14 天 ≥2 条 / 排期撞车）")
    pool = _passed
    ordered = sorted(pool, key=lambda r: -priority(r, stat_by_key[r["key"]], today))

    picks, used_ent, used_keys, pick_rel = [], set(busy_ents), set(), set()
    for r in ordered:
        k = r["key"]
        ents = set(entities_by_key.get(k, []) or [])
        if ents & used_ent:                       # 同人物不挤同一天
            continue
        rel = {x for x in (r.get("related_keys") or "").split(",") if x}
        if rel & pick_rel:                        # 同事件不挨着
            continue
        picks.append(r); used_ent |= ents; used_keys.add(k); pick_rel |= rel
        if len(picks) >= n - 1:
            break
    _cut = today - dt.timedelta(days=3)      # 10/27 事故：随机池不能全库捞（会抽到几个月前）
    rest = [r for r in ordered if r["key"] not in used_keys
            and (_day(r.get("created_at")) or _cut) >= _cut]
    if rest:                                      # 1 篇随机探索
        rnd = dict(random.Random(seed).choice(rest))
        rnd["_random"] = True
        picks.append(rnd); used_keys.add(rnd["key"])
    backups = [r for r in ordered if r["key"] not in used_keys][:2]
    return picks, backups, stat_by_key


def data_overview(today, pool, db=None):
    """§0 数据总览：断更天数 / 本月每日发布量 / 候选池规模 / 待发队列条数。"""
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        last = conn.execute("SELECT max(substr(publish_time,1,10)) FROM news "
                            "WHERE substr(publish_time,1,4)>='2000'").fetchone()[0]
        gap = (today - _day(last)).days if last else None
        month = today.strftime("%Y-%m")
        perday = conn.execute(
            "SELECT substr(publish_time,1,10) d, count(*) c FROM news "
            "WHERE substr(publish_time,1,7)=? GROUP BY d ORDER BY d DESC", (month,)).fetchall()
        pending = conn.execute("SELECT count(*) FROM news WHERE COALESCE(publish_xhs,0)=1 "
                               "AND COALESCE(publish_time,'')=''").fetchone()[0]
    finally:
        conn.close()
    return {"gap_days": gap, "perday": [(d, c) for d, c in perday],
            "pool": len(pool), "pending": pending}


def _tier(cand, stat, med_all):
    """牌型（对齐生产：分数最高但本人近期走低 → 质量牌）。"""
    ts = cand.get("title_score") or 0
    if not stat.get("n"):
        return "新人（无历史发布）"
    if ts >= 4.5 and (stat.get("median") or 0) < med_all:
        return "质量牌"
    return "流量牌"


def reasons(picks, stat_by_key, today):
    # 牌型参照：候选之间中位浏览量的中位
    meds = [(stat_by_key.get(r["key"], {}) or {}).get("median") or 0 for r in picks]
    med_all = statistics.median(meds) if meds else 0
    out = []
    for r in picks:
        s = stat_by_key.get(r["key"], {}) or {}
        tag = "[随机探索] " if r.get("_random") else ""
        out.append(f"{tag}{_reason(r, s, today)}｜{_tier(r, s, med_all)}")
    return out
