from services import renwei


def test_renwei_review_exit0():
    assert renwei.review("普通的一句话，很正常。")["exit"] == 0


def test_renwei_review_exit1_clustered():
    # 同类（一、意义拔高）≥2 次 → 聚集 → 拒绝
    r = renwei.review("这标志着新时代。\n它也见证了历史。")
    assert r["exit"] == 1 and r["problems"]


def test_renwei_review_exit2_single():
    assert renwei.review("值得注意的是这一点。")["exit"] == 2


def test_renwei_dash_excluded_from_cluster():
    # 破折号不计入聚集：单独 2 个破折号 → 非聚集（exit<=2）
    r = renwei.review("他来了——然后又走了——就这样。")
    assert r["exit"] != 1


def test_renwei_check_lists_all_hits():
    assert any("排比三连" in p for p in renwei.check("她去演戏、唱歌、跳舞的，样样都行"))
    assert any("加粗滥用" in p for p in renwei.check("**小标题**"))
    assert renwei.check("普通的一句话。") == []
