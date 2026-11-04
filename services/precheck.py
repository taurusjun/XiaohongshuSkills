"""批量 draft 机械门禁预检 —— 唯一实现（迁移自 batch_precheck.py，按渠道分流）。

- **xhs**：标题≤20；story lf=1 正文≥800 且 `##`≥2；news 无 `##`；无 `###`；顿号行 0；
  假名≤5；日文新字体 FAIL（嶋/壱 降级 WARN）；密度≥30%。
- **标题（10/31 新增）**：代词开头 / 空壳落点 / 黑话无锚点 / 日文商品词 → FAIL；
  泛反应词 / 黑话≥2 → WARN；gzh 只套日文商品词与正文日文缩写。
- **gzh**：标题≤30；**不套** 30% 密度门、不套 story/news 结构门（公众号长文排版自由）；
  其余（顿号/假名/新字体）照旧。对齐 `xhs-content-review`「gzh 稿不按 xhs 5 维/密度门」。
差异：不再 shell out 调 xhs_word_count.py，直接调 services.word_count。
"""
import argparse
import glob
import json
import os
import re

from services import rules
from services.word_count import content_len, title_len

_NF = rules.load("newfont.json")
_TH_X = rules.xhs_th()
_TH_G = rules.gzh_th()
SHINTAI_FAIL = _NF.get("fail", "発恵価歳竜徳沢辺栄広芸戦売買読選抜強実気対経済冨円浜塩込み")
SHINTAI_WARN = _NF.get("name_reserved", "嶋壱")


def _font_sets():
    """新字体集合：优先取 lexicon(type=font)，为空则回退 newfont.json。"""
    fail = warn = ""
    try:
        from services import lexicon as _lx
        for r in _lx.all(type="font"):
            if r.get("status") == "rejected":
                continue
            if r.get("note") == "fail":
                fail += r["term"]
            elif r.get("note") == "warn":
                warn += r["term"]
    except Exception:  # noqa: BLE001
        pass
    return (fail or SHINTAI_FAIL, warn or SHINTAI_WARN)
TITLE_MAX_XHS = _TH_X.get("title_max", 20)
TITLE_MAX_GZH = _TH_G.get("title_max", 30)
BODY_MIN_STORY = _TH_X.get("story_body_min", 800)
H2_MIN_STORY = _TH_X.get("story_h2_min", 2)
KANA_MAX = _TH_X.get("kana_max", 5)
DENSITY_MIN = _TH_X.get("density_min", 30)
KANA_RE = re.compile(r"[\u3040-\u309f\u30a0-\u30ff]")

# ── 10/31 标题门禁（迁移自 skill batch_precheck.py，2026-11-04）──
# 空壳落点：把差异化信息写成「证据动词+类别名词」，读者拿不到画面。
# 精度优先：只拦「发/晒/回/贴了+表情类名词」与「发文/发声（无宾语）」；
# 刻意不拦「一句台词，两种表情」这类把空壳词当对比极的好标题。
PRONOUN_HEAD_RE = re.compile(r"^[「『（(\s]*(他们|她们|他|她|它)")
SHELL_VERB_RE = re.compile(
    r"(也|还|又|只|就)?(发|晒|回|贴|po|PO)(了|了个|了一张|了一段)?(表情包|表情|动态|照片|图)$"
)
SHELL_BARE = ("发文", "发声")
SHELL_TAIL_WARN = (
    "炸了", "引热议", "引发热议", "引关注", "引起关注", "上热搜", "有反应",
    "反应", "回应", "表态",
)
# 圈内黑话：术语是压缩信息，不是「不需要解释的信息」；无锚点时 FAIL。
JARGON_TITLE = ("Under", "W中心", "Ｗ中心", "Wセンター", "新单", "表题曲", "标题曲", "选拔曲", "期生曲")
# 日文商品/流通词直接当中文名词用（中文里不成立，误伤面≈0）。
JP_LOANWORD_RE = re.compile(
    r"(?<![特区分别级个识])别注|別注|受注|完売|完卖|入荷|予約|预约受付|数量限定|别色|別色|别注品|新作发表"
)
# 正文里的日文缩写（标题门禁管不到正文）。
JP_TERM_BODY = ("W中心", "Ｗ中心", "Wセンター", "センター", "Wキメ", "立ち位置")
# 落点只写了一个抽象动作（删了/改了/开始了），没有具体规则/数字/对象 → WARN
ABSTRACT_TAIL = ("删了", "改了", "换了", "变了", "没了", "做了", "开始了", "结束了")
# 黑话锚点白名单（只用于放宽：命中即认为锚点齐全）。
GROUP_ANCHOR_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z≒≠=！!]{2,8}(?:46|48)")
GROUP_ANCHOR_WORDS = (
    "乃木坂", "樱坂", "欅坂", "榉坂", "日向坂", "Snow Man", "SixTONES", "浪花男子",
    "なにわ男子", "timelesz", "King & Prince", "Kis-My-Ft2", "≒JOY", "≠ME", "=LOVE",
    "桃色幸运草", "早安少女组", "BE:FIRST", "JO1", "INI", "ME:I", "TWICE", "BTS",
)

__all__ = ["split_title_body", "check_title", "check_text", "check", "main"]


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


def _title_tail(title):
    """末分句（按逗号/分号/冒号切），去掉尾部标点。与 skill batch_precheck 同口径。"""
    clauses = [c.strip() for c in re.split(r"[，,；;：:]", title or "") if c.strip()]
    tail = clauses[-1] if clauses else (title or "")
    return tail.strip(" 　。！？!?…~～」』）)】")


def _has_anchor(title):
    """标题本身的锚点：书名号 / xx46·xx48 / 团名白名单（无 plan name 时的口径）。"""
    return (
        "《" in title
        or bool(GROUP_ANCHOR_RE.search(title))
        or any(w in title for w in GROUP_ANCHOR_WORDS)
    )


def check_title(title, channel="xhs"):
    """只查标题门禁（供写稿选候选/复用）。返回 (problems, warns)。"""
    problems, warns = [], []
    if title is None:
        return problems, warns
    is_gzh = (channel == "gzh")
    tl = title_len(title)
    limit = TITLE_MAX_GZH if is_gzh else TITLE_MAX_XHS
    if tl > limit:
        problems.append(f"标题 {tl} 字 > {limit}（{'公众号' if is_gzh else 'news/story'}上限；就地改短到 <={limit}）")

    if not is_gzh:
        tail = _title_tail(title)
        if PRONOUN_HEAD_RE.match(title):
            problems.append(f"标题以「{title[:2]}」开头（零身份锚点）：改成人名/团名/事件主语")
        if SHELL_VERB_RE.search(tail):
            problems.append(
                f"标题落点是空壳（{tail}）：说了「发了什么类别」，没说是什么样子。"
                f"换成具体画面（「回了个星星眼」）或换有画面的一侧")
        elif tail.endswith(SHELL_BARE):
            problems.append(f"标题落点是空壳（{tail}）：发文/发声后面没有宾语，补实际内容或换落点")
        else:
            _hit = next((w for w in SHELL_TAIL_WARN if tail.endswith(w)), None)
            if _hit:
                warns.append(f"标题落点是泛反应词「{_hit}」（{title[:16]}）：确认前一分句已给出具体内容")
            _abs = next((w for w in ABSTRACT_TAIL if tail.endswith(w)), None)
            if _abs:
                warns.append(
                    f"标题落点偏抽象（…{_abs}）：补一个具体规则/数字/对象，别只写动作（{title[:16]}）")
        _jarg = [w for w in JARGON_TITLE if w in title]
        if _jarg:
            if not _has_anchor(title):
                problems.append(
                    f"标题堆圈内黑话（{'/'.join(_jarg)}）且零团名/全名/作品名锚点"
                    f"（{title[:18]}）：把团名或全名放进标题，术语最多留一个")
            elif len(_jarg) >= 2:
                warns.append(f"标题含 {len(_jarg)} 个圈内黑话（{'/'.join(_jarg)}）：确认只留最必要的一个")
    _loan = JP_LOANWORD_RE.findall(title)
    if _loan:
        problems.append(
            f"标题用了日文商品词「{'/'.join(sorted(set(_loan)))}」（{title[:18]}）：换中文表达"
            f"（别注→特别定制、受注→接单、完売→售罄、入荷→到货）")
    return problems, warns


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
        _tp, _tw = check_title(title, channel)
        problems += _tp
        warns += _tw

    body_len = content_len(body)
    _jp_body = [w for w in JP_TERM_BODY if w in body]
    if _jp_body:
        problems.append(
            f"正文出现日文缩写「{'/'.join(sorted(set(_jp_body)))}」：读者看不懂，改成中文"
            f"（W中心→双中心、センター→中心/中间位）")
    h2 = [l for l in body.split("\n") if l.startswith("## ")]
    h3 = [l for l in body.split("\n") if l.startswith("###")]

    fmt, lf = spec.get("fmt", "?"), spec.get("lf", 0)
    if not is_gzh:                                   # xhs 体裁结构门；gzh 长文排版自由
        if h3:
            problems.append(f"出现三级标题 ### ×{len(h3)}（story 小标题必须 ## ）")
        if fmt == "story" and lf == 1:
            if body_len < BODY_MIN_STORY:
                problems.append(f"story 正文 {body_len} 字 < {BODY_MIN_STORY}（硬门禁）")
            elif body_len < BODY_MIN_STORY + 50:
                warns.append(f"story 正文 {body_len} 字，低于 {BODY_MIN_STORY + 50} 余量线（改稿删字易跌破 {BODY_MIN_STORY}）")
            if len(h2) < H2_MIN_STORY:
                problems.append(f"story 正文 `##` 小标题 {len(h2)} 个 < {H2_MIN_STORY}")
        else:
            if h2:
                problems.append(f"news 正文不得有 `##` 分隔标题（发现 {len(h2)} 个），改自然过渡句")

    dunhao = [(i, l) for i, l in enumerate(body.split("\n"), 1) if l.count("、") >= 2]
    if dunhao:
        problems.append(f"顿号行 ×{len(dunhao)}（renwei 排比三连前置条件，整段清零）")

    _keep = 0
    try:
        from services import lexicon as _lx
        for _t in _lx.keep_spans((title or "") + body):        # 保留不译的拟声/neta → 不算残留
            _keep += len(KANA_RE.findall(_t))
    except Exception:  # noqa: BLE001
        _keep = 0
    kana = max(0, len(KANA_RE.findall(body)) - _keep)
    if kana > KANA_MAX:
        problems.append(f"假名 {kana} > {KANA_MAX}（已扣除保留词；含标题行按全文计）")
    kana_all = max(0, len(KANA_RE.findall((title or "") + body)) - _keep)

    _sf, _sw = _font_sets()
    shintai_fail = sorted(set(re.findall(f"[{_sf}]", (title or "") + body)))
    if shintai_fail:
        problems.append(f"日文新字体命中 {shintai_fail}（逐处替换为简体后复查）")
    shintai_warn = sorted(set(re.findall(f"[{_sw}]", (title or "") + body)))
    if shintai_warn:
        warns.append(f"疑似新字体 {shintai_warn}（人名保留规则：grep 上下文，人名放行）")

    ja = spec.get("ja") or 0
    density = (body_len / ja * 100) if ja else None
    if not is_gzh and density is not None and density < DENSITY_MIN - 0.05:   # 0.05 容差防 30.0<30 假报     # gzh 不套 xhs 30% 密度门
        problems.append(f"密度 {density:.1f}% < {DENSITY_MIN}%（先复核是否合并稿误用分母，再扩充）")

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
    ap.add_argument("--spec", action="append", default=[], help="<完整40位key>=fmt:lf:ja（可重复）")
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
        # ⚠️ draft 文件名应携带**完整 40 位 key**（AGENTS.md 规则）；这里按文件名里的 key 取用
        key_part = re.sub(r"^(xhs|gzh)_draft_", "", os.path.basename(path))[:-3]
        spec = plan.get(key_part)
        if spec is None:
            print(f"SKIP {key_part}  (plan 里没有这个 key，先补 fmt/lf/ja 再判)")
            continue
        r = check(path, spec, args.channel)
        verdict = "FAIL" if r["problems"] else ("WARN" if r["warns"] else "PASS")
        if r["problems"]:
            fails += 1
        dens = f"{r['density']:.1f}%" if r["density"] is not None else "n/a"
        print(f"{verdict} {key_part}  body={r['body_len']}字 ##={r['h2']} "
              f"密度={dens} kana={r['kana']} | {(r['title'] or '')[:26]}")
        for p in r["problems"]:
            print(f"      ✗ {p}")
        for w in r["warns"]:
            print(f"      ! {w}")

    print(f"\n{len(files)} 篇检查完毕，FAIL {fails} 篇")
    return 1 if fails else 0
