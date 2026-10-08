"""契约：REST 皮 == service 芯。"""
from services import word_count as wc
from services import precheck as pc


def test_rest_word_count_parity(client):
    text = "hello\nworld"
    r = client.post("/api/services/word-count", json={"text": text})
    assert r.status_code == 200
    assert r.get_json()["content_len"] == wc.content_len(text)


def test_rest_word_count_title(client):
    r = client.post("/api/services/word-count", json={"text": "标题", "mode": "title"})
    assert r.get_json()["title_len"] == wc.title_len("标题")


def test_rest_precheck_parity(client):
    text = "## 标题\n## A\n啊" * 1
    spec = {"fmt": "story", "lf": 1, "ja": 100}
    r = client.post("/api/services/precheck", json={"text": text, "spec": spec})
    body = r.get_json()
    core = pc.check_text(text, spec)
    assert len(body["problems"]) == len(core["problems"])


def test_rest_schedule_deterministic(client):
    r1 = client.get("/api/services/schedule?date=2026-10-09&seed=42")
    r2 = client.get("/api/services/schedule?date=2026-10-09&seed=42")
    assert r1.get_json()["plan"] == r2.get_json()["plan"]
