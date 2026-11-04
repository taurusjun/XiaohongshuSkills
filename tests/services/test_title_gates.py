"""标题门禁（10/31 Hermes skill 迁移）测试。

负样本=四个实锤失败案例；正样本=三条历史好标题（防止规则过宽）。
超硬门禁必须零误伤，误伤样本进 scan_title_gates.py 全库回归。
"""
from services import precheck as pc

NEWS = {"fmt": "news", "lf": 0, "ja": 1000}
STORY = {"fmt": "story", "lf": 1, "ja": 2000}

_TITLE_KEYS = ("空壳", "黑话", "日文商品词", "开头")


def _title_problems(title, spec=NEWS):
    r = pc.check_text(f"## {title}\n\n" + "啊" * 900, spec)
    return [p for p in r["problems"] if any(k in p for k in _TITLE_KEYS)]


def test_shell_landing_fails():
    """「发了表情」= 把差异化信息降格成类别名词。"""
    assert _title_problems("大西流星扮成夏尔，原作者也发了表情")


def test_jargon_without_anchor_fails():
    """三个黑话 + 零团名 + 人名缩昵称。"""
    assert _title_problems("新单Under曲叫“编辑”，W中心是理理杏和莲加", STORY)


def test_jp_loanword_fails():
    """日文商品词直接当中文名词用。"""
    assert _title_problems("小嶋阳菜和LeSportsac联名，三款包全是别注")


def test_pronoun_head_fails():
    assert _title_problems("她停工两年，答案到现在还没出来", STORY)


def test_good_titles_pass():
    """反向验证：规则不能误伤这些历史好标题。"""
    for t in (
        "一句台词，两种表情",                     # 空壳词当对比极
        "日向坂46新单首周52.3万张再夺冠",          # 有团名锚点 + 一个黑话
        "伊藤理々杏毕业前站C位，歌名叫《编辑》",     # 全名 + 书名号
    ):
        assert _title_problems(t, STORY) == [], t


def test_shell_tail_is_warn_only():
    r = pc.check_text("## 某某宣布结婚，粉丝炸了\n\n" + "啊" * 900, NEWS)
    assert not any("空壳" in p for p in r["problems"])
    assert any("泛反应" in w for w in r["warns"])


def test_body_jp_abbrev_fails():
    r = pc.check_text("## 正常标题\n\n" + "啊" * 400 + " W中心 站位。" + "啊" * 500, NEWS)
    assert any("日文缩写" in p for p in r["problems"])


def test_gzh_skips_shell_but_keeps_loanword():
    """公众号：空壳/黑话不套，日文商品词照查。"""
    shell = pc.check_text("## 某某晒了照片\n\n" + "啊" * 900, NEWS, channel="gzh")
    assert not any("空壳" in p for p in shell["problems"])
    loan = pc.check_text("## 某某联名，三款包全是别注\n\n" + "啊" * 900, NEWS, channel="gzh")
    assert any("日文商品词" in p for p in loan["problems"])


def test_lexicon_replaces_jp_terms(tmp_path, monkeypatch):
    from services import paths, lexicon as LX
    monkeypatch.setattr(paths, "sqlite_path", lambda: str(tmp_path / "lex.db"))
    LX.reload(); LX.ensure()
    assert LX.add("别注", zh="特别定制", status="manual")
    assert LX.add("W中心", zh="双中心", status="manual")
    LX.reload()
    assert LX.replace("三款包全是别注") == "三款包全是特别定制"
    assert LX.replace("W中心站位") == "双中心站位"


def test_check_title_only():
    """标题门禁可单独复用（3 候选选择用）。"""
    assert pc.check_title("她停工两年", "xhs")[0]
    assert pc.check_title("指原莉乃把当年伤自己的规则删了", "xhs")[0] == []
    assert any("抽象" in w for w in pc.check_title("指原莉乃把当年伤自己的规则删了", "xhs")[1])


def test_abstract_tail_is_warn_only():
    """只写抽象动作 → WARN，不阻断。"""
    p, w = pc.check_title("指原莉乃把当年伤自己的规则删了", "xhs")
    assert p == []
    assert any("抽象" in x for x in w)


def test_pick_title_prefers_concrete():
    from agent.write import _pick_title
    cand = {"title": "指原莉乃把当年伤自己的规则删了"}
    chosen = _pick_title([
        "指原莉乃把当年伤自己的规则删了",          # 复用 DB + 抽象
        "指原莉乃三个团，不办总选举也不禁恋爱",    # 具体
        "她停工两年",                              # 代词开头
    ], cand)
    assert chosen == "指原莉乃三个团，不办总选举也不禁恋爱"


def test_reused_db_title():
    from agent.write import _reused_db_title
    assert _reused_db_title("指原莉乃把当年伤自己的规则删了",
                            "指原莉乃把当年伤自己的规则删了")
    assert not _reused_db_title("指原莉乃三个团，不办总选举也不禁恋爱",
                                "指原莉乃把当年伤自己的规则删了")

def test_pick_title_tie_takes_first():
    """无数字/书名号时三条同分 → 取第一条（LLM 的画面型）。"""
    from agent.write import _pick_title
    cand = {"title": "指原莉乃把当年伤自己的规则删了"}
    first = "指原莉乃删了总选举，还取消了恋爱禁止"
    assert _pick_title([first, "指原莉乃拿制作人奖：我吃过那种苦",
                        "指原莉乃：不让她们再排一次名次"], cand) == first
