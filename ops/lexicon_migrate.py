#!/usr/bin/env python3
"""一次性迁移：旧 5 表 → lexicon（按规则自动分类 + 待审 + 导出快照）。

- 能判定的 → status=manual（人名/团体/作品/词汇/形近字/新字体）
- 判不准的 → status=pending（交 LLM judge / 人工审）
废碎片（助词等）直接丢弃。
用法：python ops/lexicon_migrate.py [--reset]
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from services import lexicon as LX  # noqa: E402

CFG = os.path.join(ROOT, "config")


def jload(name):
    try:
        return json.load(open(os.path.join(CFG, name), encoding="utf-8"))
    except Exception:                      # noqa: BLE001
        return {}


def is_pure_kana(s):
    return bool(re.fullmatch(r"[\u3040-\u309f\u30a0-\u30ffー・]+", s or ""))


def has_kanji(s):
    return bool(re.search(r"[\u4e00-\u9fff]", s or ""))


def is_latin(s):
    return bool(re.fullmatch(r"[A-Za-z0-9\-\.,'’&/ ]+", s or ""))


def classify(term, zh):
    """返回 (type, action) 或 None（判不准→pending）。"""
    t = term or ""
    if re.search(r"[《》/、·，,]", t) or len(t) >= 8:
        return ("work", "translate")
    if re.search(r"(46|48|グループ)$", t):
        return ("group", "translate")
    if is_latin(zh or ""):
        return ("work", "translate")       # 罗马字译名 → 作品/组合名
    if has_kanji(t):
        return ("person", "translate")
    return None                            # 纯假名短片 → 待审


def main():
    reset = "--reset" in sys.argv
    LX.ensure()
    if reset:
        import sqlite3
        from services import paths
        conn = sqlite3.connect(paths.sqlite_path())
        conn.execute("DELETE FROM lexicon"); conn.commit(); conn.close()
        LX.reload()

    stats = {"manual": 0, "pending": 0, "dropped": 0}

    def put(term, zh, typ, action, status="manual", note=""):
        _single_ok = typ in ("variant", "font")      # 单字形近字/新字体
        if not term or (not _single_ok and len(term) < 2) or term in LX._SKIP:
            stats["dropped"] += 1
            return
        if LX.add(term, zh=zh, type=typ, action=action, status=status, source="migrate", note=note):
            stats[status if status in stats else "manual"] += 1

    # 1) kana_replace（先入，后面权威表可覆盖）
    for term, zh in jload("kana_replace.json").items():
        if str(term).startswith("_"):
            continue
        c = classify(term, zh)
        if c:
            put(term, zh, c[0], c[1])
        else:
            put(term, "", "word", "translate", status="pending", note="auto:待审")

    # 2) artist_name_map（权威人名/团体 → manual 覆盖）
    for term, zh in jload("artist_name_map.json").items():
        if str(term).startswith("_"):
            continue
        typ = "group" if re.search(r"(46|48|グループ)$", term) else "person"
        put(term, zh, typ, "translate")

    # 3) ja_localize（日式词汇）
    for term, zh in (jload("ja_localize.json").get("map") or {}).items():
        put(term, zh, "word", "translate")

    # 4) name_variants（形近字 → 正字；keep 保留）
    nv = jload("name_variants.json")
    for wrong, right in (nv.get("variants") or {}).items():
        if wrong and wrong != right:
            put(wrong, right, "variant", "translate")
    for k in (nv.get("keep") or []):
        put(k, "", "variant", "keep")

    # 5) newfont（新字体 gate：fail / name_reserved）
    nf = jload("newfont.json")
    for ch in str(nf.get("fail") or ""):
        put(ch, "", "font", "flag", note="fail")
    for ch in str(nf.get("name_reserved") or ""):
        put(ch, "", "font", "flag", note="warn")

    n = LX.export_config()
    print("迁移完成:", stats, "导出快照", n, "条")
    print("待审(pending) 例:", [r["term"] for r in LX.pending()][:20])


if __name__ == "__main__":
    main()
