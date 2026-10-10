"""硬规则 config + 服务（写稿 skill 复刻）单测。"""
from services import content_gate as cg, name_variants as nv, titles as tt
from services import routing, rules, precheck, batches


def test_thresholds_config():
    t = rules.thresholds()
    assert t["xhs"]["title_max"] == 20
    assert t["xhs"]["story_min_score"] == 6
    assert t["xhs"]["news_min_score"] == 5
    assert t["gzh"]["min_score"] == 7
    assert t["batch"]["window_days"] == 3
    assert t["split"]["threshold"] == 3000


def test_precheck_reads_config():
    assert "発" in precheck.SHINTAI_FAIL and "強" in precheck.SHINTAI_FAIL
    assert precheck.SHINTAI_WARN == "嶋壱"
    assert precheck.TITLE_MAX_XHS == 20 and precheck.TITLE_MAX_GZH == 30
    assert precheck.DENSITY_MIN == 30 and precheck.KANA_MAX == 5


def test_routing_detail():
    assert routing.route_detail("某厂牌销量破百万张")[0] == "gzh"
    assert routing.route_detail("私服街拍同框")[0] == "xhs"
    h, c = routing.route_detail("厂牌销量 + 私服花絮")   # 皆中
    assert h == "ambiguous" and c == "low"
    h, c = routing.route_detail("某艺人开了演唱会")       # 皆不中
    assert c == "low" and h in ("xhs", "ambiguous")


def test_titles_fit_and_limit():
    assert tt.limit_for("xhs") == 20 and tt.limit_for("gzh") == 30
    short = "短标题"
    assert tt.fit(short, 20) == (short, False)
    long_sep = "第一个话题、第二个话题、第三个很长的话题名称"
    out, need = tt.fit(long_sep, 12)
    assert need is False and out.endswith("话题") or out
    # 无分隔符 → 机械砍尾 + need_llm
    out2, need2 = tt.fit("一二三四五六七八九十十一十二十三", 5)
    assert need2 is True


def test_name_variants_replace_and_keep():
    out, ch = nv.replace("萘谷慧悟和萩谷慧悟，还有小嶋阳菜、山本圭壱")
    assert "萘" not in out and "萩谷" in out
    assert "小嶋" in out and "圭壱" in out          # 人名约定保留
    assert any("萘" in c for c in ch)


def test_content_gate_marks_manual():
    r = cg.check("某元AV女优的采访", "夜店接客六年……")
    assert r and r["manual_review"] == 1 and "成人" in r["manual_reason"]
    r2 = cg.check("线上会议礼仪", "ビジネスコラム：オンライン会議注意点")
    assert r2 and r2["manual_review"] == 1
    assert cg.check("普通偶像演唱会", "粉丝很开心") is None


def test_batches_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(batches, "_dir", lambda: tmp_path)
    batches.save("2026-10-10", {"processed": [{"key": "k1", "ok": True}],
                                "skipped": [{"key": "k2", "reason": "纯重复(历史已发)→跳过"}]})
    m = batches.load("2026-10-10")
    assert batches.covered_keys([m]) == {"k1", "k2"}
    assert batches.skipped_map([m])["k2"].startswith("纯重复")


def test_need_tiers():
    import agent.write as W
    story = {"format": "story"}
    assert W._need(story, "xhs", 800) == 6          # ≤900 → 6
    assert W._need(story, "xhs", 900) == 6
    assert W._need(story, "xhs", 1200) == 7         # >900 → 7（长必须有长的价值）
    assert W._need({"format": "news"}, "xhs", 1200) == 5
    assert W._need(story, "gzh", 1200) == 7
