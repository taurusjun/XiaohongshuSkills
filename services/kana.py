"""假名替换 —— 读 config/kana_replace.json（长词优先），把日文假名专名替换为中文/罗马字。"""
import json
import re
import sys
import time

from services import paths

_KANA = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")
_repl = None

__all__ = ["load", "count", "replace", "new_terms", "log_pending", "pending", "promote", "main"]

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
    if argv and argv[0] in ("pending", "promote"):
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


def _cli(array):
    if array[0] == "pending":
        for r in pending():
            print(f"{r['term']}\t{r.get('note','')}\t{r.get('ctx','')[:40]}")
        return 0
    if array[0] == "promote" and len(array) >= 3:
        n = promote(array[1], array[2]); print(f"ok, 表内 {n} 条"); return 0
    return 1

