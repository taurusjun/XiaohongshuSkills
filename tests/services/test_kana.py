from services import kana


def test_replace_and_count():
    assert kana.count("あいう") == 3
    out = kana.replace("ひなた坂46と《シンクロニシティ》")
    assert kana.count(out) <= 2
    assert "《《" not in out


def test_table_loaded():
    assert len(kana.load()) > 20


def test_new_terms_and_promote(tmp_path, monkeypatch):
    from services import paths, kana
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    (tmp_path / "config").mkdir(); (tmp_path / "data").mkdir()
    (tmp_path / "config" / "kana_replace.json").write_text("{}", encoding="utf-8")
    kana._repl = None
    t = "某处出现了アラレちゃん和ドラえもん"
    added = kana.log_pending(t, note="k")
    assert "アラレちゃん" in added
    assert len(kana.pending()) == len(added)
    kana.promote("アラレちゃん", "阿拉蕾")
    assert all(r["term"] != "アラレちゃん" for r in kana.pending())
