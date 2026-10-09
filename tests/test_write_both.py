from agent import write as w


def _pkg():
    return {"content_ja": "x", "target": "300~900字", "tmin": 300, "tmax": 900, "refs": "",
            "merge_text": "", "hist_text": "", "related_keys": [], "cluster_keys": [],
            "method": "post", "pre_channel": "xhs", "spec": {"fmt": "news", "lf": 0, "ja": 1000}}


def _fake_db(monkeypatch, initial=None):
    """用内存 dict 模拟 news 表，让 update_news/get_by_key 反映真实写入。"""
    state = dict(initial or {})
    monkeypatch.setattr(w._news, "get_by_key", lambda k: state.get(k, {"key": k}))
    def upd(k, fields):
        state.setdefault(k, {"key": k}).update(fields)
    monkeypatch.setattr(w._news, "update_news", upd)
    return state


def _patch(monkeypatch):
    monkeypatch.setattr(w, "prepare_package", lambda c: {**_pkg(), "channel_hint": c.get("channel_hint", "")})
    monkeypatch.setattr(w._dh, "fix_text", lambda b: (b, []))
    monkeypatch.setattr(w._kana, "replace", lambda s: s)
    monkeypatch.setattr(w._kana, "new_terms", lambda s: [], raising=False)
    monkeypatch.setattr(w._kana, "log_pending", lambda *a, **k: None)
    monkeypatch.setattr(w._pc, "check_text", lambda t, spec, channel="xhs": {"problems": [], "body_len": len(t), "h2": 0, "kana": 0})
    monkeypatch.setattr(w._pc, "title_len", lambda t: len(t))
    monkeypatch.setattr(w._rw, "review", lambda t: {"exit": 0, "problems": [], "hits": {}})
    monkeypatch.setattr(w._gz, "check", lambda t, b: [])
    monkeypatch.setattr(w, "score_content", lambda t, b, **k: {"total": 9})
    monkeypatch.setattr(w, "score_gzh", lambda t, b, **k: {"total": 8})


def _cand():
    return {"key": "a" * 40, "title": "t", "format": "news", "is_long_form": 0, "content_ja": "x"}


def test_write_one_both_channels(monkeypatch):
    _patch(monkeypatch)
    st = _fake_db(monkeypatch)
    monkeypatch.setattr(w, "compose", lambda *a, **k: {
        "channel": "both", "title": "X标题", "body": "xhs正文", "gzh_title": "G标题", "gzh_body": "gzh正文"})
    r = w.write_one(_cand(), dry_run=False)
    assert r["channel"] == "both"
    assert [v["channel"] for v in r["versions"]] == ["xhs", "gzh"]
    row = st["a" * 40]
    assert row["rewritten_title"] == "X标题" and row["wechat_title"] == "G标题"
    assert row["channel"] == "both"                       # 两版共存 → both
    assert row["preselected"] == 1                        # both：xhs 版本待发 → 1
    assert row["publish_xhs"] == 0


def test_write_one_xhs_channel_empty(monkeypatch):
    _patch(monkeypatch)
    st = _fake_db(monkeypatch)
    monkeypatch.setattr(w, "compose", lambda *a, **k: {"channel": "xhs", "title": "X", "body": "b"})
    w.write_one(_cand(), dry_run=False)
    row = st["a" * 40]
    assert row.get("channel", "") == ""                   # xhs 独有 → 空（旧约定）
    assert row["rewritten_title"] == "X" and "wechat_title" not in row


def test_write_one_gzh_channel(monkeypatch):
    _patch(monkeypatch)
    st = _fake_db(monkeypatch)
    monkeypatch.setattr(w, "compose", lambda *a, **k: {"channel": "gzh", "title": "G", "body": "文"})
    w.write_one(_cand(), dry_run=False)
    row = st["a" * 40]
    assert row["channel"] == "gzh" and row["wechat_title"] == "G"
    assert "rewritten_title" not in row


def test_write_one_clears_stale_gzh(monkeypatch):
    """xhs 重写时若残留 channel='gzh' 但无 wechat 内容 → 清空。"""
    _patch(monkeypatch)
    st = _fake_db(monkeypatch, {"a" * 40: {"key": "a" * 40, "channel": "gzh"}})
    monkeypatch.setattr(w, "compose", lambda *a, **k: {"channel": "xhs", "title": "X", "body": "b"})
    w.write_one(_cand(), dry_run=False)
    assert st["a" * 40].get("channel", "") == ""


def test_write_one_channel_fallback(monkeypatch):
    _patch(monkeypatch)
    _fake_db(monkeypatch)
    monkeypatch.setattr(w, "compose", lambda *a, **k: {"channel": "weird", "title": "X", "body": "b"})
    r = w.write_one(_cand(), dry_run=True)
    assert r["channel"] == "xhs" and len(r["versions"]) == 1


def test_write_one_channel_hint_forces_gzh(monkeypatch):
    """review 标注 channel_hint=gzh → 即使 LLM 判 xhs 也强制走 gzh。"""
    _patch(monkeypatch)
    st = _fake_db(monkeypatch)
    monkeypatch.setattr(w, "compose", lambda *a, **k: {"channel": "xhs", "title": "X", "body": "b"})
    cand = _cand()
    cand["channel_hint"] = "gzh"
    r = w.write_one(cand, dry_run=False)
    assert r["channel"] == "gzh" and r["versions"][0]["channel"] == "gzh"
    assert st["a" * 40]["channel"] == "gzh" and st["a" * 40].get("wechat_title") == "X"


def test_pick_candidates_includes_gzh_hint(monkeypatch):
    A, B = "a" * 40, "b" * 40
    rows = [
        {"key": A, "content_ja": "x", "format": "story", "rewritten_content": "", "grade": "",
         "channel_hint": "gzh", "title_score": 1.0, "cluster_keys": ""},
        {"key": B, "content_ja": "x", "format": "news", "rewritten_content": "", "grade": "B",
         "channel_hint": "", "title_score": 5.0, "cluster_keys": ""},
    ]
    monkeypatch.setattr(w._news, "query_news", lambda **kw: rows)
    keys = [r["key"] for r in w.pick_candidates(0)]
    assert A in keys            # gzh 方向（无 S/A 分级）也必须入选


def test_write_one_akb_bullet(monkeypatch):
    """AKB 晒照型（akb_type=bullet）→ 不写正文，只设 preselected=1/publish_xhs=0。"""
    st = _fake_db(monkeypatch)
    cand = {"key": "c" * 40, "title": "t", "format": "story", "is_long_form": 1,
            "content_ja": "x", "akb_type": "bullet"}
    r = w.write_one(cand, dry_run=False)
    assert r["bullet"] and r["ok"] and r["versions"] == []
    row = st["c" * 40]
    assert row["preselected"] == 1 and row["publish_xhs"] == 0 and row["publish_mode"] == "normal"
    assert "rewritten_content" not in row and "wechat_content" not in row


def test_pick_candidates_excludes_processed_bullet(monkeypatch):
    A, B = "a" * 40, "b" * 40
    rows = [
        {"key": A, "content_ja": "x", "format": "story", "rewritten_content": "", "grade": "AKB大TOP",
         "akb_type": "bullet", "preselected": 1, "title_score": 5.0, "cluster_keys": ""},   # 已处理
        {"key": B, "content_ja": "x", "format": "story", "rewritten_content": "", "grade": "AKB大TOP",
         "akb_type": "event", "preselected": 0, "title_score": 4.0, "cluster_keys": ""},     # 事件型
    ]
    monkeypatch.setattr(w._news, "query_news", lambda **kw: rows)
    keys = [r["key"] for r in w.pick_candidates(0)]
    assert B in keys and A not in keys
