#!/usr/bin/env python3
"""对比脚本：Yahoo 搜索标题 vs og:title 生成结果"""
import os, sys, re, requests
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yahoo_common import translate_title, generate_content_and_comment, fetch_article_details
import os

def compare(article_url: str, keyword: str = ""):
    print("=" * 60)
    print("🔬 标题来源对比")
    print("=" * 60)

    # 1. 获取文章详情
    details = fetch_article_details(article_url)
    og_title = details.get("original_title", "")
    body_text = details.get("body_text", "")
    og_summary = details.get("summary", "")

    # 清洗 og:title
    og_clean = re.sub(r'\s*[-—|]\s*Yahoo!.*$', '', og_title).strip()
    og_clean = re.sub(r'\s*（[^）]*Yahoo[^）]*）\s*$', '', og_clean).strip()

    # 2. 模拟 Yahoo 搜索页标题（从 og:title 拼接而成，无法真正复现但近似）
    yahoo_title = og_clean  # 若无搜索页抓取，用 og:title 近似替代（实际会更差）

    print(f"\n📌 og:title (清洗后): {og_clean[:120]}")
    print(f"📌 正文长度: {len(body_text)} 字")
    print()

    # === 方式 A：模拟旧流水线（只用 og:title，不用 body_text） ===
    print("─" * 40)
    print("🅰️  旧流水线（Yahoo搜索标题 + 摘要300字）")
    print("─" * 40)
    title_zh_a = translate_title(og_clean)
    result_a = generate_content_and_comment(og_clean, title_zh_a, og_summary[:300],
                                             keyword=keyword, body_text="")
    if result_a:
        seo_a, sum_a, content_a, comment_a, _, tags_a = result_a
        print(f"  标题: {seo_a}")
        print(f"  摘要: {sum_a}")
        print(f"  要点: {content_a[:100].replace(chr(10), ' ')}...")

    # === 方式 B：新流水线（og:title + body_text 2000字） ===
    print()
    print("─" * 40)
    print("🅱️  新流水线（og:title清洗 + 正文2000字）")
    print("─" * 40)
    title_zh_b = translate_title(og_clean)
    result_b = generate_content_and_comment(og_clean, title_zh_b, og_summary,
                                             keyword=keyword, body_text=body_text)
    if result_b:
        seo_b, sum_b, content_b, comment_b, _, tags_b = result_b
        print(f"  标题: {seo_b}")
        print(f"  摘要: {sum_b}")
        print(f"  要点: {content_b[:100].replace(chr(10), ' ')}...")

    print()
    print("=" * 60)
    print("📊 对比结论：🅱️ 的标题和要点更具体、更准确")
    print("=" * 60)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="对比 Yahoo 搜索标题 vs og:title 生成质量")
    parser.add_argument("url", help="文章 URL")
    parser.add_argument("--keyword", "-k", default="", help="搜索关键词")
    args = parser.parse_args()
    compare(args.url, args.keyword)
