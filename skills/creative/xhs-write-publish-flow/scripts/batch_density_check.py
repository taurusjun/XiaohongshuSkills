#!/usr/bin/env python3
"""批量密度检查：读 draft 文件 + DB content_ja，输出 字数/密度/verdict。

用法：
    python3 scripts/batch_density_check.py <plan.json> [draft_dir=/tmp/xhs_0831]

plan.json 格式（由写稿批次生成，key 必须是完整 40 位）：
{
  "draft_S1.md": {"key": "27e5...", "rel": ["bbaf...", ...]},
  "draft_A3.md": {"key": "96d4..."}
}

判定（与 SKILL.md 一致）：
- story lf=1：正文字数 >=800 且 密度 >=30% 才 OK
- news lf=0：只要求 密度 >=30%
- 正文按 body-only 计数（首行 ## 标题剥离，因为入库逻辑把首行提为 rewritten_title，
  本地全文件计数会高估 ~30 字 —— 见 references/8-21-story-wordcount-titleline-gap.md）

verdict: OK / LEN!(story<800) / DEN!(密度<30%)

密度分母只用主素材 content_ja（合并稿按主素材密度判定，见 SKILL.md 密度规则）。
"""
import json
import os
import subprocess
import sys
import urllib.request

SCRIPT = os.path.expanduser(
    '~/.hermes/skills/creative/xhs-write-publish-flow/scripts/xhs_word_count.py')
BASE = 'http://127.0.0.1:5000'


def xhs_len(text: str) -> int:
    out = subprocess.run(['python3', SCRIPT, text],
                         capture_output=True, text=True).stdout.strip()
    return int(''.join(c for c in out if c.isdigit()) or 0)


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    plan = json.load(open(sys.argv[1]))
    draft_dir = sys.argv[2] if len(sys.argv) > 2 else '/tmp/xhs_0831'

    print(f"{'draft':<16} {'fmt/lf':<8} {'body':<6} {'ja':<6} {'density':<8} verdict")
    results = {}
    for df, info in plan.items():
        key = info['key']
        with urllib.request.urlopen(f'{BASE}/api/news/{key}', timeout=10) as r:
            d = json.loads(r.read())
        cj = d.get('content_ja') or ''
        fmt = d.get('format')
        lf = d.get('is_long_form')
        lines = open(os.path.join(draft_dir, df)).read().split('\n')
        body_only = '\n'.join(lines[1:])  # 首行标题剥离
        b = xhs_len(body_only)
        j = xhs_len(cj)
        density = b / j * 100 if j else 0
        need800 = (fmt == 'story' and lf == 1)
        ok_len = (not need800) or (b >= 800)
        ok_den = density >= 30.0
        verdict = 'OK' if (ok_len and ok_den) else ('LEN!' if not ok_len else 'DEN!')
        print(f"{df:<16} {fmt}/{lf:<7} {b:<6} {j:<6} {density:<7.1f}% {verdict}")
        results[df] = {'key': key, 'fmt': fmt, 'lf': lf, 'b_len': b,
                       'j_len': j, 'density': density, 'verdict': verdict}

    fail = [k for k, v in results.items() if v['verdict'] != 'OK']
    if fail:
        print(f"\nFAILED ({len(fail)}): {fail}")
        sys.exit(1)
    print(f"\nALL OK ({len(results)} drafts)")


if __name__ == '__main__':
    main()
