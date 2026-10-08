from services import references


def test_list_and_read():
    refs = references.list_refs()
    assert len(refs) > 100
    assert references.read_ref(refs[0])


def test_search():
    hits = references.search_refs("密度", limit=3)
    assert hits and all("ref" in h for h in hits)
