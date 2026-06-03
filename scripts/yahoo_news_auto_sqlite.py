#!/usr/bin/env python3
"""Yahoo News 并行抓取器 — SQLite 专用版
收集所有 keyword 的文章列表后，用 ThreadPoolExecutor 并行处理每篇文章。
每行输出自动带 [key前12位] 前缀区分来源。
不影响 Notion 路径（yahoo_news_auto.py 保持不变）。
"""
import sys, os, time, threading
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))

from concurrent.futures import ThreadPoolExecutor, as_completed
from config.yahoo_conf import STORAGE_BACKEND, FETCH_PARALLEL

# Thread-local stdout wrapper: prepends [key_prefix] to every line
_log_ctx = threading.local()
_log_ctx.prefix = ""

class _PrefixedStdout:
    # L-1: replaces sys.stdout globally; prefix is thread-local so safe under threading.
    # __getattr__ delegates buffer/fileno/etc to the real stdout for binary-write compatibility.
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

# Reuse CDP fetch from original script
from yahoo_news_auto import (
    fetch_news_via_cdp, KEYWORD_TAG_MAP
)
from yahoo_common import (
    process_news_item, push_with_gallery,
    extract_key_from_url, check_chrome_cdp, check_proxy,
    _disable_proxy, LITELLM_API_KEY, LITELLM_MODEL,
)


def batch_get_existing_keys(keys: set) -> set:
    """批量查询 DB 中已存在的 key"""
    if not keys:
        return set()
    from sqlite_db import _connect
    existing = set()
    key_list = list(keys)
    with _connect() as db:
        for i in range(0, len(key_list), 500):
            batch = key_list[i:i+500]
            placeholders = ','.join('?' * len(batch))
            rows = db.execute(f"SELECT key FROM news WHERE key IN ({placeholders})", batch).fetchall()
            for r in rows:
                existing.add(r['key'])
    return existing


def fetch_all_articles(keywords, max_workers):
    """串行收集所有 keyword 的文章（CDP 共享一个 tab，不能并行搜索）。
    返回 (tasks, all_collected_keys)"""
    tasks = []
    all_keys = set()
    seen_keys = set()

    retry_queue = []
    for kw in keywords:
        k, mx, cf = kw['keyword'], kw.get('max', 10), kw.get('china_filter', False)
        angle = kw.get('angle', '')
        print(f"\n{'━' * 60}")
        print(f"🔍 关键词: 【{k}】| 最多 {mx} 条" + (f" | 角度: {angle[:30]}" if angle else ""))
        print(f"{'━' * 60}")
        _log_ctx.prefix = f"[{k}] "
        articles = fetch_news_via_cdp(k, mx, cf)
        _log_ctx.prefix = ""
        print(f"  ✅ [{k}] 找到 {len(articles)} 条\n")
        if len(articles) == 0:
            retry_queue.append(kw)
            continue
        tags = [k] + (KEYWORD_TAG_MAP.get(k, []) or [])
        for a in articles:
            key = extract_key_from_url(a['link'])
            all_keys.add(key)
            if key not in seen_keys:
                seen_keys.add(key)
                a['_angle'] = angle
                tasks.append({'news': a, 'keyword': k, 'extra_tags': tags})

    # Retry failed keywords once（页面加载问题可能导致0条）
    for kw in retry_queue:
        k, mx, cf = kw['keyword'], kw.get('max', 10), kw.get('china_filter', False)
        angle = kw.get('angle', '')
        print(f"\n{'━' * 60}")
        print(f"🔁 重试: 【{k}】| 最多 {mx} 条" + (f" | 角度: {angle[:30]}" if angle else ""))
        print(f"{'━' * 60}")
        _log_ctx.prefix = f"[{k}] "
        articles = fetch_news_via_cdp(k, mx, cf)
        _log_ctx.prefix = ""
        print(f"  {'✅' if articles else '❌'} [{k}] 找到 {len(articles)} 条\n")
        tags = [k] + (KEYWORD_TAG_MAP.get(k, []) or [])
        for a in articles:
            key = extract_key_from_url(a['link'])
            all_keys.add(key)
            if key not in seen_keys:
                seen_keys.add(key)
                a['_angle'] = angle
                tasks.append({'news': a, 'keyword': k, 'extra_tags': tags})

    return tasks, all_keys


def process_article(task):
    """处理单篇文章（线程安全）"""
    news = task['news']
    keyword = task['keyword']
    extra_tags = task['extra_tags']
    key = extract_key_from_url(news['link'])

    _log_ctx.prefix = f"[{keyword}/{key[:8]}] "
    try:
        process_news_item(news, no_translate=False, extra_tags=extra_tags, keyword=keyword)
        if not news.get('_skip'):
            push_with_gallery(news)
    finally:
        _log_ctx.prefix = ""

    return key, news


def run_parallel(keywords, max_workers=3):
    """主入口"""
    print(f"\n🚀 SQLite 并行抓取 | keywords={len(keywords)} | workers={max_workers}")
    print(f"   模型={LITELLM_MODEL} | 后端={STORAGE_BACKEND}")

    tasks, all_keys = fetch_all_articles(keywords, max_workers)
    if not tasks:
        print("❌ 所有关键词均未找到新闻")
        return []

    # 批量查 DB 去重
    print(f"📋 批量去重: 收集 {len(all_keys)} 个 key...")
    existing_keys = batch_get_existing_keys(all_keys)
    print(f"   已存在: {len(existing_keys)} 篇，新文章: {len(all_keys) - len(existing_keys)} 篇")

    # Filter out existing
    new_tasks = [t for t in tasks if extract_key_from_url(t['news']['link']) not in existing_keys]
    if not new_tasks:
        print("✅ 所有文章均已存在，无需处理")
        return []
    print(f"\n📊 共收集 {len(new_tasks)} 篇新文章，开始并行处理...\n")

    results = []
    lock = threading.Lock()
    done = [0]

    def _process(task):
        kw = task['keyword']
        key, news = process_article(task)
        with lock:
            done[0] += 1
            s = '✅' if not news.get('_skip') else '⏭️'
            t = news.get('title_zh', task['news'].get('title_ja',''))[:40]
            print(f"[{done[0]}/{len(new_tasks)}] [{kw}/{key[:8]}] {s} {t}")
        return key, news

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_process, t): t for t in new_tasks}
        for f in as_completed(futures):
            try:
                key, news = f.result()
                results.append(news)
            except Exception as e:
                print(f"  ❌ 任务异常: {e}")

    print(f"\n📊 共处理 {len(new_tasks)} 条新闻，成功 {len(results)} 条")
    return results


def main():
    import argparse, json
    parser = argparse.ArgumentParser(description="SQLite 并行抓取")
    parser.add_argument('--keywords', type=str, default='', help='JSON: [{"keyword":"AKB","max":10}]')
    parser.add_argument('--push', action='store_true', default=True)
    parser.add_argument('--workers', type=int, default=FETCH_PARALLEL, help=f'并行数(默认{FETCH_PARALLEL})')
    args = parser.parse_args()

    if not check_proxy(): return
    if not check_chrome_cdp(): return

    if args.keywords:
        keywords = json.loads(args.keywords)
    else:
        # 从 agent_config 读取配置，不再硬编码 DEFAULT_KEYWORDS
        from scripts.sqlite_db import get_config
        topics = get_config("focus_topics", default=[])
        kw_map = get_config("yahoo_keyword_map", default={})
        daily = get_config("daily_quota", default=5)
        keywords = []
        for t in topics:
            cfg = kw_map.get(t, {"keyword": t, "max": daily})
            keywords.append({"keyword": cfg.get("keyword", t), "max": cfg.get("max", daily)})

    run_parallel(keywords, args.workers)


if __name__ == '__main__':
    main()
