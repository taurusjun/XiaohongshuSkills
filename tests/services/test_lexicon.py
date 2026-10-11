"""lexicon 服务：单元测试（临时 DB，隔离）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))


def _fresh(tmp_path, monkeypatch):
    from services import paths, lexicon as LX
    db = tmp_path / "lex.db"
    monkeypatch.setattr(paths, "sqlite_path", lambda: str(db))
    LX.reload(); LX.ensure()
    return LX


def test_add_inject_replace_keep(tmp_path, monkeypatch):
    LX = _fresh(tmp_path, monkeypatch)
    assert LX.add("齋藤飛鳥", zh="斋藤飞鸟", type="person", status="manual")
    assert LX.add("春よ来い、マジで来い", zh="春来了，真的来了", type="work", status="manual")
    assert LX.add("じぇじぇじぇ", action="keep", type="keep", status="manual")
    LX.reload()
    # 注入：只命中本文
    hits = LX.inject("", "見た春よ来い、マジで来い")
    assert [h["term"] for h in hits] == ["春よ来い、マジで来い"]
    # 替换：整体 + 长词优先
    assert LX.replace("《春よ来い、マジで来い》") == "《春来了，真的来了》"
    # keep：原样
    assert LX.replace("じぇじぇじぇ最高") == "じぇじぇじぇ最高"
    assert LX.keep_spans("じぇじぇじぇ最高") == ["じぇじぇじぇ"]
    # 归一
    assert LX.normalize(["齋藤飛鳥", "未知名"])["齋藤飛鳥"] == "斋藤飞鸟"


def test_manual_not_downgraded(tmp_path, monkeypatch):
    LX = _fresh(tmp_path, monkeypatch)
    LX.add("X", zh="甲", type="word", status="manual")
    assert not LX.add("X", zh="乙", type="word", status="auto")   # auto 不覆盖 manual
    assert LX.get("X")["zh"] == "甲"


def test_discover_bracketed(tmp_path, monkeypatch):
    LX = _fresh(tmp_path, monkeypatch)
    got = LX.discover("看《ニュース番組》了吗", from_="write")
    assert "ニュース番組" in got
    assert LX.get("ニュース番組")["status"] == "pending"


def test_promote_revert(tmp_path, monkeypatch):
    LX = _fresh(tmp_path, monkeypatch)
    LX.add("Y", zh="乙", type="word", status="auto")
    assert LX.promote("Y") and LX.get("Y")["status"] == "manual"
    assert LX.revert("Y") and LX.get("Y")["status"] == "rejected"
