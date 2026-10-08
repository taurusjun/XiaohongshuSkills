from services import renwei, gzh_review


def test_renwei_parallel_and_bold():
    assert any("排比三连" in p for p in renwei.check("她去演戏、唱歌、跳舞的，样样都行"))
    assert any("加粗滥用" in p for p in renwei.check("**小标题**"))
    assert renwei.check("普通的一句话。") == []


def test_gzh_title_too_long():
    assert any("> 30" in p for p in gzh_review.check("标" * 35, "第一段。"))
