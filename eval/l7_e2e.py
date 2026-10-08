"""L7：端到端 dry-run（写稿 → precheck → 入库 → publish_pipeline --preview，绝不真发布）。

用法: .venv/bin/python eval/l7_e2e.py <key前缀或留空自动选>
"""
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from agent import llm  # noqa: E402
from services import news as _news, precheck as _pc  # noqa: E402
from eval.l6_write_eval import SYS, build_user  # noqa: E402


def gen_image(path):
    from PIL import Image
    Image.new("RGB", (1080, 1440), (245, 245, 245)).save(path)


def main():
    prefix = sys.argv[1] if len(sys.argv) > 1 else ""
    rows = [r for r in _news.query_news(status="active", limit=200)
            if (r.get("content_ja") or "") and r.get("format") and r.get("format") == "story"]
    if prefix:
        rows = [r for r in rows if r["key"].startswith(prefix)]
    row = rows[0]
    print(f"素材 key={row['key']} fmt={row['format']} lf={row['is_long_form']} ja={len(row.get('content_ja') or '')}")

    spec = {"fmt": row.get("format"), "lf": row.get("is_long_form"), "ja": len(row.get("content_ja") or "")}
    msgs = [{"role": "system", "content": SYS}, {"role": "user", "content": build_user(row)}]
    title = body = ""
    res = None
    for attempt in range(1, 4):                      # 改稿循环：把门禁问题喂回
        raw = llm.chat(msgs, max_tokens=16000)
        try:
            d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
            title, body = d.get("title", ""), d.get("body", "")
            res = _pc.check_text(f"## {title}\n{body}", spec)
        except Exception as e:
            print(f"[attempt {attempt}] 解析失败: {e}"); continue
        print(f"[attempt {attempt}] precheck: {'PASS' if not res['problems'] else 'FAIL'} "
              f"body={res['body_len']} ##={res['h2']} kana={res['kana']} density={(res['density'] or 0):.1f}%")
        for p in res["problems"]:
            print("   ✗", p)
        if not res["problems"]:
            break
        msgs.append({"role": "assistant", "content": raw})
        msgs.append({"role": "user", "content": "上面未过门禁，请修正以下问题后重新只输出 JSON：\n- " + "\n- ".join(res["problems"])})
    if not res or res["problems"]:
        print("门禁未过（3 轮），L7 终止（不入库、不预览）"); return 1

    # 入库（容器快照库，隔离）
    _news.update_news(row["key"], {"rewritten_title": title, "rewritten_content": body,
                                   "publish_mode": "rewritten", "preselected": 1, "publish_xhs": 0})
    print("已入库（preselected=1, publish_xhs=0）")
    # 只读回验
    got = _news.get_by_key(row["key"])
    print(f"回验: title={got['rewritten_title']!r} len={len(got['rewritten_content'])}")

    # dry-run 发布（preview，不点发布）
    img = "/tmp/l7_img.png"; gen_image(img)
    cmd = [sys.executable, "scripts/publish_pipeline.py", "--title", title,
           "--content", body, "--images", img, "--preview", "--account", "default"]
    print("运行:", " ".join(cmd))
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=300)
    tail = (out.stdout or "")[-600:]
    print(tail)
    print("L7:", "OK（READY_TO_PUBLISH）" if "READY_TO_PUBLISH" in (out.stdout or "") else f"异常 exit={out.returncode}")
    return 0 if "READY_TO_PUBLISH" in (out.stdout or "") else 1


if __name__ == "__main__":
    sys.exit(main())
