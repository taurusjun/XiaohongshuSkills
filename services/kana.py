"""假名替换 —— 读 config/kana_replace.json（长词优先），把日文假名专名替换为中文/罗马字。"""
import json
import re
import sys
import time

from services import paths

_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_repl = None

__all__ = ["load", "count", "replace", "new_terms", "log_pending", "pending", "promote",
           "auto_promote", "main"]

PENDING = "data/kana_pending.jsonl"


def load():
    global _repl
    if _repl is None:
        p = paths.REPO_ROOT / "config" / "kana_replace.json"
        data = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        _repl = sorted(data.items(), key=lambda kv: -len(kv[0]))
    return _repl


def count(text: str) -> int:
    return len(_KANA.findall(text or ""))


def replace(text: str) -> str:
    for orig, repl in load():
        text = text.replace(orig, repl)
    # 统一中文间隔号，清双重书名号
    text = text.replace("\u30fb", "\u00b7")
    text = re.sub(r"《《([^》]*)》》", r"《\1》", text)
    return text


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in ("pending", "promote", "autopromote"):
        return _cli(argv)
    if not argv:
        print(f"词条数: {len(load())}"); return 0
    text = argv[0]
    out = replace(text)
    print(f"kana: {count(text)} -> {count(out)}")
    print(out)
    return 0

def new_terms(text: str):
    """抽出正文里仍存在、且不在替换表中的假名专名（连续假名>=2，去重）。"""
    keys = [k for k, _ in load()]
    out = []
    for tok in re.findall(r"[\u3040-\u309f\u30a0-\u30ff]{2,}", text or ""):
        if any(tok in k for k in keys):
            continue
        if tok not in out:
            out.append(tok)
    for m in re.finditer(r"[《「]([^》」\n]{1,40})[》」]", text or ""):
        inner = m.group(1).strip()
        if not _KANA.search(inner):
            continue
        if any(inner in k for k in keys) or inner in out:
            continue
        out.append(inner)
    return out


def _pending_path():
    return paths.REPO_ROOT / PENDING


def log_pending(text: str, note: str = ""):
    """写稿时把新抓到的假名专名追加到候选文件（去重，供人工审核）。"""
    terms = new_terms(text)
    if not terms:
        return []
    p = _pending_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(ln)["term"])
            except Exception:
                pass
    added = []
    with open(p, "a", encoding="utf-8") as f:
        for t in terms:
            if t in seen:
                continue
            mol = re.search(rf"[^。！？\n]{{0,20}}{re.escape(t)}[^。！？\n]{{0,20}}", text or "")
            f.write(json.dumps({"term": t, "note": note, "ctx": (mol.group(0) if mol else "")},
                               ensure_ascii=False) + "\n")
            seen.add(t); added.append(t)
    return added


def pending():
    p = _pending_path()
    if not p.exists():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(ln))
        except Exception:
            pass
    return rows


def promote(term: str, zh: str):
    """人工审核：把 term->zh 写入 config/kana_replace.json，并从候选移除。"""
    cfg = paths.REPO_ROOT / "config" / "kana_replace.json"
    data = json.loads(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}
    data[term] = zh
    cfg.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    p = _pending_path()
    if p.exists():
        keep = [ln for ln in p.read_text(encoding="utf-8").splitlines()
                if (json.loads(ln).get("term") != term)]
        p.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    global _repl
    _repl = None                    # 失效缓存
    return len(data)


def auto_promote(limit=40, dry_run=False):
    """把 pending 里的假名用 LLM 批量中译并**自动写入字典**（先自动、人工后审）。

    返回 [(term, zh)]；审计写入 data/kana_autopromoted.jsonl 供后续复核。
    """
    rows = pending()
    terms = list(dict.fromkeys(r["term"] for r in rows if r.get("term")))[:limit]
    if not terms:
        return []
    ctx = {r["term"]: r.get("ctx", "") for r in rows}
    try:
        from agent import llm
        usr = ("把下列日文假名专名译成中文：人名按日本娱乐圈通用译法，作品/节目/曲名译成中文，"
               "实在无法译的用罗马字。只输出严格 JSON：{\"<原文>\": \"<中译>\"}。\n"
               + "\n".join(f"- {t}   （语境：{ctx.get(t, '')[:50]}）" for t in terms))
        raw = llm.chat([{"role": "system", "content": "你是日娱名词翻译，只输出 JSON。"},
                        {"role": "user", "content": usr}], max_tokens=2000)
        d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
    except Exception:  # noqa: BLE001
        return []
    audit = _pending_path().parent / "kana_autopromoted.jsonl"
    out = []
    for t, zh in (d.items() if isinstance(d, dict) else []):
        if not isinstance(zh, str) or not zh.strip() or zh.strip() == t:
            continue
        t, zh = t.strip(), zh.strip()
        if not dry_run:
            promote(t, zh)
        out.append((t, zh))
        try:
            with open(audit, "a", encoding="utf-8") as f:
                f.write(json.dumps({"term": t, "zh": zh, "ctx": ctx.get(t, ""),
                                    "ts": time.strftime("%Y-%m-%d %H:%M:%S")},
                                   ensure_ascii=False) + "\n")
        except Exception:  # noqa: BLE001
            pass
    return out


def _cli(array):
    if array[0] == "pending":
        for r in pending():
            print(f"{r['term']}\t{r.get('note','')}\t{r.get('ctx','')[:40]}")
        return 0
    if array[0] == "promote" and len(array) >= 3:
        n = promote(array[1], array[2]); print(f"ok, 表内 {n} 条"); return 0
    if array[0] == "autopromote":
        dry = "--dry-run" in array
        res = auto_promote(dry_run=dry)
        for t, zh in res:
            print(f"  {t} → {zh}")
        print(f"auto-promote {len(res)} 条（{'dry-run' if dry else '已写入字典'}）"); return 0
    return 1

