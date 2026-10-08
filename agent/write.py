"""完整写稿 skill 运行：write-publish-flow SKILL + 候选素材 → LLM 产稿 → precheck → 入库。

用法: python -m cli write-full [--n 3] [--deliver] [--dry-run]
"""
import json
import re
import sys

from agent import llm
from services import news as _news, paths, precheck as _pc

SKILL_FILE = "skills/creative/xhs-write-publish-flow/SKILL.md"
SYS = ("你是小红书日娱写稿助手。只输出严格 JSON 数组：[{\"key\": <40位key>, "
       "\"title\": \"...\", \"body\": \"...\"}]，不要任何多余文字。")

__all__ = ["pick_candidates", "build_user", "run_one", "run", "main"]


def _read(rel):
    p = paths.REPO_ROOT / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def pick_candidates(n=3):
    rows = _news.query_news(status="active", limit=120)
    cand = [r for r in rows if (r.get("content_ja") or "")
            and r.get("format") in ("story", "news")
            and not (r.get("rewritten_content") or "")]
    cand.sort(key=lambda r: -(r.get("title_score") or 0))
    return cand[:n]


def build_user(cands):
    items = [{"key": r["key"], "title": r.get("title"), "fmt": r.get("format"),
              "lf": r.get("is_long_form"), "content_ja": (r.get("content_ja") or "")[:3500]}
             for r in cands]
    return ("为下列每一条素材写一篇小红书稿，输出 JSON 数组（保持 key 不变）。硬规则：\n"
            "- 标题 ≤20 字；正文全中文（假名≤5，人名/作品名给中文写法）\n"
            "- story 且 lf=1 → ≥850 字且至少 2 行以 '## ' 开头；其余 ≤900 字且无 '## '\n"
            "- 行内「、」≤1；正文/原文字数 ≥30%\n\n"
            "素材 JSON：\n" + json.dumps(items, ensure_ascii=False))


def run_one(cand, drafts_by_key, retry_ctx=None, max_tokens=16000):
    """返回 (title, body, precheck结果)。retry_ctx 为上一轮问题。"""
    msgs = [{"role": "system", "content": SYS + "\n\n=== SKILL ===\n" + _read(SKILL_FILE)}]
    msgs.append({"role": "user", "content": build_user([cand])})
    if retry_ctx:
        msgs.append({"role": "user", "content": retry_ctx})
    raw = llm.chat(msgs, max_tokens=max_tokens)
    arr = json.loads(re.search(r"\[.*\]", raw, re.S).group(0))
    d = arr[0]
    title, body = d.get("title", ""), d.get("body", "")
    spec = {"fmt": cand.get("format"), "lf": cand.get("is_long_form"),
            "ja": len(cand.get("content_ja") or "")}
    res = _pc.check_text(f"## {title}\n{body}", spec)
    return title, body, res


def run(n=3, deliver=False, dry_run=False):
    cands = pick_candidates(n)
    results = []
    for c in cands:
        title, body, res = run_one(c, None)
        attempts = 1
        while res["problems"] and attempts < 3:            # 改稿重试
            ctx = "上一版未过门禁，请修正后只输出 JSON 数组：\n- " + "\n- ".join(res["problems"])
            title, body, res = run_one(c, None, retry_ctx=ctx)
            attempts += 1
        ok = not res["problems"]
        if ok and not dry_run:
            _news.update_news(c["key"], {"rewritten_title": title, "rewritten_content": body,
                                         "publish_mode": "rewritten", "preselected": 1, "publish_xhs": 0})
        results.append({"key": c["key"][:12], "ok": ok, "attempts": attempts,
                        "title": title, "text": body, "body": res["body_len"],
                        "h2": res["h2"], "kana": res["kana"], "problems": res["problems"]})
        print(f"{'PASS' if ok else 'FAIL'} {c['key'][:12]} attempts={attempts} "
              f"body={res['body_len']} ##={res['h2']} kana={res['kana']}"
              + ("" if ok else " | " + "; ".join(res["problems"])))
    passed = sum(1 for r in results if r["ok"])
    print(f"\n写稿门禁通过 {passed}/{len(results)}（{'dry-run，未入库' if dry_run else '已入库'}）")
    if deliver:
        from services.delivery import deliver as _d
        lines = [f"# 写稿结果 —（{passed}/{len(results)} 通过）", ""]
        for r in results:
            lines.append(f"## {r['title']}")
            lines.append(f"`{r['key']}`  {'✅ 已入库' if r['ok'] else '❌ 未过门禁'}"
                         f"（attempts={r['attempts']}，正文{r['body']}字）")
            lines.append("")
            if r["ok"]:
                lines.append(r["text"])
            else:
                lines.append("未过门禁：\n- " + "\n- ".join(r["problems"]))
            lines.append("")
            lines.append("---")
            lines.append("")
        print("[delivery]", _d("\n".join(lines), name="write-result.md"))
    return results


def main(argv=None):
    import argparse
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--deliver", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    run(a.n, a.deliver, a.dry_run)
    return 0
