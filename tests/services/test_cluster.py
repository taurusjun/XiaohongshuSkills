from services import cluster


def test_cluster_same_event():
    items = [
        {"key": "a" * 40, "title": "三田宽子谈儿媳能条爱未怀孕"},
        {"key": "b" * 40, "title": "能条爱未怀孕，三田宽子看4D影像"},
        {"key": "c" * 40, "title": "完全无关的体育新闻标题"},
    ]
    groups = cluster.cluster(items)
    got = {frozenset(x["key"][:1] for x in g) for g in groups}
    assert frozenset({"a", "b"}) in got
    assert all("c" not in g for g in got)
