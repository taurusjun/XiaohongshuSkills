from services.review_archive import build_archive


def test_archive_template():
    rows = [
        {"key": "k" * 40, "title": "标题A", "fetch_by": "乃木坂", "title_score": 4.2,
         "content_score": 1.1, "content_ja": "あ" * 30},
        {"key": "j" * 40, "title": "标题B", "fetch_by": "AKB", "title_score": 3.0,
         "content_score": 0.5, "content_ja": "い" * 10},
    ]
    md = build_archive("2026-10-08", rows)                      # 默认单表
    assert md.startswith("# 每日素材 Review — 2026-10-08（东京时间）")
    assert "**fetch_by 分布：**" in md
    assert "## 一、全量素材一览（单张合并表，2条）" in md
    assert "| 来源 | key | ts | cs | cj_len | title |" in md
    assert "| 乃木坂 | `" + "k" * 40 + "` | 4.2 | 1.1 | 30 | 标题A |" in md
    # 分组模式仍可用
    md2 = build_archive("2026-10-08", rows, single_table=False)
    assert "### fetch_by = 乃木坂（1条）" in md2


def test_no_japanese_label():
    from services.review_archive import build_archive
    md = build_archive("2026-10-08", [])
    assert "データ範囲" not in md and "数据范围" in md
