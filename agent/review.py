"""完整 review skill 运行：4 层 SKILL + 当日素材 → LLM 生成完整存档（一~六）。

忠于原 skill：
- review **只产出文档**，不做任何 DB 写入（layer23 明确「禁止写入操作」）。
- 「第五节 跨时间关联」：按**实体名（中日双形）**做只读检索（等价 layer23 的 search=），
  用只读 DB 三字段 OR（title/content_ja/rewritten_title）—— API search= 不索引 content_ja 日文正文。
- 「第4层」先跑 layer4_aggregate.py 产出发布数据底稿喂 LLM，再**仅追加**更新 data-feedback-patterns.md。
- 收尾跑 validate-tables（表格铁律）。
"""
import json
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

ENT_SYS = ("你是日娱实体抽取器。给每条素材(title=中文标题, ja=日文原标题)，抽取**用于检索的实体**："
           "人物名/团体名/作品名/系列名。**每个实体同时给出「日语汉字（或假名）」与「简体中文」两种写法**"
           "（两者相同则只给一个）——因为本库历史 title/rewritten_title 以简体中文为主、content_ja 为日文，需中日双查。"
           "不要把“古民家/剪去长发/唱的是/移籍/写真/演唱会/评论区/自拍”等描述性词当实体。"
           '只输出严格 JSON：{"<key前12位>": ["实体1","实体1简体", ...]}。')

__all__ = ["compact_rows", "prepare_package", "extract_entities", "run", "main"]


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
            d["body"] = _body(r.get("content_ja"), 2500)
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
    """取输末 ```json {...}``` → (grades, clusters, feedback, 去块文本)。"""
    if not text:
        return None, None, None, text
    for mm in reversed(list(re.finditer(r"```json\s*", text))):
        start = mm.end()
        end = text.find("```", start)
        if end < 0:
            continue
        d = _loads_balanced(text[start:end].strip())
        if isinstance(d, dict) and ("grades" in d or "clusters" in d or "feedback" in d):
            fb = d.get("feedback")
            return (d.get("grades") or {}), (d.get("clusters") or []), fb, text[:mm.start()] + text[end + 3:]
    return None, None, None, text


def persist(date, grades, clusters):
    """把 review 的**结构化共享数据**写库（只写当日行）：grade + cluster_keys（聚类计划，互指）。"""
    day = {r["key"] for r in _news.query_news(date_from=date, date_to=date, status="active", limit=500)}
    wg = wr = cleared = 0
    for k12, g in (grades or {}).items():
        fk = _resolve_keys([k12])
        if fk and fk[0] in day:
            _news.update_news(fk[0], {"grade": str(g)})
            wg += 1
    # 聚类计划 → cluster_keys（**不是** related_keys；related_keys 由 write 阶段写）
    norm = {}
    for grp in (clusters or []):
        fks = [x for x in _resolve_keys(list(grp)) if x in day]
        for x in fks:
            norm[x] = ",".join(sorted(y for y in fks if y != x))
    for k, v in norm.items():
        _news.update_news(k, {"cluster_keys": v})
        wr += 1
    for k in day:
        if k not in norm and (_news.get_by_key(k) or {}).get("cluster_keys"):
            _news.update_news(k, {"cluster_keys": ""})
            cleared += 1
    return wg, wr, cleared


def extract_entities(items, max_tokens=4000):
    """items:[{key,title,ja}] → {key12: [实体,...]}（LLM 抽取，中日双形）。"""
    if not items:
        return {}
    try:
        raw = llm.chat([{"role": "system", "content": ENT_SYS},
                        {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
                       max_tokens=max_tokens)
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else {}
    except Exception:  # noqa: BLE001
        return {}


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
    return {"rows": rows, "prev": prev, "hist": hist, "pub": pub, "fb_ctx": fb_ctx}


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
    part1 = build_archive(date, single_table=True)          # 一、单张合并表
    out = llm.chat(build_messages(date, pkg), max_tokens=max_tokens)   # 二~六 + 机器JSON
    grades, clusters, feedback, out = _extract_machine_json(out)
    rest = out
    idx = rest.find("## 二、")
    if idx > 0:
        rest = rest[idx:]
    md = part1.rstrip() + "\n\n---\n\n" + rest.lstrip()
    # 结构化共享数据入 DB（只写当日行）：grade + cluster_keys
    try:
        wg, wr, cl = persist(date, grades, clusters)
        print(f"[persist] grade {wg} 篇 / cluster {wr} 篇 / 清空 {cl} 篇（仅当日；related_keys 留给 write）")
    except Exception as e:  # noqa: BLE001
        print(f"[persist] 跳过: {e}")
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
            ok, msg = _fb.update(feedback.get("pattern"), feedback.get("trend_row"))
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
