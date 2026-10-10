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


def test_new_terms_captures_full_title():
    from services import kana
    t = "10月23日开幕的《春よ来い、マジで来い》，是她第一次站上舞台"
    terms = kana.new_terms(t)
    assert "マジで" in terms                          # 连续假名片段
    assert "春よ来い、マジで来い" in terms             # 整段作品名（新增）


def test_auto_promote_skips_fragments(monkeypatch, tmp_path):
    from services import kana as K
    import agent.llm
    monkeypatch.setattr(K, "pending", lambda: [
        {"term": "ーザー", "ctx": "卡兹雷ーザー率平成军"},
        {"term": "れざる", "ctx": "《室井慎次 败れざる者》"},
        {"term": "クイズフェス", "ctx": "《クイズフェス!2026秋》"},
    ])
    monkeypatch.setattr(K, "_pending_path", lambda: tmp_path / "p.jsonl")
    monkeypatch.setattr(K, "promote", lambda t, zh: None)
    monkeypatch.setattr(agent.llm, "chat", lambda m, max_tokens=0:
                        '{"ーザー":"扎","れざる":"不败者","クイズフェス":"问答节"}')
    res = dict(K.auto_promote(dry_run=True))
    assert "ーザー" not in res and "れざる" not in res      # 碎片跳过
    assert res.get("クイズフェス") == "问答节"               # 标题词保留
