from services import split_write as sp


def test_split_groups(monkeypatch):
    data = {"m": {"key": "m", "content_ja": "x" * 8000},   # content_len 8000
            "a": {"key": "a", "content_ja": "y" * 1000},   # 1000
            "b": {"key": "b", "content_ja": "z" * 1000}}   # 1000
    monkeypatch.setattr(sp, "get_by_key", lambda k: data.get(k))
    cand = {"key": "m", "cluster_keys": "a,b"}
    assert sp.should_split(cand) is True
    groups = sp.split_groups(cand)
    assert groups == [["m"], ["a", "b"]]


def test_no_split_small(monkeypatch):
    data = {"m": {"key": "m", "content_ja": "x" * 400}, "a": {"key": "a", "content_ja": "y" * 400}}
    monkeypatch.setattr(sp, "get_by_key", lambda k: data.get(k))
    assert sp.should_split({"key": "m", "cluster_keys": "a"}) is False
