"""L6：写稿机械门禁评测（真实 LLM → services.precheck 硬断言）。

用法: .venv/bin/python eval/l6_write_eval.py [N]
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from agent import llm  # noqa: E402
from services import news as _news, precheck as _pc  # noqa: E402

SYS = "你是小红书日娱写稿助手。只输出严格 JSON：{\"title\": \"...\", \"body\": \"...\"}，不要任何多余文字。"


def build_user(row):
    return (
        "根据下面日文素材写一篇小红书稿。\n"
        f"素材(日文)：\n{(row.get('content_ja') or '')[:3500]}\n\n"
        "硬规则：\n"
        "- 标题 ≤20 字；正文全中文（日文假名总数 ≤5，人名/作品名给中文写法）\n"
        f"- format={row.get('format')} is_long_form={row.get('is_long_form')}："
        "story且lf=1 → 正文 ≥850 字且至少 2 行以 '## ' 开头；否则正文 ≤900 字且不得含 '## '\n"
        "- 行内「、」≤1（枚举用·）；正文/原文字数 ≥30%\n"
        "只输出 JSON。"
    )


def pick(n):
    rows = _news.query_news(status="active", limit=60)
    out = [r for r in rows if (r.get("content_ja") or "") and r.get("format")]
    return out[:n]


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    rows = pick(n)
    if not rows:
        print("无可用素材"); return 1
    passed = 0
    for r in rows:
        try:
            raw = llm.chat([{"role": "system", "content": SYS},
                            {"role": "user", "content": build_user(r)}], max_tokens=16000)
            m = re.search(r"\{.*\}", raw, re.S)
            d = json.loads(m.group(0))
            title, body = d.get("title", ""), d.get("body", "")
        except Exception as e:
            print(f"key={r['key'][:12]} 解析失败: {e}"); continue
        spec = {"fmt": r.get("format"), "lf": r.get("is_long_form"),
                "ja": len(r.get("content_ja") or "")}
        res = _pc.check_text(f"## {title}\n{body}", spec)
        ok = not res["problems"]
        passed += ok
        print("=" * 64)
        print(f"key={r['key'][:12]} fmt={spec['fmt']} lf={spec['lf']} ja={spec['ja']}")
        print(f"title={title!r} ({_pc.title_len(title)}字) body={res['body_len']}字 "
              f"##={res['h2']} kana={res['kana']} density={(res['density'] or 0):.1f}%")
        print("verdict:", "PASS" if ok else "FAIL")
        for p in res["problems"]:
            print("  ✗", p)
    print(f"\n机械门禁一把过率: {passed}/{len(rows)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
