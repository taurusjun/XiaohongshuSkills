from agent import write as w


def test_pick_candidates_dedup_cluster(monkeypatch):
    A, B, C = "a" * 40, "b" * 40, "c" * 40
    rows = [
        {"key": A, "content_ja": "x", "format": "story", "rewritten_content": "", "grade": "S",
         "title_score": 5.0, "cluster_keys": A + "," + B},
        {"key": B, "content_ja": "x", "format": "story", "rewritten_content": "", "grade": "S",
         "title_score": 4.9, "cluster_keys": A + "," + B},
        {"key": C, "content_ja": "x", "format": "news", "rewritten_content": "", "grade": "A",
         "title_score": 4.0, "cluster_keys": ""},
    ]
    monkeypatch.setattr(w._news, "query_news", lambda **kw: rows)
    picks = w.pick_candidates(3)
    keys = [r["key"] for r in picks]
    assert (A in keys) != (B in keys), keys        # 同组只取一条
    assert A in keys and C in keys, keys           # 取分高的 A + 独立的 C
