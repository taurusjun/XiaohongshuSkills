"""skills 知识库访问（references 按需检索，避免全塞 prompt → 覆盖不遗漏）。"""
import os
import sys

from services import paths

__all__ = ["roots", "list_refs", "read_ref", "search_refs", "relevant", "main"]

SKILL_DIRS = [
    "skills/creative/xhs-write-publish-flow",
    "skills/creative/xhs-daily-material-review",
    "skills/creative/xhs-content-review",
    "skills/social-media/xhs-publish-workflow",
    "skills/writing/xhs-review-self-audit",
    "skills/xhs-daily-material-review-layer23",
    "skills/xhs-daily-material-review-layer4",
]


def roots():
    out = []
    for d in SKILL_DIRS:
        r = paths.REPO_ROOT / d / "references"
        if r.exists():
            out.append((d, r))
    return out


def list_refs(skill=None):
    files = []
    for d, r in roots():
        if skill and skill not in d:
            continue
        files += [f"{d}/references/{p.name}" for p in sorted(r.glob("*.md"))]
    return files


def read_ref(name):
    for d, r in roots():
        p = r / os.path.basename(name)
        if p.exists():
            return p.read_text(encoding="utf-8")
    return ""


def search_refs(query, limit=12):
    import re
    hits = []
    rx = re.compile(re.escape(query), re.I)
    for d, r in roots():
        for p in sorted(r.glob("*.md")):
            try:
                for i, ln in enumerate(p.read_text(encoding="utf-8").split("\n"), 1):
                    if rx.search(ln):
                        hits.append({"ref": f"{d}/references/{p.name}", "line": i, "text": ln.strip()[:160]})
                        if len(hits) >= limit:
                            return hits
            except Exception:
                pass
    return hits


def relevant(query, k=3, chars=700):
    """给一段文本（标题/关键词）→ 相关 references 摘录（RAG-lite），供 agent 按需参考。"""
    import re
    terms = [t for t in re.split(r"[\s，。/]+", query or "") if len(t) >= 2][:3]
    hits, seen, out = [], [], []
    for t in terms:
        hits += search_refs(t, limit=30)
    for h in hits:
        if h["ref"] in seen:
            continue
        seen.append(h["ref"])
        out.append(f"[{h['ref']}]\n" + read_ref(h["ref"])[:chars])
        if len(out) >= k:
            break
    return "\n\n".join(out)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] == "list":
        print("\n".join(list_refs(argv[1] if len(argv) > 1 else None))); return 0
    if argv[0] == "read" and len(argv) > 1:
        print(read_ref(argv[1])); return 0
    if argv[0] == "search" and len(argv) > 1:
        for h in search_refs(argv[1]):
            print(f"{h['ref']}:{h['line']}  {h['text']}")
        return 0
    print("用法: references list|read <file>|search <q>"); return 1
