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
