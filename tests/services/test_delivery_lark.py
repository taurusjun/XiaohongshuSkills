from services.delivery import feishu_md_to_lark


def test_table_becomes_bullets_and_headings_bold():
    md = ("# 每日 Review\n\n"
          "| key | ts | cs | title |\n|---|---|---|---|\n"
          "| `abc` | 4.2 | 1.1 | 标题甲 |\n| `def` | 3.0 | 0.5 | 标题乙 |\n")
    lark = feishu_md_to_lark(md)
    assert "**每日 Review**" in lark
    assert "**标题甲**" in lark and "ts=4.2" in lark and "cs=1.1" in lark
    assert "**标题乙**" in lark
    assert "|---|" not in lark            # 表格分隔行消失


def test_multicard_page_label():
    from services.delivery import feishu_build_cards
    # 构造足够大以产生 >1 卡片
    md = "# 标题\n\n" + "\n\n".join("**段落%d**\n%s" % (i, "字" * 3000) for i in range(3))
    cards = feishu_build_cards(md, "每日素材 Review", max_chars=3500)
    assert len(cards) > 1
    titles = [c["header"]["title"]["content"] for c in cards]
    assert titles[0].endswith(f"（1/{len(cards)}）")
    assert titles[-1].endswith(f"（{len(cards)}/{len(cards)}）")


def test_card_v2_markdown_and_table():
    from services.delivery import feishu_build_cards_v2
    md = "# 每日素材 Review\n\n**数据范围：** 今天\n\n## 一、列表\n\n| a | b |\n|---|---|\n| 1 | 2 |\n"
    cards = feishu_build_cards_v2(md, "每日素材 Review")
    assert cards[0]["schema"] == "2.0"
    tags = [e["tag"] for e in cards[0]["body"]["elements"]]
    assert "markdown" in tags and "table" in tags
    # H1 不在正文（在 header）
    for e in cards[0]["body"]["elements"]:
        if e["tag"] == "markdown":
            assert not e["content"].startswith("# 每日素材 Review")
