#!/usr/bin/env python3
"""批量 draft 门禁预检（写稿阶段 / renwei 之前跑，一次覆盖所有机械门禁）

为什么需要这个脚本：Phase A 每写完一篇 story draft 就要做字数预检，Phase B 之前
还要做顿号预检、假名计数、新字体扫描、体裁结构检查。手敲这 6 类检查容易漏项，
而且「## 计数是否排除首行」「news 是否误带 ##」这类错误肉眼看不出来（9/22 实测：
4 篇 news 忘记写首行 `## 标题`、2 篇 story 的 ## 计数把标题行算了进去）。

用法
----
    python3 batch_precheck.py --dir /tmp/d0923 --plan /tmp/plan.json
    python3 batch_precheck.py --dir /tmp/d0923 --spec ba9a938aa5b8=story:1:1279 \
        --spec 5486d9330d9d=news:0:590

plan.json 格式（推荐，批量时写入一次）:
    {
      "ba9a938aa5b8": {"fmt": "story", "lf": 1, "ja": 1279},
      "5486d9330d9d": {"fmt": "news",  "lf": 0, "ja": 590}
    }

    ja  = 主素材 content_ja 字数（用本 skill 的 xhs_word_count.py 算）
          合并稿一律填【主素材】长度，不要填合并去重后的分母
          （参见「密度计算完整规则（含关联素材）」一节）
    fmt/lf 必须来自 DB 字段 curl GET，禁止用字数反推

draft 文件命名: <dir>/xhs_draft_<key12>.md 或 <dir>/gzh_draft_<key12>.md
首行必须是 `## 干净标题`（story 与 news 同规则）。

判据
----
story lf=1 : 《length(body) >= 800 且 body 的 `## ` 数 >= 2》（<850 给 WARN 余量提醒）
news  lf=0 : 《body 的 `## ` 数 == 0》（news 正文禁止任何分隔标题）
全部       : 顿号行 == 0（行内 `、` >= 2 即命中 renwei 排比三连的真实前置条件）
             假名 <= 5
             日文新字体 0 命中（嶋/壱 属人名保留规则，降级为 WARN）
             密度 = body字数 / ja >= 30%
             标题 <= 20 字（rewritten_title；news/story 同限，无例外）

退出码: 0 = 全部 PASS/WARN；1 = 有 FAIL
"""
import argparse
import glob
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
WORD_COUNT = os.path.join(HERE, "xhs_word_count.py")

# 标题长度只认 xhs_word_count.py 的单一算法源，禁止在本脚本手写第二份实现
sys.path.insert(0, HERE)
from xhs_word_count import xhs_title_len  # noqa: E402

# 只放「与简体确实不同」的字；嶋/壱/与/国/教 会误伤人名，单独放 WARN 组
SHINTAI_FAIL = "発恵価歳竜徳沢辺栄広芸戦売買読選抜強実気対経済冨円浜塩込み"
SHINTAI_WARN = "嶋壱"

# 9/13 补充：正则字符类是 [，、]（逗号与顿号同样计数），
# 所以预检只用「行内顿号 >= 2」这一个条件，不要自写完整正则（会误报 20+ 行）
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")


def body_of(path):
    """返回 (标题, 正文)。首行必须带 ## ；缺首行时按整文件当正文并报警。"""
    text = open(path, encoding="utf-8").read()
    lines = text.split("\n")
    if lines and lines[0].lstrip().startswith("#"):
        title = lines[0].strip().lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
    else:
        # 9/22 实测坑：news draft 直接从正文开写，入库会把正文第一句当 rewritten_title
        title = None
        body = text.strip()
    return title, body


def wc(text):
    if not text:
        return 0
    out = subprocess.run(
        [sys.executable, WORD_COUNT, text], capture_output=True, text=True
    ).stdout.strip()
    digits = "".join(c for c in out if c.isdigit())
    return int(digits) if digits else 0


def check(path, spec):
    title, body = body_of(path)
    problems, warns = [], []

    if title is None:
        problems.append("缺首行 `## 标题`（入库会把正文第一句当 rewritten_title）")

    if title is not None and title.startswith(("S-", "A-", "K-", "T-")):
        problems.append(f"标题残留编号前缀: {title[:20]}")

    # 10/6 起硬门禁：rewritten_title 一律 <=20 字（news/story 同限，无例外）。
    # 此前 story 走 64 字上限，10/6 待发队列 6 条里 4 条 22-26 字被用户整批退回。
    if title is not None:
        tl = xhs_title_len(title)
        if tl > 20:
            problems.append(f"标题 {tl} 字 > 20（news/story 同限；就地改短到 <=20）")

    body_len = wc(body)
    h2 = [l for l in body.split("\n") if l.startswith("## ")]
    h3 = [l for l in body.split("\n") if l.startswith("###")]

    if h3:
        problems.append(f"出现三级标题 ### ×{len(h3)}（story 小标题必须 ## ）")

    fmt, lf = spec.get("fmt", "?"), spec.get("lf", 0)
    if fmt == "story" and lf == 1:
        if body_len < 800:
            problems.append(f"story 正文 {body_len} 字 < 800（硬门禁）")
        elif body_len < 850:
            warns.append(f"story 正文 {body_len} 字，低于 850 余量线（改稿删字易跌破 800）")
        if len(h2) < 2:
            problems.append(f"story 正文 `##` 小标题 {len(h2)} 个 < 2")
    else:
        if h2:
            problems.append(f"news 正文不得有 `##` 分隔标题（发现 {len(h2)} 个），改自然过渡句")

    # 顿号行（含 gzh 长段落：同一段落不同句子里的顿号也计入聚集）
    dunhao = [
        (i, l) for i, l in enumerate(body.split("\n"), 1) if l.count("、") >= 2
    ]
    if dunhao:
        problems.append(f"顿号行 ×{len(dunhao)}（renwei 排比三连前置条件，整段清零）")

    kana = len(KANA_RE.findall(body))
    if kana > 5:
        problems.append(f"假名 {kana} > 5（含标题行时按全文计）")
    kana_all = len(KANA_RE.findall((title or "") + body))

    shintai_fail = sorted(set(re.findall(f"[{SHINTAI_FAIL}]", (title or "") + body)))
    if shintai_fail:
        problems.append(f"日文新字体命中 {shintai_fail}（逐处替换为简体后复查）")
    shintai_warn = sorted(set(re.findall(f"[{SHINTAI_WARN}]", (title or "") + body)))
    if shintai_warn:
        warns.append(f"疑似新字体 {shintai_warn}（人名保留规则：grep 上下文，人名放行）")

    ja = spec.get("ja") or 0
    density = (body_len / ja * 100) if ja else None
    if density is not None and density < 30:
        problems.append(f"密度 {density:.1f}% < 30%（先复核是否合并稿误用分母，再扩充）")

    return dict(
        key=os.path.basename(path),
        title=title,
        body_len=body_len,
        h2=len(h2),
        density=density,
        kana=kana_all,
        dunhao=dunhao,
        problems=problems,
        warns=warns,
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="draft 所在目录")
    ap.add_argument("--plan", help="plan.json 路径")
    ap.add_argument("--spec", action="append", default=[],
                    help="k12=fmt:lf:ja（可重复）")
    ap.add_argument("--glob", default="*_draft_*.md")
    args = ap.parse_args()

    plan = {}
    if args.plan:
        plan.update(json.load(open(args.plan, encoding="utf-8")))
    for s in args.spec:
        k, rest = s.split("=", 1)
        fmt, lf, ja = rest.split(":")
        plan[k] = {"fmt": fmt, "lf": int(lf), "ja": int(ja)}

    files = sorted(glob.glob(os.path.join(args.dir, args.glob)))
    if not files:
        print(f"no draft matched {args.dir}/{args.glob}")
        return 1

    fails = 0
    for path in files:
        k12 = re.sub(r"^(xhs|gzh)_draft_", "", os.path.basename(path))[:-3]
        spec = plan.get(k12)
        if spec is None:
            print(f"SKIP {k12}  (plan 里没有这个 key，先补 fmt/lf/ja 再判)")
            continue
        r = check(path, spec)
        verdict = "FAIL" if r["problems"] else ("WARN" if r["warns"] else "PASS")
        if r["problems"]:
            fails += 1
        dens = f"{r['density']:.1f}%" if r["density"] is not None else "n/a"
        print(
            f"{verdict} {k12}  body={r['body_len']}字 ##={r['h2']} "
            f"密度={dens} kana={r['kana']} | {(r['title'] or '')[:26]}"
        )
        for p in r["problems"]:
            print(f"      ✗ {p}")
        for w in r["warns"]:
            print(f"      ! {w}")
        for i, line in r["dunhao"][:3]:
            print(f"      · L{i}: {line[:90]}")

    print(f"\n{len(files)} 篇检查完毕，FAIL {fails} 篇")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
