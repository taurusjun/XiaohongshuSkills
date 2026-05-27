#!/usr/bin/env python3
"""
单测：对比「标题先生成」vs「标题后生成（用导语作上下文）」的效果

用法：
    python scripts/test_title_order.py --key <article_key>
    python scripts/test_title_order.py  # 自动取最近一篇 story 文章
"""
import sys, os, argparse
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
os.environ.setdefault('SQLITE_PATH', 'data/news_dev.db')

from scripts.sqlite_db import get_by_key, _connect


def get_test_article(key: str = "") -> dict:
    if key:
        row = get_by_key(key)
        if not row:
            print(f"❌ 找不到文章: {key}")
            sys.exit(1)
        return row
    with _connect() as db:
        row = db.execute(
            "SELECT * FROM news WHERE format_suitability LIKE '%story%' "
            "AND length(content_ja) > 500 AND status='active' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        print("❌ 没有可用的 story 文章")
        sys.exit(1)
    return dict(row)


def approach_a(article: dict) -> dict:
    """当前方案：一次调用同时生成标题+导语+正文"""
    from scripts.yahoo_common import generate_story_article
    print("\n📌 方案A（当前）：一次调用，标题先于导语生成")
    result = generate_story_article(
        article['title_ja'], article.get('title', ''),
        body_ja=article.get('content_ja', ''),
    )
    if not result:
        print("  ❌ 生成失败")
        return {}
    print(f"  story_type : {result.get('story_type')}")
    print(f"  标题       : {result.get('title')}")
    print(f"  导语       : {result.get('intro','')[:60]}...")
    return result


def approach_b(article: dict) -> dict:
    """新方案：先生成导语/正文，再用导语生成标题"""
    from scripts.yahoo_common import generate_story_article, generate_title_only
    import json as _j

    print("\n📌 方案B（新）：先生成导语+正文，再用导语生成标题")

    # Pass 1：生成除标题外的所有内容（临时在 prompt 里把标题输出改成让模型跳过）
    # 实际实现时会修改 generate_story_article，这里用完整生成再替换标题来模拟
    result = generate_story_article(
        article['title_ja'], article.get('title', ''),
        body_ja=article.get('content_ja', ''),
    )
    if not result:
        print("  ❌ Pass 1 生成失败")
        return {}

    intro = result.get('intro', '')
    outro = result.get('outro', '')
    story_type = result.get('story_type', '')
    body_preview = result.get('body', '')[:300]
    print(f"  story_type : {story_type}")
    print(f"  导语       : {intro[:60]}...")
    print(f"  结语       : {outro[:60]}...")
    print(f"  Pass 1 标题（暂存）: {result.get('title')}")

    # Pass 2：用导语 + 结语 + 正文摘要 生成标题
    print("\n  → Pass 2：用导语+结语+正文摘要生成标题...")
    new_title = generate_title_only(
        article['title_ja'],
        article.get('content_ja', ''),
        summary=intro,
        outro=outro,
        story_type=story_type,
        current_title=result.get('title', ''),
        content_zh=body_preview,
    )
    if not new_title:
        print("  ❌ Pass 2 标题生成失败，使用 Pass 1 结果")
        new_title = result.get('title', '')

    result['title_b'] = new_title
    print(f"  方案B 标题 : {new_title}")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--key', default='', help='指定文章 key（不填则自动选最新 story）')
    parser.add_argument('--repeat', type=int, default=1, help='重复次数（多次对比方差）')
    args = parser.parse_args()

    article = get_test_article(args.key)
    print(f"\n{'='*60}")
    print(f"文章: {article.get('title')}")
    print(f"Key : {article.get('key')}")
    print(f"原标题(日文): {article.get('title_ja','')[:60]}")
    print(f"{'='*60}")

    for i in range(args.repeat):
        if args.repeat > 1:
            print(f"\n{'─'*40} 第 {i+1} 次 {'─'*40}")

        result_a = approach_a(article)
        result_b = approach_b(article)

        print(f"\n{'='*60}")
        print("📊 对比结果")
        print(f"{'='*60}")
        print(f"方案A（同步生成）: {result_a.get('title','')}")
        print(f"方案B（导语先行）: {result_b.get('title_b','')}")
        print(f"导语             : {result_b.get('intro','')[:80]}")
        print(f"{'='*60}\n")


if __name__ == '__main__':
    main()
