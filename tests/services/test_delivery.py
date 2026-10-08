from services import delivery


def test_chunk_respects_max():
    text = "\n".join("x" * 40 for _ in range(20))   # 20 行 ×40
    chunks = delivery.chunk_markdown(text, max_chars=100)
    assert all(len(c) <= 100 for c in chunks)
    assert "".join(chunks) .replace("\n", "") == "x" * 800


def test_deliver_feishu_with_injected_sender():
    sent = []
    r = delivery.deliver("hello\nworld", channel="feishu", send_fn=lambda t: sent.append(t) or True)
    assert r["ok"] and r["channel"] == "feishu"
    assert len(sent) == 1


def test_deliver_web(tmp_path, monkeypatch):
    from services import paths
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    r = delivery.deliver("内容", channel="web", name="d.md")
    assert r["ok"] and (tmp_path / "data" / "reviews" / "d.md").read_text() == "内容"


def test_deliver_always_saves_locally(tmp_path, monkeypatch):
    from services import paths
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    r = delivery.deliver("hi", channel="feishu", name="x.md", send_fn=lambda t: True)
    assert r["local_saved"] and (tmp_path / "data" / "reviews" / "x.md").read_text() == "hi"
    assert r["feishu_sent"] and r["ok"]


def test_deliver_web_only(tmp_path, monkeypatch):
    from services import paths
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    r = delivery.deliver("hi", channel="web", name="y.md")
    assert r["local_saved"] and "feishu_sent" not in r
