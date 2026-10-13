"""渠道判定：skip 出口贯通 review→write（对齐原 skill ❌=skip）。"""
from agent import write as w
from agent import review


def test_judge_channels_emits_skip(monkeypatch):
    import json
    k1, k2 = "a" * 40, "b" * 40
    pkg = {"rows": [{"key": k1, "title": "T1"}, {"key": k2, "title": "T2"}], "entities": {}}
    monkeypatch.setattr(review.llm, "chat", lambda *a, **k: json.dumps({
        "aaaaaaaaaaaa": {"channel": "skip", "reason": "纯榜单无上下文"},
        "bbbbbbbbbbbb": {"channel": "both", "reason": "人物心路＋人物深度兼有"},
    }))
    monkeypatch.setattr(review, "_log_channels", lambda *a, **k: None)
    out = review.judge_channels("2026-10-13", pkg, {"aaaaaaaaaaaa": "S", "bbbbbbbbbbbb": "A"})
    assert out.get("aaaaaaaaaaaa") == "skip"      # skip 会落库（xhs 不列=默认，skip 要显式）
    assert out.get("bbbbbbbbbbbb") == "both"


def test_pick_candidates_excludes_skip(monkeypatch):
    A, B = "a" * 40, "b" * 40
    rows = [
        {"key": A, "content_ja": "x", "format": "story", "rewritten_content": "", "grade": "S",
         "title_score": 5.0, "cluster_keys": "", "channel_hint": "skip"},
        {"key": B, "content_ja": "x", "format": "story", "rewritten_content": "", "grade": "A",
         "title_score": 4.0, "cluster_keys": "", "channel_hint": ""},
    ]
    monkeypatch.setattr(w._news, "query_news", lambda **kw: rows)
    picks = [r["key"] for r in w.pick_candidates(5)]
    assert A not in picks and B in picks, picks   # skip 不进候选池


def test_write_one_skip_short_circuit():
    cand = {"key": "a" * 40, "channel_hint": "skip", "title": "T"}
    r = w.write_one(cand, dry_run=True)
    assert r.get("skipped") is True and r.get("channel") == "skip"
    assert any("skip" in p for p in r.get("problems") or [])


def test_judge_channels_sakamichi_hard_rule(monkeypatch):
    """坂道系 → xhs 门必过：LLM 判 gzh 时升级为 both（不跳过 gzh 判断）。"""
    import json
    k = "a" * 40
    pkg = {"rows": [{"key": k, "title": "齐藤京子出道10年转身当歌手",
                     "ja": "元日向坂46・齊藤京子 芸能活動10周年", "body": ""}], "entities": {}}
    monkeypatch.setattr(review.llm, "chat",
                        lambda *a, **kw: json.dumps({"aaaaaaaaaaaa": {"channel": "gzh", "reason": "人物深度"}}))
    monkeypatch.setattr(review, "_log_channels", lambda *a, **kw: None)
    out = review.judge_channels("2026-10-13", pkg, {"aaaaaaaaaaaa": "A"})
    assert out.get("aaaaaaaaaaaa") == "both", out


def test_judge_channels_skip_not_overridden_by_sakamichi(monkeypatch):
    import json
    k = "a" * 40
    pkg = {"rows": [{"key": k, "title": "乃木坂46 纯榜单", "ja": "乃木坂46", "body": ""}], "entities": {}}
    monkeypatch.setattr(review.llm, "chat",
                        lambda *a, **kw: json.dumps({"aaaaaaaaaaaa": {"channel": "skip", "reason": "纯榜单"}}))
    monkeypatch.setattr(review, "_log_channels", lambda *a, **kw: None)
    out = review.judge_channels("2026-10-13", pkg, {"aaaaaaaaaaaa": "A"})
    assert out.get("aaaaaaaaaaaa") == "skip", out
