"""services.precheck 机械门禁测试。"""
from services import precheck as pc

STORY = {"fmt": "story", "lf": 1, "ja": 2000}
NEWS = {"fmt": "news", "lf": 0, "ja": 1000}


def _ok_story_text():
    body = "## 小标题一\n" + "啊" * 500 + "\n## 小标题二\n" + "吧" * 400
    return "## 标题\n" + body


def test_story_ok():
    assert pc.check_text(_ok_story_text(), STORY)["problems"] == []


def test_missing_title_line():
    r = pc.check_text("啊" * 900, STORY)
    assert any("缺首行" in p for p in r["problems"])


def test_story_too_short():
    r = pc.check_text("## 标题\n## A\n啊" * 1, STORY)
    assert any("< 800" in p for p in r["problems"])


def test_story_needs_two_h2():
    body = "啊" * 900  # 无 ##
    r = pc.check_text("## 标题\n" + body, STORY)
    assert any("`##` 小标题" in p for p in r["problems"])


def test_news_must_not_have_h2():
    r = pc.check_text("## 标题\n## x\n" + "啊" * 900, NEWS)
    assert any("news 正文不得有" in p for p in r["problems"])


def test_dunhao_line():
    r = pc.check_text("## 标题\n这是A、B、C的排比\n" + "啊" * 900, NEWS)
    assert any("顿号行" in p for p in r["problems"])


def test_kana_over_5():
    r = pc.check_text("## 标题\nあいうえおか\n" + "啊" * 900, NEWS)
    assert any("假名" in p for p in r["problems"])


def test_shintai_fail():
    r = pc.check_text("## 标题\n発\n" + "啊" * 900, NEWS)
    assert any("新字体" in p for p in r["problems"])


def test_shintai_warn_person_name():
    r = pc.check_text("## 标题\n小嶋阳菜\n" + "啊" * 900, NEWS)
    assert any("嶋" in w for w in r["warns"])
    assert not any("新字体" in p for p in r["problems"])


def test_density_below_30():
    r = pc.check_text("## 标题\n" + "啊" * 100, {"fmt": "news", "lf": 0, "ja": 1000})
    assert any("密度" in p for p in r["problems"])


def test_title_too_long():
    r = pc.check_text("## " + "标" * 25 + "\n" + "啊" * 900, NEWS)
    assert any("标题" in p and "> 20" in p for p in r["problems"])


def test_h3_not_allowed():
    r = pc.check_text("## 标题\n### 三级\n## A\n## B\n" + "啊" * 900, STORY)
    assert any("三级标题" in p for p in r["problems"])
