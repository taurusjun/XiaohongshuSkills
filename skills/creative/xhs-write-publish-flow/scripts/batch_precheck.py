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
             标题不得以他/她/他们开头（零身份锚点 = FAIL；人名不是必须项，见下）

plan 里每个 key 可加 "name": "道枝骏佑/浪花男子" —— 多个候选用 / 或 , 分隔。
10/23 用户口径：**人名不是必须项**，缺人名只给 WARN，不 FAIL。硬门禁是「身份锚点」——
标题不能只有代词当主语（人名/团名/作品名/身份词占一个即可，事件本身当主语也行）。

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

# 10/31：标题落点（末分句结尾）空壳门禁。
# 失败形态：把素材里最有信息量的细节写成「证据动词+类别名词」，读者拿不到任何画面。
# 实锤：「大西流星扮成夏尔，原作者也发了表情」—— 20字内、锚点齐全、过了全部既有门禁，
# 但「表情」是什么没人知道，等于把「原作者盖章」这个最强信号降格成废话。
# 精度优先：只拦「发/晒/回/贴了+表情类名词」与「发文/发声（无宾语）」这两种真·空壳形态。
# 刻意不拦「一句台词，两种表情」「被镜头抓到的表情」这类把空壳词当对比极/带具体物的好标题。
SHELL_VERB_RE = re.compile(
    r"(也|还|又|只|就)?(发|晒|回|贴|po|PO)(了|了个|了一张|了一段)?(表情包|表情|动态|照片|图)$"
)
SHELL_BARE = ("发文", "发声")
# 10/31 第二例（c0040119）：标题堆日式黑话（新单/Under曲/W中心）+ 零团名 + 人名缩成昵称
# 「新单Under曲叫"编辑"，W中心是理理杏和莲加」——粉丝觉得是废话，路人 0 秒读不懂。
# 黑话是「压缩过的信息」，不是「不需要解释的信息」；压缩时先砍术语，团名/全名最后砍。
JARGON_TITLE = ("Under", "W中心", "Ｗ中心", "Wセンター", "新单", "表题曲", "标题曲", "选拔曲", "期生曲")
# 10/31 第三例（5a9e366a）：标题落点写成日文商品词「别注」，中文读者不认
# 「小嶋阳菜和LeSportsac联名，三款包全是别注」——20字、人名靠前、无代词，全部门禁通过，
# 但「别注」= 別注（特别定制）是日文词，中文没这个说法，标题等于没说完。
# 判据：日文商品/流通词直接当中文名词用 → FAIL（中文里不会出现这些词，误伤面≈0）
JP_LOANWORD_TITLE = (
    "别注", "別注", "受注", "完売", "完卖", "入荷", "予約", "预约受付",
    "数量限定", "别色", "別色", "别注品", "新作发表",
)
# 10/31 修正误报：「特别注重」里的「别注」不是日文词（cb1fe978 被误判）。
# 用负向前瞻排除 特別/分别/区别/级别/个别/识别 这些中文常见搭配。
JP_LOANWORD_RE = re.compile(
    r"(?<![特区分别级个识])别注|別注|受注|完売|完卖|入荷|予約|预约受付|数量限定|别色|別色|别注品|新作发表"
)
# 正文里的日文缩写（标题门禁管不到正文，5a9e/c004 各踩一次）
JP_TERM_BODY = ("W中心", "Ｗ中心", "Wセンター", "センター", "Wキメ", "立ち位置")
# 团名锚点白名单（只用于「放宽」黑话门禁：命中即认为锚点齐全，不会因此多拦稿）
GROUP_ANCHOR_RE = re.compile(r"[\u4e00-\u9fa5A-Za-z≒≠=！!]{2,8}(?:46|48)")
GROUP_ANCHOR_WORDS = (
    "乃木坂", "樱坂", "欅坂", "榉坂", "日向坂", "Snow Man", "SixTONES", "浪花男子",
    "なにわ男子", "timelesz", "King & Prince", "Kis-My-Ft2", "≒JOY", "≠ME", "=LOVE",
    "桃色幸运草", "早安少女组", "BE:FIRST", "JO1", "INI", "ME:I", "TWICE", "BTS",
)
# 标题党/泛化反应家族：只 WARN（前半句常有具体信息，一刀切会误伤历史好稿）
SHELL_TAIL_WARN = (
    "炸了", "引热议", "引发热议", "引关注", "引起关注", "上热搜", "有反应",
    "反应", "回应", "表态",
)
# 落点只写抽象动作（删了/改了/开始了），没有具体规则/数字/对象 → WARN
ABSTRACT_TAIL = ("删了", "改了", "换了", "变了", "没了", "做了", "开始了", "结束了")


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

    # 10/23：人名不是必填项（用户口径），降级为 WARN。
    # 真正的硬门禁是「身份锚点」——标题不能只有他/她/他们当主语：读者得知道这是谁、或哪一类人。
    names = spec.get("name") or spec.get("names")
    if title is not None:
        if re.match(r"^[「『（(\s]*(他们|她们|他|她|它)", title):
            problems.append(
                f"标题以代词开头、零身份锚点（{title[:14]}）：补人名/团名/作品名/身份词，或改用事件做主语"
            )
        if not names:
            warns.append("plan 未给 name，人名未校验（可选；人名非必须项）")
        else:
            alts = [n.strip() for n in re.split(r"[,，/|、]", str(names)) if n.strip()]
            if not any(a in title for a in alts):
                warns.append(
                    f"标题不含 name 候选（{'/'.join(alts)}）：人名非必须，确认钩子本身够硬、锚点够清楚"
                )

    # 10/31 硬门禁：标题的落点（最后一个分句的结尾）不能是空壳词。
    # 实测（大西流星扮夏尔稿 6810400b）：两条素材合并后唯一差异点=原作者回应，
    # 标题写成「大西流星扮成夏尔，原作者也发了表情」—— 20 字内、锚点齐全、过了全部
    # 既有门禁，但「表情」是什么读者拿不到任何画面，等于把最强信号降格成废话。
    # 规则：差异化信息必须落到「具体画面 / 具体身份」，不能落到 表情·发文·发声·反应·热搜 这类名词。
    if title is not None:
        clauses = [c.strip() for c in re.split(r"[，,；;：:]", title) if c.strip()]
        tail = (clauses[-1] if clauses else title).strip(" 　。！？!?…~～」』）)】")
        if SHELL_VERB_RE.search(tail):
            problems.append(
                f"标题落点是空壳（{tail}）：说了「发了什么类别」，没说是什么样子。"
                f"换成具体画面（「回了个星星眼」）或换成有画面的一侧"
            )
        elif tail.endswith(SHELL_BARE):
            problems.append(
                f"标题落点是空壳（{tail}）：发文/发声后面没有宾语，读者不知道内容，补实际内容或换落点"
            )
        else:
            hit_warn = next((w for w in SHELL_TAIL_WARN if tail.endswith(w)), None)
            if hit_warn:
                warns.append(
                    f"标题落点是泛反应词「{hit_warn}」（{title[:16]}）：确认前一分句已给出具体内容，否则读者不知道发生了什么"
                )
            _abs = next((w for w in ABSTRACT_TAIL if tail.endswith(w)), None)
            if _abs:
                warns.append(
                    f"标题落点偏抽象（…{_abs}）：补一个具体规则/数字/对象，别只写动作（{title[:16]}）"
                )

    # 10/31：圈内黑话门禁。黑话本身不是错，错的是「黑话当锚点用」——
    # 术语对粉丝是压缩信息，对划屏路人是乱码；标题里黑话已经够多时，至少得有一个可检索的锚点。
    if title is not None:
        jarg = [w for w in JARGON_TITLE if w in title]
        if jarg:
            cand = [n.strip() for n in re.split(r"[,，/|、]", str(names)) if n.strip()] if names else []
            has_anchor = (
                any(a in title or (len(a) >= 3 and a[:3] in title) for a in cand)
                or "《" in title
                or bool(GROUP_ANCHOR_RE.search(title))
                or any(w in title for w in GROUP_ANCHOR_WORDS)
            )
            if not has_anchor:
                problems.append(
                    f"标题堆圈内黑话（{'/'.join(jarg)}）且零团名/全名/作品名锚点（{title[:18]}）："
                    f"路人 0 秒读不懂。把团名或全名放进标题，术语最多留一个，其余改中文口语"
                )
            elif len(jarg) >= 2:
                warns.append(
                    f"标题含 {len(jarg)} 个圈内黑话（{'/'.join(jarg)}）：锚点够了，但确认术语只留最必要的一个"
                )

    # 10/31：日文商品词直接当中文名词用（别注/受注/完売…），中文读者解码不了 —— 一律 FAIL。
    if title is not None:
        loan = JP_LOANWORD_RE.findall(title)
        if loan:
            problems.append(
                f"标题用了日文商品词「{'/'.join(sorted(set(loan)))}」（{title[:18]}）：中文没这个说法，读者不知道是什么。"
                f"换成中文表达（别注→特别定制、受注→接单、完売→售罄、入荷→到货）"
            )

    body_len = wc(body)
    # 10/31：日文缩写混进正文（W中心=Wセンター=双中心）——标题门禁管不到正文，单独查。
    # 用户原话：「W中心 是什么玩意？？你他妈能说人话吗」。
    jp_body = [w for w in JP_TERM_BODY if w in body]
    if jp_body:
        problems.append(
            f"正文出现日文缩写「{'/'.join(sorted(set(jp_body)))}」：读者看不懂，改成中文"
            f"（W中心→双中心、センター→中心/中间位）"
        )
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
