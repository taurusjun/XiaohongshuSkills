"""完整 review skill 运行：4 层 SKILL + 当日素材 → LLM 生成完整存档（一~六）。

忠于原 skill：
- review **只产出文档**，不做任何 DB 写入（layer23 明确「禁止写入操作」）。
- 「第五节 跨时间关联」：按**实体名（中日双形）**做只读检索（等价 layer23 的 search=），
  用只读 DB 三字段 OR（title/content_ja/rewritten_title）—— API search= 不索引 content_ja 日文正文。
- 「第4层」先跑 layer4_aggregate.py 产出发布数据底稿喂 LLM，再**仅追加**更新 data-feedback-patterns.md。
- 收尾跑 validate-tables（表格铁律）。
"""
import json
import os
import re
import sys

from agent import llm
from services import news as _news, paths, related as _rel

SKILL_FILES = [
    "skills/creative/xhs-daily-material-review/SKILL.md",
    "skills/creative/xhs-daily-material-review-layer1/SKILL.md",
    "skills/xhs-daily-material-review-layer23/SKILL.md",
    "skills/xhs-daily-material-review-layer4/SKILL.md",
]

ENT_SYS = ("你是日娱实体抽取器。给每条素材(title=中文标题, ja=日文原标题)，抽取**用于检索的实体**，"
           "并**分成三类**：p=人物名（检索主轴）；g=团体名（如组合/剧团的正式名）；"
           "w=作品名/系列名/角色名（仅辅助，别混进 p/g）。"
           "**每个实体同时给出「日语汉字（或假名）」与「简体中文」两种写法**"
           "（两者相同则只给一个）——因为本库历史 title/rewritten_title 以简体中文为主、content_ja 为日文，需中日双查。"
           "不要把“古民家/剪去长发/唱的是/移籍/写真/演唱会/评论区/自拍”等描述性词当实体。"
           '只输出严格 JSON：{"<key前12位>": {"p": ["实体1","实体1简体", ...], "g": [...], "w": [...]}}。')

__all__ = ["compact_rows", "prepare_package", "extract_entities", "Entities", "run", "main"]


def _read(rel):
    p = paths.REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _body(s, cap):
    return re.sub(r"\s+", " ", (s or "")).strip()[:cap]


def compact_rows(date, limit=500, with_body=False):
    rows = _news.query_news(date_from=date, date_to=date, status="active", limit=limit)
    out = []
    for r in rows:
        d = {"key": r["key"][:12], "title": r.get("title"), "ja": (r.get("title_ja") or "")[:40],
             "fmt": r.get("format"), "lf": r.get("is_long_form"), "ts": r.get("title_score"),
             "cs": r.get("content_score"), "cj": len(r.get("content_ja") or ""),
             "src": r.get("fetch_by"), "pub": r.get("publish_xhs")}
        if with_body:
            d["body"] = _body(r.get("content_ja"), 6000)   # 覆盖全文（当日最长正文 5779）
        out.append(d)
    return out


def _resolve_keys(prefixes):
    import sqlite3
    out = []
    conn = sqlite3.connect(paths.sqlite_path())
    try:
        for pfx in prefixes or []:
            r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (str(pfx)[:40],)).fetchone()
            if r:
                out.append(r[0])
    finally:
        conn.close()
    return out


def _loads_balanced(raw):
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        pass
    i, j = raw.find("{"), raw.rfind("}")
    if i >= 0 and j > i:
        try:
            return json.loads(raw[i:j + 1])
        except Exception:  # noqa: BLE001
            return None
    return None


def _extract_machine_json(text):
    """取输末 ```json {...}``` → (machine_dict|None, 去块文本)。

    machine 可能含 grades/clusters/channel_hint/akb_type/feedback。
    """
    if not text:
        return None, text
    for mm in reversed(list(re.finditer(r"```json\s*", text))):
        start = mm.end()
        end = text.find("```", start)
        if end < 0:
            continue
        d = _loads_balanced(text[start:end].strip())
        if isinstance(d, dict) and any(k in d for k in
                                       ("grades", "clusters", "feedback", "channel_hint", "akb_type")):
            return d, text[:mm.start()] + text[end + 3:]
    return None, text


def persist(date, grades, clusters, channel_hint=None, akb_type=None, cross_time=None, advice=None,
            grade_reasons=None, date_trap=None, cluster_main=None, cluster_note=None,
            clusters_raw=None, cluster_mains=None):
    """把 review 的**结构化共享数据**写库（只写当日行）：grade + cluster_keys + channel_hint + akb_type
    + **cross_time（第3层跨时间关联）并入 related_keys**。

    ⚠️ cross_time 是 review 用 LLM 判好的跨时间关联（原 skill 第3层），**必须先落库**：
    write 阶段只做机械候选（实体 LIKE），判不出「同事件不同人/同名不同人」。
    这里写进 related_keys，write 阶段做 **union（不覆盖）**，优先级排在簇之后、机械候选之前。
    """
    day = {r["key"] for r in _news.query_news(date_from=date, date_to=date, status="active", limit=500)}
    day = {r["key"] for r in _news.query_news(date_from=date, date_to=date, status="active", limit=500)}
    wg = wr = cleared = 0
    for k12, g in (grades or {}).items():
        fk = _resolve_keys([k12])
        if fk and fk[0] in day:
            _g = (g.get("grade") if isinstance(g, dict) else g) or ""
            _news.update_news(fk[0], {"grade": str(_g).strip()})
            _rz = (grade_reasons or {}).get(k12) or ""
            if _rz:
                _news.update_news(fk[0], {"grade_reason": str(_rz)[:300]})
            wg += 1
    # 聚类计划 → cluster_keys（**不是** related_keys；related_keys 由 write 阶段写）
    norm = {}
    for grp in (clusters or []):
        fks = [x for x in _resolve_keys(list(grp)) if x in day]
        for x in fks:
            norm[x] = ",".join(sorted(y for y in fks if y != x))
    for k, v in norm.items():
        _news.update_news(k, {"cluster_keys": v})
        _m = (cluster_main or {}).get(k[:12]) or (cluster_main or {}).get(k) or ""
        _n = (cluster_note or {}).get(k[:12]) or (cluster_note or {}).get(k) or ""
        _ms = (cluster_mains or {}).get(k[:12]) or (cluster_mains or {}).get(k) or ""
        if _ms:
            _news.update_news(k, {"cluster_mains": _ms})
        _is_main = k[:12] in [x.strip() for x in _ms.split(",") if x.strip()] if _ms else False
        if _m and not _is_main:
            _fk = _resolve_keys([_m])
            if _fk and _fk[0] != k:
                # 生产约定：同事件重复条/被吸收条 → 标 merged-into-<主条key12>
                #   （生产写在 score_dims；这里另存专列 merged_into，且仅在 score_dims 为空时才写 score_dims，避免丢评分数据）
                _cur = (_news.get_by_key(k) or {})
                _upd = {"cluster_main": _fk[0], "cluster_mains": _ms, "merged_into": _fk[0][:12]}
                if not (_cur.get("score_dims") or "").strip():
                    _upd["score_dims"] = f"merged-into-{_fk[0][:12]}"
                # S/A/AKB大TOP 被吸收条：按生产口径 preselected=1（靠 merged_into 拦写稿）；其余保持 0
                _g = (_cur.get("grade") or "").strip().upper()
                if _g in ("S", "A", "AKB", "AKB大TOP"):
                    _upd["preselected"] = 1
                _news.update_news(k, _upd)
        if _n:
            _news.update_news(k, {"cluster_note": str(_n)[:200]})
        wr += 1
    for k in day:
        if k not in norm and (_news.get_by_key(k) or {}).get("cluster_keys"):
            _news.update_news(k, {"cluster_keys": ""})
            cleared += 1

    # 日期陷阱（前日重复/前日已发）：write 侧「跳过 vs 补写」要用它（原 skill :212）
    dt = 0
    for it in (date_trap or []):
        if not isinstance(it, dict):
            continue
        fk = _resolve_keys([it.get("key") or ""])
        if fk and fk[0] in day:
            _news.update_news(fk[0], {"date_trap": json.dumps(
                {"kind": (it.get("kind") or "").strip(), "note": (it.get("note") or "").strip()},
                ensure_ascii=False)})
            dt += 1

    # 第2层「价值建议」（review 已判：标题方向/写法/充实度/优先级/情绪）→ 落库，供 write 注入 prompt
    ad = 0
    for k12, v in (advice or {}).items():
        if not isinstance(v, dict) or not v:
            continue
        fk = _resolve_keys([k12])
        if fk and fk[0] in day:
            _news.update_news(fk[0], {"review_advice": json.dumps(v, ensure_ascii=False)})
            ad += 1

    # 第3层「跨时间关联」（review 已判，原 skill 3b）→ 并入 related_keys（write 阶段 union，不覆盖）
    ct = 0
    for k12, hits in (cross_time or {}).items():
        fk = _resolve_keys([k12])
        if not fk or fk[0] not in day:
            continue
        cur = [x for x in ((_news.get_by_key(fk[0]) or {}).get("related_keys") or "").split(",") if x]
        addk = []
        for h in (hits or []):
            kk = (h.get("key") if isinstance(h, dict) else h) or ""
            r = _resolve_keys([kk])
            if r and r[0] != fk[0] and r[0] not in cur and r[0] not in addk:
                addk.append(r[0])
        if addk:
            _news.update_news(fk[0], {"related_keys": ",".join(cur + addk)})
            ct += 1
    # channel_hint（gzh 方向，旧 skill 阶段2「从 review 提取 gzh 方向素材」）
    ch = 0
    ch_keys = set()
    for k12, v in (channel_hint or {}).items():
        fk = _resolve_keys([k12])
        if fk and fk[0] in day and str(v).strip():
            _news.update_news(fk[0], {"channel_hint": str(v).strip()})
            ch_keys.add(fk[0])
            ch += 1
    for k in day:
        if k not in ch_keys and (_news.get_by_key(k) or {}).get("channel_hint"):
            _news.update_news(k, {"channel_hint": ""})
    # akb_type（AKB大TOP 事件型 vs 晒照型 → 决定 write 是否写正文）
    ak = 0
    ak_keys = set()
    for k12, v in (akb_type or {}).items():
        fk = _resolve_keys([k12])
        if fk and fk[0] in day and str(v).strip() in ("event", "bullet"):
            _news.update_news(fk[0], {"akb_type": str(v).strip()})
            ak_keys.add(fk[0])
            ak += 1
    for k in day:
        if k not in ak_keys and (_news.get_by_key(k) or {}).get("akb_type"):
            _news.update_news(k, {"akb_type": ""})
    # 簇身份 + 运行历史（services/review_store）：一簇一行 + 成员行存 cluster_id + 本次运行留档
    try:
        import sqlite3 as _sq3
        from services import review_store as _rs
        _cx = _sq3.connect(paths.sqlite_path(), timeout=30)
        _ncl, _cidmap = _rs.save_clusters(_cx, date, clusters_raw, resolve_main=_resolve_keys)
        for _k12, _cidv in _cidmap.items():
            _fk = _resolve_keys([_k12])
            if _fk and _fk[0] in day:
                _news.update_news(_fk[0], {"cluster_id": _cidv})
        _rs.save_run(_cx, date, {"rows": len(day), "grade": wg, "cluster": wr, "advice": ad,
                                 "cross": ct, "date_trap": dt}, ok=1)
        _cx.close()
        print(f"[review_store] 簇 {_ncl} 行 + 运行留档 1 行")
    except Exception as _e:  # noqa: BLE001
        print(f"[review_store] 跳过: {_e}")

    return wg, wr, cleared, ch, ak, ct, ad


class Entities(dict):
    """{key12: [扁平实体,...]}（向后兼容旧调用：取值仍是扁平列表）；另存分型 {key12:{"p":[],"w":[]}}。"""

    def __init__(self, flat=None, typed=None):
        super().__init__(flat or {})
        self._typed = typed or {}

    def typed(self, k):
        """取分型实体；缺失时把扁平列表当 p（兼容旧数据/旧 prompt）。"""
        return self._typed.get(k) or {"p": list(self.get(k) or []), "g": [], "w": []}


def extract_entities(items, max_tokens=4000):
    """items:[{key,title,ja}] → Entities（{key12:[扁平实体]} + .typed(key12) 分型）。"""
    if not items:
        return Entities()
    try:
        raw = llm.chat([{"role": "system", "content": ENT_SYS},
                        {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
                       max_tokens=max_tokens, json_mode=True)
        m = re.search(r"\{.*\}", raw, re.S)
        d = json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001
        return Entities()
    flat, typed = {}, {}
    for k, v in (d or {}).items():
        if isinstance(v, dict):
            pp = [str(x).strip() for x in (v.get("p") or []) if str(x).strip()]
            ww = [str(x).strip() for x in (v.get("w") or []) if str(x).strip()]
        else:                                   # 容错：旧式扁平输出
            pp = [str(x).strip() for x in (v or []) if str(x).strip()]
            ww = []
        gg = [str(x).strip() for x in (v.get("g") or []) if str(x).strip()] if isinstance(v, dict) else []
        typed[k] = {"p": pp, "g": gg, "w": ww}
        flat[k] = list(dict.fromkeys(pp + gg + ww))
    return Entities(flat, typed)


def prepare_package(date, hist_k=6):
    """阶段1：当日全量(含正文) + 前一天(日期陷阱) + 实体名(中日双形)历史候选 + 发布数据聚合。"""
    import datetime as dt
    rows = compact_rows(date, with_body=True)
    try:
        prev = compact_rows((dt.date.fromisoformat(date) - dt.timedelta(days=1)).isoformat())
    except Exception:  # noqa: BLE001
        prev = []
    full = _news.query_news(date_from=date, date_to=date, status="active", limit=500)
    ent = extract_entities([{"key": r["key"][:12], "title": r.get("title") or "",
                             "ja": (r.get("title_ja") or "")[:60]}
                            for r in full if (r.get("title") or "").strip()])
    hist = {}
    for r in full:
        if not (r.get("title") or "").strip():
            continue
        cs = _rel.find_related(r["key"], ent.get(r["key"][:12]) or [], limit=hist_k)
        if cs:
            hist[r["key"][:12]] = [{"k": c["key"][:12], "day": c["day"], "t": (c["title"] or "")[:26]}
                                   for c in cs]
    from services import feedback_patterns as _fb
    try:
        pub = _fb.aggregate(date)
    except Exception as e:  # noqa: BLE001
        pub = f"[aggregate 失败] {e}"
    if len(pub) > 9000:
        pub = pub[-9000:]
    try:
        fb_ctx = _fb.context()
    except Exception:  # noqa: BLE001
        fb_ctx = {"last_pattern_no": 0, "tail": ""}
    return {"rows": rows, "prev": prev, "hist": hist, "pub": pub, "fb_ctx": fb_ctx, "entities": ent}


# review 的输入很大（当日全量 ~60-70k token）、输出长 → 默认 60s 贴线超时；单独放宽
_REVIEW_LLM_TO = int(os.environ.get("REVIEW_LLM_TIMEOUT", "600") or 600)


def _layer4_samples(date, n=3):
    """第4层「rewritten_title 抽检证据」：取最近已发布的 n 条（原始title → rewritten_title, mode）。"""
    import sqlite3
    from services import paths as _p
    out = []
    try:
        conn = sqlite3.connect(_p.sqlite_path())
        try:
            rows = conn.execute(
                "SELECT substr(key,1,12), COALESCE(title,''), COALESCE(rewritten_title,''), "
                "COALESCE(publish_mode,'') FROM news "
                "WHERE publish_xhs=1 AND COALESCE(xhs_pub_time,publish_time)!='' "
                "AND COALESCE(rewritten_title,'')!='' "
                "ORDER BY COALESCE(xhs_pub_time,publish_time) DESC LIMIT ?", (n,)).fetchall()
        finally:
            conn.close()
        for k, t, rw, md in rows:
            out.append({"key": k, "orig": t[:40], "rw": rw[:40], "mode": md})
    except Exception:  # noqa: BLE001
        pass
    return out


CH_SYS = ("你是渠道判定器。对每篇候选，**分别独立**判断它是否适合 xhs、是否适合 gzh（两门互不排斥），并判断是否应当**跳过**。"
          "输出 channel ∈ \"xhs|gzh|both|skip\"：两门都过 → \"both\"；只过 xhs → \"xhs\"；只过 gzh → \"gzh\"；都不过 → \"xhs\"（默认）；命中跳过判据 → \"skip\"。"
          "只输出严格 JSON：{\"<key前12位>\": {\"channel\": \"xhs|gzh|both|skip\", \"reason\": \"<一句话依据>\"}}。\n"
          "【xhs 门｜给粉丝看：有趣/有画面/有情绪】艺人日常/趣闻/穿搭/反差萌/亲子/怀旧；**争议/八卦/恋爱/爆笑短讯**/同框/私物/综艺花絮"
          "（争议型素材在小红书天然有流量；男团粉丝争议/私物/同框/综艺花絮也走 xhs）；**坂道系（乃木坂46/欅坂46/日向坂46/櫻坂46 及其成员）/女艺人（女性向）**/AKB大TOP；"
          "偶像成长/逆袭/**人物心路**（粉丝向情感共鸣，有画面感）；简单事件（一句话能说清，有事件/时间/引语）。\n"
          "【gzh 门｜给路人看：有深度/需思考】深度分析（电影/音乐/摄影/美学等专业解读）；行业分析（娱乐产业/经纪/厂牌/销量/榜单/战略）；"
          "**人物深度**长访（完整叙事弧/公共议题）；跨文化分析（日本娱乐现象→中国语境）；多人物对照/行业历史梳理；"
          "该人物/题材**近期连续0曝光 ≥3 条**（见每条『连续0曝光N条』）→ 改道深度 gzh；"
          "成人产业/风俗/AV 的**人物弧光/公共议题**（歧视/权利/职业选择；非猎奇操作）→ gzh。\n"
          "【明确 both】① **人物深度**（完整人物叙事弧/深度长访）→ \"both\"（gzh 深度稿同时出 xhs 版）；② **人物心路＋人物深度兼有** → \"both\"；③ 其他同时满足两门的。\n"
          "【skip（不写稿）】纯榜单/列表无上下文；品牌合作软文（>50%产品参数）；セクシー女優写真宣传；与日娱无关（体育/经济/政治无关）；"
          "夜店/风俗/AV 的**行业操作细节/猎奇本身**（非人物弧光）。\n"
          "【硬规则】坂道系（乃木坂/欅坂/日向坂/櫻坂…）与女艺人（女性向）→ **xhs 门必过**（由程序强制；仍判 gzh 门，gzh 门也过则为 both）。\n"
          "口诀：给粉丝看的爆料/日常/心路/争议 → xhs；给路人看的人物/产业分析 → gzh；两者都占 → both；命中 skip 判据 → skip；拿不准 → \"xhs\"（默认）。")


def _entity_pub(ents, m=8):
    """每个实体近期发布：{实体: {recent:[(日期,浏览,标题)], zero_streak, zero_in_m, n}}（仅已发布）。"""
    import sqlite3
    from services import paths as _p
    out = {}
    conn = sqlite3.connect(_p.sqlite_path())
    try:
        for e in (ents or [])[:4]:
            if not e or len(e) < 2:
                continue
            like = f"%{e}%"
            rows = conn.execute(
                "SELECT substr(COALESCE(xhs_pub_time,publish_time),1,10) d, COALESCE(xhs_views,0) v, "
                "COALESCE(rewritten_title,title) t FROM news "
                "WHERE COALESCE(publish_time,'')!='' AND (title LIKE ? OR rewritten_title LIKE ? OR content_ja LIKE ?) "
                "ORDER BY COALESCE(xhs_pub_time,publish_time) DESC LIMIT ?",
                (like, like, like, m)).fetchall()
            recent = [(r[0], int(r[1] or 0), (r[2] or "")[:22]) for r in rows]
            streak = 0
            for _d, _v, _t in recent:
                if _v == 0:
                    streak += 1
                else:
                    break
            out[e] = {"recent": recent, "zero_streak": streak,
                      "zero_in_m": sum(1 for _, v, _ in recent if v == 0), "n": len(recent)}
    finally:
        conn.close()
    return out


def judge_channels(date, pkg, grades, max_tokens=4000):
    """**独立一次 LLM**：对当日 S/A/AKB 候选判渠道，返回 {k12: gzh|both}（xhs 不列=默认）。

    输入含 Python 预取的「实体近期发布序列 + 连续0曝光/近M条0曝光计数」，供 LLM 判「改道 gzh」。
    """
    rows = pkg.get("rows") or []
    ents = pkg.get("entities") or {}
    cands = [r for r in rows
             if (grades.get(r["key"][:12]) or "").upper() in ("S", "A", "AKB", "AKB大TOP")]
    if not cands:
        return {}
    lines = []
    for r in cands:
        k = r["key"][:12]
        e = ents.get(k) or []
        pubs = _entity_pub(e)
        parts = []
        for ent, st in pubs.items():
            rtxt = "；".join(f"{d} {v}阅《{t}》" for d, v, t in st["recent"][:4]) or "无历史发布"
            parts.append(f"{ent}（近{st['n']}条中0曝光{st['zero_in_m']}条、**连续0曝光{st['zero_streak']}条**）: {rtxt}")
        pubtxt = "  ||  ".join(parts) or "无历史发布"
        lines.append(f"- [{k}] 《{r.get('title')}》 grade={grades.get(k)} 实体={'/'.join(e[:4])}"
                     f"\n    近期发布: {pubtxt}")
    try:
        raw = llm.chat([{"role": "system", "content": CH_SYS},
                        {"role": "user", "content": "当日候选：\n" + "\n".join(lines)}],
                       max_tokens=max_tokens, json_mode=True, timeout=_REVIEW_LLM_TO)
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:  # noqa: BLE001
        return {}
    from services import routing as _routing
    _txt = {r["key"][:12]: " ".join([r.get("title") or "", r.get("ja") or "",
                                     (r.get("body") or "")[:800]]) for r in cands}
    out, decisions = {}, []
    for k, v in (d.items() if isinstance(d, dict) else []):
        if isinstance(v, dict):
            ch = str(v.get("channel") or "").strip().lower()
            reason = str(v.get("reason") or "").strip()
        else:
            ch, reason = str(v).strip().lower(), ""
        if ch not in ("xhs", "gzh", "both", "skip"):
            continue
        # 硬规则：坂道系 → xhs 门必过（**不跳过 gzh 判断**；gzh 门也过 → both；skip 不覆盖）
        if ch != "skip" and _routing.force_xhs(_txt.get(k, "")):
            if ch in ("gzh", "both"):
                ch = "both"
                reason = (reason + "；坂道系硬规则→xhs 门必过").strip("；")
            else:
                ch = "xhs"
        decisions.append((k, ch, reason))
        if ch in ("gzh", "both", "skip"):   # xhs 不列=默认
            out[k] = ch
    _log_channels(date, decisions)
    return out


def _log_channels(date, decisions):
    """渠道判定留痕：打印 + 追加 logs/channel.log。"""
    import datetime as _dt
    from services import paths as _p
    try:
        log = _p.REPO_ROOT / "logs" / "channel.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        now = _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(log, "a", encoding="utf-8") as f:
            for k, ch, reason in decisions:
                print(f"[渠道] {k} → {ch}（{reason}）", flush=True)
                f.write(f"{now}\t{date}\t{k}\t{ch}\t{reason}\n")
    except Exception:  # noqa: BLE001
        pass


def build_messages(date, pkg):
    system = _read("agent/prompts/review.md")
    user = (f"日期：{date}（东京时间）。\n\n"
            f"=== 当日全量素材（JSON，字段含 body=content_ja 日文正文，请逐条阅读）===\n"
            f"{json.dumps(pkg['rows'], ensure_ascii=False)}\n\n"
            f"=== 前一天素材（JSON，用于日期陷阱；无 body）===\n{json.dumps(pkg['prev'], ensure_ascii=False)}\n\n"
            f"=== 第五节参考：实体名(中日双形)全历史候选（只读，供跨时间关联）===\n"
            f"{json.dumps(pkg['hist'], ensure_ascii=False)}\n\n"
            f"=== 第六节参考：已发布数据聚合（layer4_aggregate.py 只读输出）===\n{pkg['pub']}\n\n"
            f"=== data-feedback-patterns.md 现状：当前最大模式编号={pkg['fb_ctx']['last_pattern_no']}，"
            f"趋势表末尾如下（用于生成「新增模式」与「趋势行」，请续编号）===\n{pkg['fb_ctx']['tail']}")
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run(date, deliver=False, name=None, max_tokens=20000):
    from services.review_archive import build_archive
    pkg = prepare_package(date)
    part1 = build_archive(date, single_table=False)         # 一、按来源分组（旧 skill 1d）
    out = llm.chat(build_messages(date, pkg), max_tokens=max_tokens, json_mode=True,
                   timeout=_REVIEW_LLM_TO)   # 单个 JSON 对象（结构化数据）
    try:
        machine = json.loads(out)
    except Exception:                       # noqa: BLE001
        machine, _ = _extract_machine_json(out)     # 兜底
    machine = machine or {}
    if not isinstance(machine, dict):
        machine = {}
    if not (machine.get("grades")) and pkg.get("rows"):
        # 偶发空响应（如推理模型 content 为空）→ 重试一次，别静默评出 0 条
        print("[review] ⚠️ 主调用未产出 grades → 重试一次")
        try:
            out2 = llm.chat(build_messages(date, pkg), max_tokens=max_tokens, json_mode=True,
                            timeout=_REVIEW_LLM_TO)
            try:
                m2 = json.loads(out2)
            except Exception:                       # noqa: BLE001
                m2, _ = _extract_machine_json(out2)
            if isinstance(m2, dict) and m2.get("grades"):
                machine = m2
                print("[review] 重试成功，已取回 grades")
            else:
                print("[review] ⚠️⚠️ 主调用与重试均无 grades（本次可能评出 0 条）")
                try:
                    from services import error_log as _elog
                    _elog.log(f"review {date} 两次调用均未产出 grades")
                except Exception:  # noqa: BLE001
                    pass
        except Exception as e:                      # noqa: BLE001
            print(f"[review] 重试异常: {e}")
    _raw_grades = machine.get("grades") or {}
    grades = {k: (v.get("grade") if isinstance(v, dict) else v) for k, v in _raw_grades.items()}
    # 分级理由（原 skill 的分级表带 reason；write 侧读 review 存档时能看到 → 落库并注入）
    grade_reasons = {k: (v.get("reason") if isinstance(v, dict) else "") for k, v in _raw_grades.items()}
    _craw = [c for c in (machine.get("clusters") or []) if isinstance(c, dict) and c.get("keys")]
    clusters = [c.get("keys") for c in _craw]
    # 主条(clusters.main)与同事件说明(clusters.note)：write 的「只写主条」要用权威判据，别再推断
    cluster_main, cluster_mains, cluster_note = {}, {}, {}
    for _c in _craw:
        _ml = _c.get("mains") or ([_c.get("main")] if _c.get("main") else [])
        _ml = [str(x).strip() for x in _ml if str(x).strip()]
        _m = (_ml[0] if _ml else "").strip()
        _n = (_c.get("note") or "").strip()
        for _k in (_c.get("keys") or []):
            _k = str(_k).strip()
            if not _k:
                continue
            if _m:
                cluster_main[_k] = _m
                cluster_mains[_k] = ",".join(_ml)
            if _n:
                cluster_note[_k] = _n
    channel_hint = machine.get("channel_hint") or {}
    try:                                             # 独立一次 LLM 判渠道（覆盖主调用）
        _ch = judge_channels(date, pkg, grades)
        if _ch:
            channel_hint = _ch
    except Exception:  # noqa: BLE001
        pass
    akb_type = machine.get("akb_type") or {}
    feedback = machine.get("feedback")
    from services import review_render as _rr
    _titles = {r["key"][:12]: (r.get("title") or "") for r in pkg["rows"]}
    _samples = _layer4_samples(date)          # 第4层抽检证据（原 skill 要求）
    rest = _rr.render(machine, _titles, samples=_samples)
    md = part1.rstrip() + "\n\n---\n\n" + rest.lstrip()
    # 结构化共享数据入 DB（只写当日行）：grade + cluster_keys
    try:
        wg, wr, cl, ch, ak, ct, ad = persist(date, grades, clusters, channel_hint, akb_type,
                                         machine.get("cross_time"),
                                         machine.get("advice"),
                                         grade_reasons, machine.get("date_trap"),
                                         cluster_main, cluster_note, _craw, cluster_mains)
        print(f"[persist] grade {wg} 篇 / cluster {wr} 篇 / 清空 {cl} 篇 / gzh方向 {ch} 篇 / AKB分流 {ak} 篇 / "
                  f"跨时间关联 {ct} / 价值建议 {ad} / 日期陷阱 {dt} 篇落库；主条 {len(cluster_main or {})} 篇 / 说明 {len(cluster_note or {})} 篇")
    except Exception as e:  # noqa: BLE001
        print(f"[persist] 跳过: {e}")
    # 产出校验告警：当日有素材却评出 0 条 grade（静默失败兜底；如 LLM 空响应/超时/思考模式未关）
    try:
        _n_rows = len(pkg.get("rows") or [])
        if _n_rows and not grades:
            from scripts.feishu_bot import send_alert
            _ok = send_alert(f"⚠️ review {date} 产出异常：当日 {_n_rows} 条素材，但评出 0 条 grade"
                             f"（可能 LLM 空响应/超时/思考模式未关）。请检查。")
            print(f"[review] ⚠️ 产出校验失败（grade=0 / 素材={_n_rows}）→ 飞书告警{'已发' if _ok else '未发(未配置)'}")
        elif _n_rows and len(grades) < _n_rows * 0.5:
            print(f"[review] ⚠️ 分级覆盖率偏低：{len(grades)}/{_n_rows}")
    except Exception as e:  # noqa: BLE001
        print(f"[review] 产出校验告警失败: {e}")
    # 表格铁律：收尾校验
    try:
        from services import validate_tables as _vt
        nt, probs = _vt.validate_text(md)
        print(f"[validate-tables] tables={nt} problems={len(probs)}")
        for p in probs[:20]:
            print("   -", p)
    except Exception as e:  # noqa: BLE001
        print(f"[validate-tables] 跳过: {e}")
    # 4d：仅追加更新 data-feedback-patterns.md（模式节 + 趋势行）
    if isinstance(feedback, dict):
        try:
            from services import feedback_patterns as _fb
            ok, msg = _fb.update(feedback.get("pattern"), feedback.get("trend_row"), date=date,
                                patterns=feedback.get("patterns"))
            print(f"[feedback-patterns] {msg}")
        except Exception as e:  # noqa: BLE001
            print(f"[feedback-patterns] 跳过: {e}")
    else:
        print("[feedback-patterns] 跳过: LLM 未给 feedback")
    # 本地存档（人看 / 飞书）：**无条件**
    from services.delivery import web_save
    web_save(md, name=name or f"{date}-review-full.md")
    if deliver:                                          # 推送：可选
        from services.delivery import deliver as _d
        print("[delivery]", _d(md, name=name or f"{date}-review-full.md"))
    else:
        print(md)
    try:
        from agent import llm as _llm
        print(_llm.format_usage("review "), flush=True)
        from services import token_usage as _tu
        _tu.record("review", "run", "", _llm.usage())
    except Exception:  # noqa: BLE001
        pass
    return md


def main(argv=None):
    import argparse
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", required=True)
    ap.add_argument("--deliver", action="store_true")
    ap.add_argument("--name", default=None)
    a = ap.parse_args(argv)
    run(a.date, a.deliver, a.name)
    return 0
