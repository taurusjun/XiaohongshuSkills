from services import references


def test_list_and_read():
    refs = references.list_refs()
    assert len(refs) > 100
    assert references.read_ref(refs[0])


def test_search():
    hits = references.search_refs("密度", limit=3)
    assert hits and all("ref" in h for h in hits)


def test_references_excludes_feedback_seed():
    from services import references as r
    names = [f.rsplit("/", 1)[-1] for f in r.list_refs()]
    assert "data-feedback-patterns.md" not in names
    # 检索也不应命中它
    hits = r.search_refs("セクシー女優 退圈", limit=50)
    assert all("data-feedback-patterns" not in h["ref"] for h in hits)
