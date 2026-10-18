"""关联候选（机械，全历史无窗口）：**按实体名检索**（人物/团体/作品/系列名）。

实体由 LLM 抽取并**分型**（见 agent/review.py 的 ENT_SYS）：
  p = 人物名/团体名        —— **检索主轴**（对齐生产 skill 第3层「用 API search= 搜同人物」）
  w = 作品名/系列名/角色名 —— 仅辅助；**只命中 w 的候选一律丢弃**（require_person）
      理由：角色名/作品名常与他人姓名撞车（舞台角色「千寻」↔ 须藤千寻），
      或把「同作品/同角色但不同人」拉进来（千寻扮演者川荣李奈）。
本模块再补 zhconv 简繁/地区字形变体。全库历史以简体中文为主、content_ja 为日文，
故用只读 DB 三字段 OR（title/content_ja/rewritten_title），比 API search= 更全
（API search= 不索引 content_ja 日文正文）。
"""
import sqlite3

from services import paths

__all__ = ["find_related", "variants", "terms"]


def variants(e):
    """把一个实体扩展为检索变体：原样 + zhconv 简繁/地区字形。"""
    out = []
    e = str(e).strip()
    if e:
        out.append(e)
    try:
        import zhconv
        for tgt in ("zh-cn", "zh-hant", "zh-hk", "zh-tw"):
            try:
                v = zhconv.convert(e, tgt)
            except Exception:  # noqa: BLE001
                v = ""
            if v and v not in out:
                out.append(v)
    except Exception:  # noqa: BLE001
        pass
    return out


def terms(entities):
    """entities → [(term, kind)]，kind ∈ {'p'(人物),'g'(团体),'w'(作品/系列/角色)}。接受：
       - {"p":[...], "g":[...], "w":[...]}（LLM 分型）
       - {"p":[...], "w":[...]}（旧两分型，兼容）
       - [str, ...]（扁平：**一律视为 p**，兼容旧调用与测试）
       - [{"n":..., "k":...}, ...]（容错）
    """
    out, seen = [], set()

    def add(name, kind):
        for v in variants(name):
            if len(v) >= 2 and (v, kind) not in seen:
                seen.add((v, kind))
                out.append((v, kind))

    if isinstance(entities, dict):
        for k in ("p", "g", "w"):
            for e in (entities.get(k) or []):
                add(e, k)
    else:
        for e in (entities or []):
            if isinstance(e, dict):
                kk = str(e.get("k") or "").lower()
                add(e.get("n") or e.get("name") or "", kk if kk in ("p", "g", "w") else "p")
            else:
                add(e, "p")
    return out


def _daynum(day):
    d = "".join(ch for ch in (day or "") if ch.isdigit())
    return int(d) if d else 0


def find_related(key, entities, limit=8, db=None, require_person=True, max_work_only=1):
    """entities: 分型实体（见 terms()）。返回 [{key,title,day,score,n,mx,np,ng,nw}]。

    排序（对齐生产 skill「同人物」口径）：人物命中 → 团体命中 → 作品命中 → 最长命中词 →
    **日期倒序（新的优先）**。
    require_person=True（默认）：至少命中 **人物/团体** 才算强候选；**只命中作品/系列/角色（w）**
    的候选最多保留 `max_work_only` 条并排在最后 —— 这类里既有「同事件的同剧素材」（如
    「七人共演千寻」，只靠角色名命中，必须留），也有「同作品不同人 / 同名不同人」
    （如千寻扮演者川荣李奈、须藤千寻，必须滤），**靠日期新 + 限量 + LLM 判定**区分。
    """
    ts = terms(entities)
    if not ts:
        return []
    conn = sqlite3.connect(db or paths.sqlite_path())
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(news)")}
        search = [c for c in ("title", "title_ja", "content_ja", "rewritten_title") if c in cols] or ["title"]
        sel = "key,title" + (",title_ja" if "title_ja" in cols else ", ''")
        sel += ",created_at" if "created_at" in cols else ", ''"
        where = " OR ".join(f"{c} LIKE ?" for c in search)
        # ⚠️ 必须 ORDER BY created_at DESC 再 LIMIT：否则 SQLite 按 rowid（≈最老）返回，
        # 命中数 >200 的热门实体（如人物名）会把**最新**的稿全部截断掉。
        order = " ORDER BY created_at DESC" if "created_at" in cols else ""
        cand = {}
        for term, kind in ts:
            like = f"%{term}%"
            for r in conn.execute(
                    f"SELECT {sel} FROM news WHERE key!=? AND status='active' AND ({where}){order} LIMIT 200",
                    (key, *([like] * len(search)))):
                d = cand.setdefault(r[0], {"key": r[0], "title": r[1] or "", "day": (r[3] or "")[:10],
                                           "n": 0, "mx": 0, "np": 0, "ng": 0, "nw": 0})
                d["n"] += 1
                d["n" + kind] += 1
                d["mx"] = max(d["mx"], len(term))
        out = list(cand.values())
        # 排序：人物命中 → 作品命中 → 最长命中词 → **日期倒序**。
        # 注意：**团体命中（ng）不参与排序**——组合通稿命中「团体名」很廉价，
        # 若当正向信号会把「乃木坂46+某人」的旧通稿顶到同人物近期稿前面。
        out.sort(key=lambda x: (-x["np"], -x["nw"], -x["mx"], -_daynum(x["day"])))
        if require_person:
            strong = [d for d in out if (d["np"] or d["ng"])]
            weak = [d for d in out if not (d["np"] or d["ng"])]
            # 只命中作品/角色名的**保留少量名额**（同事件的同剧素材只能靠作品名命中）
            out = strong[:max(0, limit - max_work_only)] + weak[:max_work_only]
        for v in out:
            v["score"] = v["np"] * 2 + v["ng"]
        return out[:limit]
    finally:
        conn.close()
