from services import density


def test_story_ok():
    r = density.check_draft("啊" * 900, "あ" * 2000, "story", 1)
    assert r["verdict"] == "OK"


def test_story_too_short():
    r = density.check_draft("啊" * 500, "あ" * 1000, "story", 1)
    assert r["verdict"] == "LEN!"


def test_news_density_below_30():
    r = density.check_draft("啊" * 100, "あ" * 1000, "news", 0)
    assert r["verdict"] == "DEN!"


def test_news_ok():
    r = density.check_draft("啊" * 900, "あ" * 2000, "news", 0)
    assert r["verdict"] == "OK"
