"""review 产出校验：当日有素材但评出 0 条 grade → 飞书告警。"""
from agent import review


def _stub(monkeypatch, chat_out):
    monkeypatch.setattr(review, "prepare_package",
                        lambda d: {"rows": [{"key": "a" * 12, "title": "T"}], "prev": [], "hist": {},
                                   "pub": "", "fb_ctx": {"last_pattern_no": 0, "tail": ""}, "entities": {}})
    monkeypatch.setattr(review, "build_messages", lambda d, pkg: [])
    monkeypatch.setattr(review.llm, "chat", lambda *a, **k: chat_out)
    monkeypatch.setattr(review, "persist", lambda *a, **k: (0, 0, 0, 0, 0))
    import services.review_archive as ra
    monkeypatch.setattr(ra, "build_archive", lambda d, single_table=False: "")
    import services.delivery as dv
    monkeypatch.setattr(dv, "web_save", lambda *a, **k: None)


def test_alert_when_zero_grades(monkeypatch):
    _stub(monkeypatch, '{"layer4": {"conclusion": "x"}}')
    sent = {}
    import scripts.feishu_bot as fb
    monkeypatch.setattr(fb, "send_alert", lambda t: sent.setdefault("t", t) or True)
    review.run("2026-10-15")
    assert "产出异常" in sent.get("t", ""), sent


def test_no_alert_when_graded(monkeypatch):
    _stub(monkeypatch, '{"grades": {"aaaaaaaaaaaa": {"grade": "A", "reason": "x"}}}')
    sent = {}
    import scripts.feishu_bot as fb
    monkeypatch.setattr(fb, "send_alert", lambda t: sent.setdefault("t", t) or True)
    review.run("2026-10-15")
    assert "t" not in sent, sent
