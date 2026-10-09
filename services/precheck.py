"""批量 draft 机械门禁预检 —— 唯一实现（迁移自 batch_precheck.py，按渠道分流）。

- **xhs**：标题≤20；story lf=1 正文≥800 且 `##`≥2；news 无 `##`；无 `###`；顿号行 0；
  假名≤5；日文新字体 FAIL（嶋/壱 降级 WARN）；密度≥30%。
- **gzh**：标题≤30；**不套** 30% 密度门、不套 story/news 结构门（公众号长文排版自由）；
  其余（顿号/假名/新字体）照旧。对齐 `xhs-content-review`「gzh 稿不按 xhs 5 维/密度门」。
差异：不再 shell out 调 xhs_word_count.py，直接调 services.word_count。
"""
import argparse
import glob
import json
import os
import re

from services.word_count import content_len, title_len

SHINTAI_FAIL = "発恵価歳竜徳沢辺栄広芸戦売買読選抜強実気対経済冨円浜塩込み"
SHINTAI_WARN = "嶋壱"
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")

__all__ = ["split_title_body", "check_text", "check", "main"]


def split_title_body(text):
    """返回 (标题|None, 正文)。首行必须带 `## `；缺首行时按整文件当正文。"""
    lines = text.split("\n")
    if lines and lines[0].lstrip().startswith("#"):
        title = lines[0].strip().lstrip("#").strip()
        body = "\n".join(lines[1:]).strip()
    else:
        title = None
        body = text.strip()
    return title, body


def body_of(path):
    with open(path, encoding="utf-8") as f:
        return split_title_body(f.read())


def check_text(text, spec, channel="xhs"):
    """对草稿文本做机械门禁检查（按渠道），返回结果 dict。"""
    title, body = split_title_body(text)
    problems, warns = [], []
    is_gzh = (channel == "gzh")

    if title is None:
        problems.append("缺首行 `## 标题`（入库会把正文第一句当 rewritten_title）")
    elif title.startswith(("S-", "A-", "K-", "T-")):
        problems.append(f"标题残留编号前缀: {title[:20]}")

    if title is not None:
        tl = title_len(title)
        limit = 30 if is_gzh else 20
        if tl > limit:
            problems.append(f"标题 {tl} 字 > {limit}（{'公众号' if is_gzh else 'news/story'}上限；就地改短到 <={limit}）")

    body_len = content_len(body)
    h2 = [l for l in body.split("\n") if l.startswith("## ")]
    h3 = [l for l in body.split("\n") if l.startswith("###")]

    fmt, lf = spec.get("fmt", "?"), spec.get("lf", 0)
    if not is_gzh:                                   # xhs 体裁结构门；gzh 长文排版自由
        if h3:
            problems.append(f"出现三级标题 ### ×{len(h3)}（story 小标题必须 ## ）")
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

    dunhao = [(i, l) for i, l in enumerate(body.split("\n"), 1) if l.count("、") >= 2]
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
    if not is_gzh and density is not None and density < 30:     # gzh 不套 xhs 30% 密度门
        problems.append(f"密度 {density:.1f}% < 30%（先复核是否合并稿误用分母，再扩充）")

    return dict(key="", title=title, body_len=body_len, h2=len(h2), density=density,
                kana=kana_all, dunhao=dunhao, problems=problems, warns=warns, channel=channel)


def check(path, spec, channel="xhs"):
    with open(path, encoding="utf-8") as f:
        r = check_text(f.read(), spec, channel)
    r["key"] = os.path.basename(path)
    return r


def main(argv=None):
    import sys
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True, help="draft 所在目录")
    ap.add_argument("--plan", help="plan.json 路径")
    ap.add_argument("--spec", action="append", default=[], help="k12=fmt:lf:ja（可重复）")
    ap.add_argument("--channel", default="xhs", choices=["xhs", "gzh"])
    ap.add_argument("--glob", default="*_draft_*.md")
    args = ap.parse_args(argv)

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
        r = check(path, spec, args.channel)
        verdict = "FAIL" if r["problems"] else ("WARN" if r["warns"] else "PASS")
        if r["problems"]:
            fails += 1
        dens = f"{r['density']:.1f}%" if r["density"] is not None else "n/a"
        print(f"{verdict} {k12}  body={r['body_len']}字 ##={r['h2']} "
              f"密度={dens} kana={r['kana']} | {(r['title'] or '')[:26]}")
        for p in r["problems"]:
            print(f"      ✗ {p}")
        for w in r["warns"]:
            print(f"      ! {w}")

    print(f"\n{len(files)} 篇检查完毕，FAIL {fails} 篇")
    return 1 if fails else 0
