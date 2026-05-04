#!/usr/bin/env python3
"""
日本 Yahoo 新闻自动抓取器
- 通过 Chrome CDP 搜索关键词并抓取新闻
- 智能筛选中国相关新闻
- AI 翻译 + 内容生成
- 自动推送到 Notion 数据库
"""

import json
import re
import sys
import time
import argparse
from datetime import datetime
from typing import List, Dict

import requests
from bs4 import BeautifulSoup

# 公共模块（配置、AI、Notion、工具函数全在这里）
from yahoo_common import (
    LITELLM_API_KEY, LITELLM_MODEL, LITELLM_MAX_TOKENS, NOTION_DATABASE_ID,
    CDP_HOST, CDP_PORT,
    YAHOO_SEARCH_URL, YAHOO_BASE_URL,
    is_sensitive, is_china_related, extract_key_from_url,
    translate_title, generate_content_and_comment, auto_classify,
    fetch_article_details, upload_cover_image, process_news_item,
    load_today_keys, push_to_notion, push_with_gallery, push_stub_to_notion,
    check_chrome_cdp,
)

# ============ 默认任务配置 ============

# (关键词, 每次抓取数量, 是否开启中国相关过滤)
DEFAULT_KEYWORDS = [
    # ("中国",  3, True),
    ("AKB",    10, False),
    ("乃木坂",  5, False),
    ("欅坂",    3, False),
    ("コスプレ", 3, False),
    ("原神",    3, False),
    ("鳴潮",    3, False),
]

# 关键词 → 额外发布标签
KEYWORD_TAG_MAP: dict[str, list[str]] = {
    "AKB":     ["AKB48", "akb48"],
    "乃木坂":  ["乃木坂", "乃木坂46"],
    "欅坂":    ["欅坂", "欅坂46", "樱坂", "樱坂46"],
    "伊織もえ": ["伊織もえ", "伊织萌", "きゅるん"],
}


# ============ CDP 抓取 ============

def fetch_news_via_cdp(keyword: str, max_results: int = 5,
                       china_filter: bool = True,
                       existing_keys: set | None = None,
                       max_retries: int = 2) -> List[Dict]:
    """通过 CDP 导航到 Yahoo 搜索页并抓取新闻列表"""
    import websocket as _ws_module

    for attempt in range(max_retries + 1):
        if attempt > 0:
            print(f"  🔄 重试 ({attempt}/{max_retries})...")
            time.sleep(3)
        try:
            resp = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=10)
            if resp.status_code != 200:
                print("❌ 无法连接 Chrome")
                return []
            tabs = resp.json()
            if not tabs:
                return []
            ws_url = tabs[0].get("webSocketDebuggerUrl", "")
            if not ws_url:
                return []

            ws = _ws_module.create_connection(ws_url, timeout=15)
            try:
                ws.settimeout(15)
                ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
                ws.recv()

                url = f"{YAHOO_SEARCH_URL}?p={keyword}&ei=UTF-8"
                ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))

                print("等待页面加载...")
                start = time.time()
                while time.time() - start < 20:
                    msg = json.loads(ws.recv())
                    if msg.get("method") == "Page.loadEventFired":
                        break
                time.sleep(3)

                ws.send(json.dumps({
                    "id": 3,
                    "method": "Runtime.evaluate",
                    "params": {"expression": "document.documentElement.outerHTML"},
                }))
                html = ""
                while True:
                    msg = json.loads(ws.recv())
                    if msg.get("id") == 3:
                        html = msg.get("result", {}).get("result", {}).get("value", "")
                        break
            finally:
                try:
                    ws.close()
                except Exception:
                    pass

            if not html:
                continue

            soup = BeautifulSoup(html, "html.parser")
            news_list: list[dict] = []
            seen: set[str] = set()

            for link in soup.find_all('a'):
                if len(news_list) >= max_results * 2:
                    break
                href = link.get("href", "")
                if "/articles/" not in href or href in seen:
                    continue
                seen.add(href)

                title = link.get_text(strip=True)
                if len(title) < 15:
                    continue
                if china_filter and not is_china_related(title):
                    continue
                if is_sensitive(title):
                    continue

                full_link = href if href.startswith("http") else YAHOO_BASE_URL + href
                if existing_keys and extract_key_from_url(full_link) in existing_keys:
                    continue

                source = "Yahoo Japan"
                li = link.find_parent("li")
                if li:
                    date_re = re.compile(r'\d+/\d+|^\d+:\d+|^20\d\d')
                    for t in reversed([t.strip() for t in li.stripped_strings if t.strip()]):
                        if (2 < len(t) < 30 and not date_re.search(t)
                                and t not in title[:30] and '…' not in t and '。' not in t):
                            source = t
                            break

                news_list.append({"title_ja": title, "link": full_link, "source": source})

            return news_list[:max_results]

        except Exception as e:
            print(f"抓取出错: {e}")
            if attempt == max_retries:
                return []
    return []


# ============ 主处理流程 ============

def process_keyword(keyword: str, max_results: int, china_filter: bool,
                    no_translate: bool, existing_keys: set | None = None,
                    push: bool = False) -> List[Dict]:
    """抓取并处理单个关键词的新闻"""
    filter_desc = "筛选中国相关" if china_filter else "不过滤"
    print(f"\n{'━' * 60}")
    print(f"🔍 关键词: 【{keyword}】| {filter_desc} | 最多 {max_results} 条")
    print(f"{'━' * 60}")

    news_list = fetch_news_via_cdp(keyword, max_results, china_filter, existing_keys)
    if not news_list:
        print("  ❌ 未找到相关新闻")
        return []
    print(f"  ✅ 找到 {len(news_list)} 条\n")

    # 关键词 → 额外标签
    extra_tags: list[str] = []
    if keyword in KEYWORD_TAG_MAP:
        extra_tags = KEYWORD_TAG_MAP[keyword]
    elif keyword != '中国':
        extra_tags = [keyword]

    processed: list[dict] = []
    for i, news in enumerate(news_list, 1):
        print(f"  [{i}/{len(news_list)}] {news['title_ja'][:45]}...")
        process_news_item(news, no_translate=no_translate,
                          extra_tags=extra_tags, keyword=keyword)
        if news.get('_skip'):
            continue
        processed.append(news)

        if push:
            push_with_gallery(news, existing_keys)

    return processed


# ============ 入口 ============

def main():
    parser = argparse.ArgumentParser(description='日本 Yahoo 新闻自动抓取器')
    parser.add_argument('--push', '-p', action='store_true', help='自动推送到 Notion')
    parser.add_argument('--max', '-m', type=int, default=None, help='每个关键词最大抓取数量')
    parser.add_argument('--keyword', '-k', type=str, default=None, help='指定单个搜索关键词')
    parser.add_argument('--no-filter', action='store_true', help='关闭中国相关性过滤')
    parser.add_argument('--no-translate', action='store_true', help='跳过翻译')
    parser.add_argument('--auto', action='store_true', help='跳过预览，直接处理所有新闻')
    args = parser.parse_args()

    print("=" * 60)
    print("🇯🇵 日本 Yahoo 新闻自动抓取器")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}\n")

    if not LITELLM_API_KEY:
        print("❌ LiteLLM 未配置，请在 scripts/.env 中设置 LITELLM_API_KEY")
        return
    print(f"✅ LiteLLM 已配置  模型: {LITELLM_MODEL}  max_tokens: {LITELLM_MAX_TOKENS}")
    print("📡 检查 Chrome CDP...")
    if not check_chrome_cdp():
        return

    # 构建任务列表
    if args.keyword:
        china_filter = not args.no_filter and args.keyword == '中国'
        tasks = [(args.keyword, args.max or 5, china_filter)]
    else:
        tasks = [(kw, args.max or cnt, cf) for kw, cnt, cf in DEFAULT_KEYWORDS]

    existing_keys = load_today_keys()

    # ── 预览模式（默认）：先拉标题，让用户勾选 ──────────────────
    if not args.auto:
        all_candidates: list[dict] = []
        for keyword, max_results, china_filter in tasks:
            print(f"\n🔍 关键词: 【{keyword}】")
            candidates = fetch_news_via_cdp(keyword, max_results, china_filter, existing_keys)
            for news in candidates:
                news['keyword'] = keyword
                print(f"    翻译: {news['title_ja'][:40]}...")
                news['title_zh'] = translate_title(news['title_ja'])
            all_candidates.extend(candidates)

        if not all_candidates:
            print("\n❌ 未找到任何新闻")
            return

        print(f"\n{'─' * 60}")
        print(f"📋 共找到 {len(all_candidates)} 条新闻，请选择要处理的编号：")
        print(f"{'─' * 60}")
        for i, news in enumerate(all_candidates, 1):
            kw = news.get('keyword', '')
            print(f"  [{i:2d}] [{kw}] {news.get('title_zh', news['title_ja'])[:40]}")
            print(f"        {news['link']}")
        print(f"{'─' * 60}")
        print("输入编号（逗号分隔，如 1,3,5），输入 all 选全部，回车取消：")

        raw = input("> ").strip()
        if not raw:
            print("已取消")
            return
        if raw.lower() == 'all':
            selected = all_candidates
            unselected = []
        else:
            try:
                indices = {int(x.strip()) - 1 for x in raw.split(',')}
                selected   = [all_candidates[i] for i in sorted(indices) if 0 <= i < len(all_candidates)]
                unselected = [n for i, n in enumerate(all_candidates) if i not in indices]
            except ValueError:
                print("❌ 输入格式有误")
                return

        if not selected:
            print("未选择任何条目")
            return

        # 未选中的新闻推送存根（仅去重用）
        if args.push and unselected:
            print(f"\n  归档未选中条目（{len(unselected)} 条）...")
            for news in unselected:
                push_stub_to_notion(news, existing_keys)

        print(f"\n✅ 已选 {len(selected)} 条，开始处理...\n")
        all_processed: list[dict] = []
        for news in selected:
            kw = news.get('keyword', '')
            extra_tags = KEYWORD_TAG_MAP.get(kw, [kw] if kw and kw != '中国' else [])
            print(f"  处理: {news['title_ja'][:50]}...")
            process_news_item(news, no_translate=args.no_translate,
                              extra_tags=extra_tags, keyword=kw)
            if news.get('_skip'):
                continue
            all_processed.append(news)
            if args.push:
                push_with_gallery(news, existing_keys)

    # ── 自动模式（--auto）────────────────────────────────────────
    else:
        all_processed: list[dict] = []
        for keyword, max_results, china_filter in tasks:
            results = process_keyword(keyword, max_results, china_filter,
                                      args.no_translate, existing_keys, args.push)
            all_processed.extend(results)

    if not all_processed:
        print("\n❌ 所有关键词均未找到新闻")
        return

    print(f"\n{'=' * 60}")
    print(f"📊 共处理 {len(all_processed)} 条新闻")

    if args.push:
        print(f"✅ 完成！已推送 {len(all_processed)} 条")
        print(f"🔗 查看: https://www.notion.so/{NOTION_DATABASE_ID}")
    else:
        print("使用 --push 或 -p 参数自动推送到 Notion")
        print("=" * 60)
        for i, news in enumerate(all_processed, 1):
            print(f"[{i}] [{news.get('keyword', '')}] {news.get('title_zh', news['title_ja'])[:40]}...")
            print(f"     分类: {news['category']} | {', '.join(news['tags'][:3])}")


if __name__ == "__main__":
    main()
