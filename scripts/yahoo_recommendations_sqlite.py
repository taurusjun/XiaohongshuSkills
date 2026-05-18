#!/usr/bin/env python3
"""Yahoo 推荐抓取 — SQLite 并行版
CDP 串行收集 → ThreadPoolExecutor 并行处理。
不影响 Notion 路径（yahoo_recommendations.py 保持不变）。
"""
import sys, os, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from concurrent.futures import ThreadPoolExecutor, as_completed
from config.yahoo_conf import STORAGE_BACKEND, FETCH_PARALLEL

# Thread-local stdout wrapper: prepends [key] prefix to every line
_log_ctx = threading.local()
_log_ctx.prefix = ""

class _PrefixedStdout:
    def __init__(self, real): self._real = real
    def write(self, s):
        p = getattr(_log_ctx, 'prefix', '')
        if p and s.strip():
            for line in s.splitlines(True):
                self._real.write((p + line) if line.strip() else line)
        else:
            self._real.write(s)
    def flush(self): self._real.flush()
    def __getattr__(self, a): return getattr(self._real, a)

sys.stdout = _PrefixedStdout(sys.stdout)

from yahoo_recommendations import fetch_recommendations_via_cdp
from yahoo_common import (
    process_news_item, push_with_gallery, load_today_keys,
    extract_key_from_url, check_chrome_cdp, check_proxy,
    _disable_proxy, LITELLM_API_KEY, LITELLM_MODEL,
)


def run_parallel(max_results=20, max_workers=3):
    print(f"\n🚀 SQLite 推荐并行抓取 | max={max_results} | workers={max_workers}")
    print(f"   模型={LITELLM_MODEL} | 后端={STORAGE_BACKEND}")

    print("📋 加载去重 key...")
    existing_keys = load_today_keys()

    # Phase 1: Serial CDP fetch
    print(f"\n{'━' * 60}")
    print(f"📰 推荐抓取 | 最多 {max_results} 条")
    print(f"{'━' * 60}")
    _log_ctx.prefix = "[recom] "
    articles = fetch_recommendations_via_cdp(max_results)
    _log_ctx.prefix = ""
    print(f"  ✅ 找到 {len(articles)} 条\n")

    if not articles:
        print("❌ 未找到推荐新闻")
        return []

    # Build tasks, dedup
    tasks = []
    seen = set()
    for a in articles:
        key = extract_key_from_url(a['link'])
        if key not in seen and key not in existing_keys:
            seen.add(key)
            tasks.append({'news': a, 'keyword': 'recomm'})
    print(f"📊 去重后 {len(tasks)} 篇文章，开始并行处理...\n")

    # Phase 2: Parallel processing
    results = []
    lock = threading.Lock()
    done = [0]

    def _process(task):
        news = task['news']
        key = extract_key_from_url(news['link'])
        _log_ctx.prefix = f"[{key[:8]}] "
        try:
            process_news_item(news, extra_tags=["日本新闻"], keyword="")
            if not news.get('_skip'):
                push_with_gallery(news)
        finally:
            _log_ctx.prefix = ""
        with lock:
            done[0] += 1
            s = '✅' if not news.get('_skip') else '⏭️'
            t = news.get('title_zh', news.get('title_ja',''))[:40]
            print(f"[{done[0]}/{len(tasks)}] [{key[:8]}] {s} {t}")
        return key, news

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process, t): t for t in tasks}
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                print(f"  ❌ 任务异常: {e}")

    print(f"\n📊 共处理 {len(tasks)} 条新闻，成功 {len(results)} 条")
    return results


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SQLite 推荐并行抓取")
    parser.add_argument('--max', type=int, default=20)
    parser.add_argument('--push', action='store_true', default=True)
    parser.add_argument('--workers', type=int, default=FETCH_PARALLEL)
    args = parser.parse_args()

    if not check_proxy(): return
    if not check_chrome_cdp(): return

    run_parallel(args.max, args.workers)


if __name__ == '__main__':
    main()
