from agent import write as w


def _pkg():
    return {"content_ja": "x", "target": "300~900字", "tmin": 300, "tmax": 900, "refs": "",
            "merge_text": "", "hist_text": "", "related_keys": [], "cluster_keys": [],
            "method": "post", "pre_channel": "xhs", "spec": {"fmt": "news", "lf": 0, "ja": 1000}}


def _patch(monkeypatch):
    monkeypatch.setattr(w, "prepare_package", lambda c: _pkg())
    monkeypatch.setattr(w._dh, "fix_text", lambda b: (b, []))
    monkeypatch.setattr(w._kana, "replace", lambda s: s)
    monkeypatch.setattr(w._kana, "new_terms", lambda s: [], raising=False)
    monkeypatch.setattr(w._kana, "log_pending", lambda *a, **k: None)
    monkeypatch.setattr(w._pc, "check_text", lambda t, spec: {"problems": [], "body_len": len(t), "h2": 0, "kana": 0})
    monkeypatch.setattr(w._pc, "title_len", lambda t: len(t))
    monkeypatch.setattr(w._rw, "review", lambda t: {"exit": 0, "problems": [], "hits": {}})
    monkeypatch.setattr(w._gz, "check", lambda t, b: [])
    monkeypatch.setattr(w, "score_content", lambda t, b, **k: {"total": 9})
    monkeypatch.setattr(w, "score_gzh", lambda t, b, **k: {"total": 8})


def test_write_one_both(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(w, "compose", lambda *a, **k: {
        "channel": "both", "title": "X标题", "body": "xhs正文", "gzh_title": "G标题", "gzh_body": "gzh正文"})
    r = w.write_one({"key": "a" * 40, "title": "t", "format": "news", "is_long_form": 0, "content_ja": "x"}, dry_run=True)
    assert r["channel"] == "both"
    assert [v["channel"] for v in r["versions"]] == ["xhs", "gzh"]
    assert all(v["ok"] for v in r["versions"])


def test_write_one_channel_fallback(monkeypatch):
    _patch(monkeypatch)
    monkeypatch.setattr(w, "compose", lambda *a, **k: {"channel": "weird", "title": "X", "body": "b"})
    r = w.write_one({"key": "a" * 40, "title": "t", "format": "news", "is_long_form": 0, "content_ja": "x"}, dry_run=True)
    assert r["channel"] == "xhs" and len(r["versions"]) == 1
