#!/usr/bin/env python3
"""Yahoo News 并行抓取器 — SQLite 专用版
收集所有 keyword 的文章列表后，用 ThreadPoolExecutor 并行处理每篇文章。
不影响 Notion 路径（yahoo_news_auto.py 保持不变）。
"""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from concurrent.futures import ThreadPoolExecutor, as_completed
from config.yahoo_conf import STORAGE_BACKEND, FETCH_PARALLEL

# Reuse CDP fetch from original script
from yahoo_news_auto import (
    fetch_news_via_cdp, KEYWORD_TAG_MAP, DEFAULT_KEYWORDS
)
from yahoo_common import (
    process_news_item, push_with_gallery, load_today_keys,
    extract_key_from_url, check_chrome_cdp, check_proxy,
    _disable_proxy, LITELLM_API_KEY, LITELLM_MODEL,
)

# ============ Parallel Pipeline ============

def fetch_all_articles(keywords: list[dict], existing_keys: set, max_workers: int) -> list[dict]:
    """并行收集所有 keyword 的文章（并行 CDP）"""
    tasks = []
    lock = threading.Lock()

    def _fetch_one(kw):
        k, mx, cf = kw['keyword'], kw.get('max', 10), kw.get('china_filter', False)
        with lock:
            print(f"\n{'━' * 60}")
            print(f"🔍 关键词: 【{k}】| 最多 {mx} 条")
            print(f"{'━' * 60}")
        articles = fetch_news_via_cdp(k, mx, cf, existing_keys)
        with lock:
            print(f"  ✅ 找到 {len(articles)} 条\n")
        extra_tags = KEYWORD_TAG_MAP.get(k, []) or [k]
        return [{'news': a, 'keyword': k, 'extra_tags': extra_tags} for a in articles]

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_fetch_one, kw): kw for kw in keywords}
        for f in as_completed(futures):
            tasks.extend(f.result())
    return tasks


def process_article(task: dict):
    """处理单篇文章（线程安全）"""
    news = task['news']
    keyword = task['keyword']
    extra_tags = task['extra_tags']
    key = extract_key_from_url(news['link'])

    process_news_item(news, no_translate=False, extra_tags=extra_tags, keyword=keyword)

    # push_with_gallery is thread-safe (each article is independent)
    if not news.get('_skip'):
        push_with_gallery(news)

    return key, news


def run_parallel(keywords: list[dict], max_workers: int = 3):
    """主入口：收集 + 并行处理"""
    print(f"\n🚀 SQLite 并行抓取 | keywords={len(keywords)} | workers={max_workers}")
    print(f"   模型={LITELLM_MODEL} | 后端={STORAGE_BACKEND}")

    # Dedup
    print("📋 加载去重 key...")
    existing_keys = load_today_keys()

    # Phase 1: Collect
    tasks = fetch_all_articles(keywords, existing_keys, max_workers)
    if not tasks:
        print("❌ 所有关键词均未找到新闻")
        return []
    print(f"\n📊 共收集 {len(tasks)} 篇文章，开始并行处理...\n")

    # Phase 2: Parallel process
    results = []
    lock = threading.Lock()
    done_count = [0]  # mutable for closure

    def _process(task):
        key, news = process_article(task)
        with lock:
            done_count[0] += 1
            status = '✅' if not news.get('_skip') else '⏭️ 跳过'
            print(f"  [{done_count[0]}/{len(tasks)}] [{task['keyword']}] {status} {news.get('title_zh', news['title_ja'])[:50]}")
        return key, news

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process, t): t for t in tasks}
        for f in as_completed(futures):
            try:
                key, news = f.result()
                results.append(news)
            except Exception as e:
                print(f"  ❌ 任务异常: {e}")

    print(f"\n📊 共处理 {len(tasks)} 条新闻，成功 {len(results)} 条")
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SQLite 并行抓取")
    parser.add_argument('--keywords', type=str, default='', help='JSON: [{"keyword":"AKB","max":10}]')
    parser.add_argument('--push', action='store_true', default=True, help='推送到存储后端')
    parser.add_argument('--workers', type=int, default=FETCH_PARALLEL, help=f'并行数(默认{FETCH_PARALLEL})')
    args = parser.parse_args()

    if not check_proxy():
        return
    if not check_chrome_cdp():
        return

    if args.keywords:
        import json
        keywords = json.loads(args.keywords)
    else:
        keywords = [{"keyword": kw, "max": mx, "china_filter": cf}
                    for kw, mx, cf in DEFAULT_KEYWORDS]

    run_parallel(keywords, args.workers)


if __name__ == '__main__':
    main()
