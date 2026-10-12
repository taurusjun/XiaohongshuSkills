"""术语表（lexicon）—— 日→中专有名词**整体**映射（translate / keep）。

真源 = DB（news_dev.db 的 lexicon / lexicon_audit）。config/lexicon/manual/*.json 仅作 seed/export。
单元 = 专有名词整体（人名/团体/作品/节目/曲名/舞台名）；keep = 不可译（拟声/neta）原样保留。
**注入只取本文命中的术语，绝不全量塞进上下文。**
"""
import datetime
import json
import re
import sqlite3
from contextlib import contextmanager

from services import paths

__all__ = ["ensure", "inject", "replace", "normalize", "brackets", "keep_spans", "discover", "discover_names", "looks_term", "residual_terms",
           "add", "update", "delete", "get", "all", "pending", "judge", "promote", "revert",
           "seed_from_config", "export_config", "reload", "main", "TYPES"]

TYPES = ("person", "group", "work", "word", "variant", "font", "keep")
_MANUAL_DIR = "config/lexicon/manual"
_TYPES_IN_MANUAL = ("names", "works", "words", "variants", "fonts", "keep")

_SKIP = {"から", "まで", "より", "など", "こと", "もの", "あり", "なし", "ざる", "ごと",
         "ので", "のに", "けど", "です", "ます", "って", "いる", "ある", "する"}


# ── DB ─────────────────────────────────────────────────────────────
@contextmanager
def _db():
    conn = sqlite3.connect(paths.sqlite_path())
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


_L_DDL = """CREATE TABLE IF NOT EXISTS lexicon (
  term TEXT PRIMARY KEY, type TEXT DEFAULT '', action TEXT DEFAULT 'translate',
  zh TEXT DEFAULT '', aliases TEXT DEFAULT '', status TEXT DEFAULT 'pending',
  source TEXT DEFAULT '', src_key TEXT DEFAULT '', note TEXT DEFAULT '',
  created_at TEXT DEFAULT '', updated_at TEXT DEFAULT '')"""
_A_DDL = """CREATE TABLE IF NOT EXISTS lexicon_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT, term TEXT, old_json TEXT DEFAULT '',
  new_json TEXT DEFAULT '', actor TEXT DEFAULT '', reason TEXT DEFAULT '', ts TEXT DEFAULT '')"""


def ensure():
    with _db() as c:
        c.execute(_L_DDL)
        c.execute(_A_DDL)
        c.execute("CREATE INDEX IF NOT EXISTS idx_lexicon_status ON lexicon(status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_lexicon_type ON lexicon(type)")


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _audit(c, term, old, new, actor, reason):
    try:
        c.execute("INSERT INTO lexicon_audit (term, old_json, new_json, actor, reason, ts) "
                  "VALUES (?,?,?,?,?,?)",
                  (term, json.dumps(old, ensure_ascii=False) if old else "",
                   json.dumps(new, ensure_ascii=False) if new else "", actor, reason, _now()))
    except Exception:  # noqa: BLE001
        pass


# ── in-memory index (manual+auto) ──────────────────────────────────
_index = None


def reload():
    global _index
    _index = None


def _rows_active():
    ensure()
    with _db() as c:
        return [dict(r) for r in c.execute(
            "SELECT * FROM lexicon WHERE status IN ('manual','auto')").fetchall()]


def _build():
    global _index
    if _index is not None:
        return _index
    rows = _rows_active()
    alias = {}
    for r in rows:
        for a in (r.get("aliases") or "").split(","):
            a = a.strip()
            if a:
                alias[a] = r
    zhvals = {(r.get("zh") or "").strip() for r in rows if r.get("zh")}
    _index = {"ordered": sorted(rows, key=lambda r: -len(r["term"] or "")),
              "by_term": {r["term"]: r for r in rows}, "alias": alias, "zh": zhvals}
    return _index


# ── 读取/注入 ──────────────────────────────────────────────────────
def inject(title_ja, body_ja, limit=None):
    """返回**本文命中**的术语条目（长词优先，去重）。用于写稿前置注入，绝不全量。"""
    idx = _build()
    text = (title_ja or "") + "\n" + (body_ja or "")
    out, seen = [], set()
    for r in idx["ordered"]:
        t = r["term"]
        if t and t in text and t not in seen:
            out.append(r)
            seen.add(t)
    for a, r in idx["alias"].items():
        if a in text and r["term"] not in seen:
            out.append(r)
            seen.add(r["term"])
    return out[:limit] if limit else out


def replace(text):
    """事后兜底：只替换 action=translate；keep 原样不动。长词优先。"""
    idx = _build()
    out = text or ""
    for r in idx["ordered"]:
        if (r.get("action") or "translate") == "translate" and r.get("zh"):
            out = out.replace(r["term"], r["zh"])
    out = out.replace("\u30fb", "\u00b7")
    out = re.sub(r"《《([^》]*)》》", r"《\1》", out)
    return out


def keep_spans(text):
    """正文里出现的 keep 词（供假名门禁扣除）。"""
    idx = _build()
    t = text or ""
    return [r["term"] for r in idx["ordered"] if r.get("action") == "keep" and r["term"] in t]


_BADP = re.compile(r"[。！？!?…;；:：,，\.\(\)\[\]【】]")
_BADEND = re.compile(r"(する|した|してる|している|です|ます|ません|ください|としたら|行くぞ|だよ|だね|かな|だろう|いません)$")


def looks_term(n):
    """像专名/作品名（非短语/整句）。"""
    if not n or not (2 <= len(n) <= 16):
        return False
    if not _KANA.search(n):
        return False
    if _BADP.search(n) or _BADEND.search(n):
        return False
    return True


def discover(text, src_key="", from_="write"):
    """从文本发现候选专名（括号整体；未在表者）→ pending。返回新增 term。"""
    idx = _build()
    known = set(idx["by_term"]) | set(idx["alias"])
    out = []
    for inner in brackets(text or ""):
        if inner in known or len(inner) < 2:
            continue
        if not looks_term(inner):           # 像专名（非短语/整句）
            continue
        if add(inner, status="pending", source=from_, src_key=src_key, note="auto:发现"):
            out.append(inner)
    return out


def discover_names(names, src_key="", from_="review"):
    """把实体名列表里未在表的 → pending（hint person）。"""
    idx = _build()
    known = set(idx["by_term"]) | set(idx["alias"]) | idx["zh"]
    out = []
    for n in (names or []):
        n = (n or "").strip()
        if n in known or n in _SKIP or not looks_term(n):
            continue                                             # 像专名（含假名、非短语）
        if add(n, status="pending", source=from_, src_key=src_key, note="auto:实体"):
            out.append(n)
    return out


def residual_terms(text):
    """正文里剩余的假名片段（≥2，已扣除 keep 保留词）。供改稿反馈。"""
    idx = _build()
    t = text or ""
    for r in idx["ordered"]:
        if r.get("action") == "keep":
            t = t.replace(r["term"], "")
    return list(dict.fromkeys(re.findall(r"[\u3040-\u309f\u30a0-\u30ff]{2,}", t)))


def normalize(names):
    """把实体名归一到标准译名：{原名: 标准中文}（未命中保留原样）。"""
    idx = _build()
    out = {}
    for n in (names or []):
        n = (n or "").strip()
        if not n:
            continue
        r = idx["by_term"].get(n) or idx["alias"].get(n)
        out[n] = (r.get("zh") or n) if r and (r.get("action") or "translate") == "translate" else n
    return out


_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_BRACKET = re.compile(r"[《【]([^》】\n]{1,60})[》】]")


def brackets(text):
    """括号内的候选专名（作品/节目）；含假名或较长者视为候选。"""
    out = []
    for m in _BRACKET.finditer(text or ""):
        inner = m.group(1).strip()
        if inner and inner not in out:
            out.append(inner)
    return out


# ── 写（UI / 自增长）────────────────────────────────────────────────
def add(term, zh=None, type="word", action="translate", aliases="", status="auto",
        source="", src_key="", note=""):
    """新增/更新一条。manual 不被 auto 覆盖。返回是否写入。"""
    term = (term or "").strip()
    if not term:
        return False
    if action == "translate" and zh and zh.strip() == term:   # 恒等翻译 = 保留
        action, zh = "keep", ""
    ensure()
    with _db() as c:
        row = c.execute("SELECT * FROM lexicon WHERE term=?", (term,)).fetchone()
        old = dict(row) if row else None
        if old and old.get("status") == "manual" and status != "manual":
            return False                       # 不降级人工条目
        now = _now()
        if old:
            c.execute("UPDATE lexicon SET type=?, action=?, zh=?, aliases=?, status=?, source=?, "
                      "src_key=?, note=?, updated_at=? WHERE term=?",
                      (type, action, zh or "", aliases, status, source, src_key, note, now, term))
        else:
            c.execute("INSERT INTO lexicon (term,type,action,zh,aliases,status,source,src_key,note,"
                      "created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                      (term, type, action, zh or "", aliases, status, source, src_key, note, now, now))
        _audit(c, term, old, {"term": term, "zh": zh, "type": type, "action": action,
                              "status": status}, source or "ui", "add")
    reload()
    return True


def update(term, **fields):
    ensure()
    ok = False
    with _db() as c:
        row = c.execute("SELECT * FROM lexicon WHERE term=?", (term,)).fetchone()
        if not row:
            return False
        old = dict(row)
        cols = {k: v for k, v in fields.items()
                if k in ("type", "action", "zh", "aliases", "status", "note", "src_key", "source")}
        if not cols:
            return False
        cols["updated_at"] = _now()
        c.execute(f"UPDATE lexicon SET {', '.join(k+'=?' for k in cols)} WHERE term=?",
                  list(cols.values()) + [term])
        _audit(c, term, old, {**old, **cols}, "ui", "update")
        ok = True
    reload()
    return ok


def delete(term, actor="ui"):
    ensure()
    with _db() as c:
        row = c.execute("SELECT * FROM lexicon WHERE term=?", (term,)).fetchone()
        if not row:
            return False
        c.execute("DELETE FROM lexicon WHERE term=?", (term,))
        _audit(c, term, dict(row), None, actor, "delete")
    reload()
    return True


def get(term):
    ensure()
    with _db() as c:
        r = c.execute("SELECT * FROM lexicon WHERE term=?", (term,)).fetchone()
    return dict(r) if r else None


_SORT_COLS = {"term", "type", "status", "action", "zh", "created_at", "updated_at", "source"}


def all(type=None, status=None, q="", sort_by="updated_at", sort_dir="desc"):
    ensure()
    sql, args = "SELECT * FROM lexicon WHERE 1=1", []
    if type:
        sql += " AND type=?"; args.append(type)
    if status:
        sql += " AND status=?"; args.append(status)
    if q:
        sql += " AND (term LIKE ? OR zh LIKE ? OR aliases LIKE ?)"; args += [f"%{q}%"] * 3
    sb = sort_by if sort_by in _SORT_COLS else "updated_at"
    d = "ASC" if str(sort_dir).lower() == "asc" else "DESC"
    sql += f" ORDER BY {sb} {d}, term ASC"
    with _db() as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def pending():
    return all(status="pending")


# ── 自增长：LLM 判定 ───────────────────────────────────────────────
def judge(limit=40, dry_run=False, chunk=40):
    """对 pending 候选做 LLM 判定（分批）；通过护栏后写 auto（**即生效**）+ 审计。"""
    rows = pending()[:limit]
    if not rows:
        return []
    terms = [r["term"] for r in rows]
    out = []
    for i in range(0, len(terms), chunk):
        part = terms[i:i + chunk]
        try:
            from agent import llm
            usr = ("对下列日娱词条判断并只输出严格 JSON：{\"<原文>\": {\"type\": \"person|group|work|word|keep\", "
                   "\"action\": \"translate|keep\", \"zh\": \"<中译，keep 时留空>\"}}\n"
                   "person=人名 group=团体 work=作品/剧/曲/节目/舞台 word=日式词汇 keep=拟声词或neta等不可译(原样保留)\n"
                   + "\n".join(f"- {t}" for t in part))
            raw = llm.chat([{"role": "system", "content": "你是日娱专有名词整理助手，只输出 JSON。"},
                            {"role": "user", "content": usr}], max_tokens=4000, json_mode=True)
            d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception:  # noqa: BLE001
            continue
        for t in part:
            v = d.get(t)
            if not isinstance(v, dict):
                continue
            ty = (v.get("type") or "word").strip()
            act = (v.get("action") or "translate").strip()
            zh = (v.get("zh") or "").strip()
            if len(t) < 2 or t in _SKIP:
                continue
            if act == "keep":
                zh = ""
            elif not zh or zh == t:
                continue
            if not dry_run:
                add(t, zh=zh, type=ty if ty in TYPES else "word", action=act or "translate",
                    status="auto", source="auto")
            out.append({"term": t, "type": ty, "action": act, "zh": zh})
    return out


def promote(term):
    """人工保留：auto/pending → manual。"""
    return update(term, status="manual")


def revert(term):
    """人工撤回：标 rejected（留档，不参与生效）。"""
    return update(term, status="rejected")


# ── seed / export ──────────────────────────────────────────────────
def seed_from_config():
    """表空时从 config/lexicon/manual/*.json 导入（seed）。返回导入条数。"""
    ensure()
    with _db() as c:
        n = c.execute("SELECT COUNT(*) FROM lexicon").fetchone()[0]
    if n:
        return 0
    base = paths.REPO_ROOT / _MANUAL_DIR
    if not base.exists():
        return 0
    cnt = 0
    for typ in _TYPES_IN_MANUAL:
        f = base / f"{typ}.json"
        if not f.exists():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for term, spec in (data or {}).items():
            if str(term).startswith("_"):
                continue
            if isinstance(spec, str):
                spec = {"zh": spec}
            action = "keep" if (spec.get("action") == "keep" or typ == "keep") else "translate"
            if add(term, zh=spec.get("zh"), type=spec.get("type") or _type_of(typ),
                   action=action, aliases=",".join(spec.get("aliases") or []),
                   status="manual", source="seed"):
                cnt += 1
    return cnt


def _type_of(fname):
    return {"names": "person", "works": "work", "words": "word",
            "variants": "variant", "fonts": "font", "keep": "keep"}.get(fname, "word")


def export_config():
    """导出为 config/lexicon/manual/*.json（进 git 的快照，按 type 归文件）。"""
    ensure()
    base = paths.REPO_ROOT / _MANUAL_DIR
    base.mkdir(parents=True, exist_ok=True)
    buckets = {t: {} for t in _TYPES_IN_MANUAL}
    tmap = {"person": "names", "group": "names", "work": "works", "word": "words",
            "variant": "variants", "font": "fonts", "keep": "keep"}
    with _db() as c:
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM lexicon WHERE status IN ('manual','auto')").fetchall()]
    for r in rows:
        b = tmap.get(r["type"], "words")
        spec = {}
        if r.get("zh"):
            spec["zh"] = r["zh"]
        if r.get("action") == "keep":
            spec["action"] = "keep"
        if r.get("type") and r["type"] != {"names": "person"}.get(b):
            spec["type"] = r["type"]
        if r.get("aliases"):
            spec["aliases"] = [a for a in r["aliases"].split(",") if a]
        if r.get("status") == "auto":
            spec["source"] = "auto"
        buckets[b][r["term"]] = spec if spec else r.get("zh") or ""
    n = 0
    for typ, data in buckets.items():
        (base / f"{typ}.json").write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        n += len(data)
    return n


# ── CLI ────────────────────────────────────────────────────────────
def main(argv=None):
    a = list(argv) if argv is not None else []
    if not a:
        print("用法: lexicon [seed|export|list|pending|judge|add|revert]")
        return 0
    cmd = a[0]
    if cmd == "seed":
        print(f"seed {seed_from_config()} 条")
    elif cmd == "export":
        print(f"export {export_config()} 条")
    elif cmd == "list":
        for r in all(status=a[1] if len(a) > 1 else None):
            print(f"{r['term']}\t{r['zh']}\t{r['type']}\t{r['action']}\t{r['status']}")
    elif cmd == "pending":
        for r in pending():
            print(f"{r['term']}\t{r.get('note','')[:40]}")
    elif cmd == "judge":
        for d in judge(dry_run="--dry-run" in a):
            print(f"  {d['term']} → {d['zh'] or '（保留）'}  [{d['type']}/{d['action']}]")
    elif cmd == "add" and len(a) >= 3:
        print("ok" if add(a[1], zh=a[2], status="manual", source="ui") else "skip")
    elif cmd == "revert" and len(a) >= 2:
        print("ok" if revert(a[1]) else "未找到")
    else:
        print("未知命令")
        return 2
    return 0
