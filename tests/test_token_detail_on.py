"""Token 明细：按日期取当天全部（供「run 折叠 article」分组）。"""
from services import token_usage as tu


def test_detail_on_filters_by_date(tmp_path, monkeypatch):
    from services import paths
    db = tmp_path / "t.db"
    monkeypatch.setattr(paths, "sqlite_path", lambda: str(db))
    tu.record("write", "article", "a" * 40, {"calls": 2, "total_tokens": 100})
    tu.record("write", "run", "", {"calls": 2, "total_tokens": 100})
    import datetime
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    rows = tu.detail_on(today)
    assert len(rows) == 2
    assert [r["scope"] for r in rows] == ["article", "run"]      # id 升序：article 在 run 前
    assert tu.detail_on("1999-01-01") == []


def test_record_stage_roundtrip(tmp_path, monkeypatch):
    """每次 fetch 调用带 stage 落库，可按 stage 聚合（重试=score_retry/regen）。"""
    import datetime
    from services import paths
    db = tmp_path / "t.db"
    monkeypatch.setattr(paths, "sqlite_path", lambda: str(db))
    k = "k" * 40
    for st in ("translate", "generate", "score", "score_retry", "regen"):
        tu.record("fetch", "call", k, {"calls": 1, "total_tokens": 100}, stage=st)
    tu.record("fetch", "run", "", {"calls": 5, "total_tokens": 500})
    today = datetime.datetime.now().strftime("%Y-%m-%d")
    rows = tu.detail_on(today, run="fetch")
    stages = [r["stage"] for r in rows if r["scope"] == "call"]
    assert stages == ["translate", "generate", "score", "score_retry", "regen"]
    retry = [r for r in rows if r["stage"] in ("score_retry", "regen")]
    assert len(retry) == 2
