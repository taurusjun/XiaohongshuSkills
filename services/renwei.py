"""Renwei 审读（**完整移植** ~/.hermes/skills/writing/renwei-writing/scripts/renwei-pre-commit.py）。

六类 AI 信号扫描 + 聚集判定：
- 0 = 通过（无信号或非聚集）
- 1 = 拒绝（聚集信号，必须改稿）
- 2 = 警告（单个聚集信号，建议检查）
聚集定义：排除「破折号」后，同一类≥2 次 或 跨类≥3 类。

`check(text)` 返回全部命中（兼容旧调用）；`review(text)` 返回结构化 {exit, hits, problems}。
"""
import math
import re

__all__ = ["xhs_len", "scan", "review", "check"]

CATS = ["一、意义拔高", "二、宣传腔", "三、句式套路", "四、格式痕迹", "五、语气痕迹", "六、填充与对冲"]


def xhs_len(text):
    cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3000' <= c <= '\u303f' or '\uff00' <= c <= '\uffef')
    return math.ceil(cjk + (len(text) - cjk) * 0.5)


def scan(text):
    """返回 {类别: [(行号, 命中文本, 规则名), ...]}。"""
    lines = text.split("\n")
    results = {}

    def note(cat, line_no, matched, rule):
        results.setdefault(cat, []).append((line_no, matched.strip(), rule))

    prev_line = ""
    for i, line in enumerate(lines, 1):
        s = line.strip()

        # 一、意义拔高
        if re.search(r"(标志着|见证了|体现了|彰显了|折射出|反映了)", s):
            note("一、意义拔高", i, s, "标志性动词")
        if re.search(r"(最[^。，！？]{1,10}也最[^。，！？]{1,10}(的|是))", s):
            note("一、意义拔高", i, s, "标签式定义(最X也最Y)")
        if re.search(r"[^。，！？]{2,20}(的|之)(路|语言|镜子|货币|篇章|缩影|写照)", s):
            note("一、意义拔高", i, s, "格言公式(X是Y的Z)")
        if re.search(r"(而|但)(这个|那个)(故事|话题|事件)(的|就|却)(是|已经|不过)", s):
            note("一、意义拔高", i, s, "标题式收束")

        # 二、宣传腔
        for kw in ("璀璨", "深厚底蕴", "得天独厚", "赋能", "致力于", "匠心",
                   "不容错过", "令人惊叹", "极致体验"):
            if kw in s:
                note("二、宣传腔", i, s, f"宣传词({kw})")

        # 三、句式套路
        if re.search(r"(不是[^。，！？]{1,30}(而是|就是|是))", s):
            note("三、句式套路", i, s, "不是X而是Y")
        if re.search(r"(与其说[^。，！？]{1,20}不如说)", s):
            note("三、句式套路", i, s, "与其说不如说")
        if re.search(r"([^，。！？]{2,8}[，、]){2,}[^，。！？]{2,8}(的|是|和)", s):
            if "、" in s and s.count("、") >= 2:
                note("三、句式套路", i, s, "排比三连(顿号分列)")
        if re.search(r"(结果呢|然后呢|为什么|怎么办|谁曾想)[？?]{1}([^。！？]{1,30}[。！？])", s):
            note("三、句式套路", i, s, "自问自答")
        if re.search(r"^(说实话|老实讲|讲真|坦白说)", s):
            note("三、句式套路", i, s, "假坦诚开场")
        if i >= 2 and prev_line and len(prev_line) <= 15 and 0 < len(s) <= 15:
            note("三、句式套路", i, s, "短句轰炸(连续短句)")

        # 四、格式痕迹
        if "——" in s:
            note("四、格式痕迹", i, s, "破折号")
        if re.search(r"\*\*[^\*]{2,20}\*\*", s):
            note("四、格式痕迹", i, s, "加粗滥用")
        if re.search(r"^#{1,3}\s", prev_line) and len(s) < 40:
            if re.sub(r"^#+\s*", "", prev_line)[:5] in s:
                note("四、格式痕迹", i, s, "标题后废话垫场")

        # 五、语气痕迹
        if re.search(r"(有专家|业内人士|不少观察者|据说|传闻)(认为|指出|发现|表示)", s):
            note("五、语气痕迹", i, s, "模糊归因")
        if re.search(r"(未来可期|前景广阔|让我们拭目以待|值得期待)", s):
            note("五、语气痕迹", i, s, "万能展望结尾")
        if re.search(r"^(让我们|接下来|话不多说|言归正传)", s):
            note("五、语气痕迹", i, s, "签到式过渡")
        if re.search(r"(希望这对你有帮助|需要我展开吗|以下是)", s):
            note("五、语气痕迹", i, s, "聊天残留")

        # 六、填充与对冲
        if re.search(r"(值得注意的是|需要指出的是|不得不说)", s):
            note("六、填充与对冲", i, s, "填充语")
        if re.search(r"(在某种程度上|或许可能|一定程度上)", s):
            note("六、填充与对冲", i, s, "对冲叠加")

        prev_line = s

    return results


def review(text):
    """返回 {exit, hits, problems}。exit: 0=通过 / 1=拒绝(聚集) / 2=警告(非聚集)。"""
    hits = scan(text or "")
    # 破折号不计入聚集判断（中文常见标点）
    non_dash = {k: [h for h in v if h[2] != "破折号"] for k, v in hits.items()}
    non_dash = {k: v for k, v in non_dash.items() if v}
    max_same = max((len(v) for v in non_dash.values()), default=0)
    cats_real = len(non_dash)
    total = sum(len(v) for v in hits.values())
    clustered = max_same >= 2 or cats_real >= 3
    if total == 0:
        code = 0
    elif clustered:
        code = 1
    else:
        code = 2
    probs = [f"L{n} [{rule}] {m[:40]}" for cat in CATS for (n, m, rule) in hits.get(cat, [])]
    return {"exit": code, "hits": hits, "problems": probs}


def check(text):
    """兼容旧调用：返回全部命中字符串（fail-closed 的调用方应改用 review 的 exit==1）。"""
    return [f"L{n} {rule} | {m[:40]}" for cat in CATS
            for (n, m, rule) in scan(text or "").get(cat, [])]
