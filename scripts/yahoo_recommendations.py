#!/usr/bin/env python3
"""
Yahoo Japan 推荐内容抓取器
- 通过 Chrome CDP 读取已登录 Chrome 的「あなたにおすすめ」个性化推荐
- 后续处理（翻译、AI 生成、分类、封面图、Notion 推送）与 yahoo_news_auto.py 完全一致
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

# 公共模块
from yahoo_common import (
    LITELLM_API_KEY, NOTION_DATABASE_ID,
    CDP_HOST, CDP_PORT,
    YAHOO_BASE_URL, YAHOO_HOME_URL,
    is_sensitive, is_china_related, extract_key_from_url,
    process_news_item,
    load_today_keys, push_with_gallery,
    check_chrome_cdp, get_yahoo_tab_ws_url,
)


# ============ CDP 抓取「あなたにおすすめ」============

def fetch_recommendations_via_cdp(max_results: int = 20) -> List[Dict]:
    """通过 CDP 读取已登录 Chrome 中 Yahoo 首页的个性化推荐。

    策略：
      1. 如果已有 Yahoo 首页 tab → 直接读取 HTML
      2. 否则导航到首页，断线后重连再读取
    """
    try:
        import websocket
    except ImportError:
        print("❌ 需要安装: pip install websocket-client")
        return []

    ws_url, already_on_yahoo = get_yahoo_tab_ws_url()
    if not ws_url:
        print("❌ 找不到可用的 page tab")
        return []

    html = ""
    try:
        if already_on_yahoo:
            print("  ✅ 已在 Yahoo 首页，直接读取内容")
            ws = websocket.create_connection(ws_url)
            ws.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": "document.documentElement.outerHTML"},
            }))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 1:
                    html = msg.get("result", {}).get("result", {}).get("value", "")
                    break
            ws.close()
        else:
            # 导航到首页，连接可能因页面跳转而断开
            ws = websocket.create_connection(ws_url)
            ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            ws.recv()
            ws.send(json.dumps({
                "id": 2, "method": "Page.navigate",
                "params": {"url": YAHOO_HOME_URL},
            }))
            print("等待页面加载...")
            start = time.time()
            while time.time() - start < 20:
                try:
                    msg = json.loads(ws.recv())
                    if msg.get("method") == "Page.loadEventFired":
                        break
                except Exception:
                    break
            ws.close()

            # 等待个性化内容渲染，然后重连读取
            time.sleep(4)
            ws_url2, _ = get_yahoo_tab_ws_url()
            if not ws_url2:
                print("❌ 无法重新获取 WebSocket URL")
                return []
            ws2 = websocket.create_connection(ws_url2)
            ws2.send(json.dumps({
                "id": 1, "method": "Runtime.evaluate",
                "params": {"expression": "document.documentElement.outerHTML"},
            }))
            while True:
                msg = json.loads(ws2.recv())
                if msg.get("id") == 1:
                    html = msg.get("result", {}).get("result", {}).get("value", "")
                    break
            ws2.close()

    except Exception as e:
        print(f"抓取出错: {e}")
        return []

    if not html:
        print("❌ 获取页面 HTML 失败")
        return []

    # ── 解析 #newsFeed ────────────────────────────────
    soup = BeautifulSoup(html, "html.parser")
    feed = soup.find(id="newsFeed")
    if not feed:
        print("❌ 未找到 #newsFeed 容器（页面可能未正常加载）")
        return []

    items = feed.find_all("li", attrs={"data-ual-view-type": "list"})
    print(f"  ✅ 找到 {len(items)} 条「あなたにおすすめ」条目")

    news_list: list[dict] = []
    seen: set[str] = set()
    for li in items:
        if len(news_list) >= max_results:
            break

        a = li.find("a", href=re.compile(r"/articles/"))
        if not a:
            continue
        href = a.get("href", "")
        if href in seen:
            continue
        seen.add(href)

        # 标题
        title_el = li.find("div", class_=re.compile(r"sc-3ls169"))
        title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
        if len(title) < 5:
            continue

        # 来源
        source_el = li.find("span", class_=re.compile(r"XhYZV"))
        source = source_el.get_text(strip=True) if source_el else "Yahoo Japan"

        # 时间
        time_el = li.find("time")
        pub_time = time_el.get_text(strip=True) if time_el else ""

        # 缩略图（取 jpeg srcset 首个 URL）
        img_src = ""
        pic_source = li.find("source", attrs={"type": "image/jpeg"})
        if pic_source:
            srcset = pic_source.get("srcset", "")
            img_src = srcset.split(",")[0].strip().split(" ")[0]
        if not img_src:
            img_el = li.find("img")
            if img_el:
                img_src = img_el.get("src", "")

        full_link = href if href.startswith("http") else YAHOO_BASE_URL + href
        news_list.append({
            "title_ja":  title,
            "link":      full_link,
            "source":    source,
            "pub_time":  pub_time,
            "image_url": img_src,
        })

    return news_list


def fetch_recommendations_fallback() -> List[Dict]:
    """备用 HTTP 方法（无登录态，通常无法获取个性化推荐）"""
    print("尝试备用方法获取推荐内容...")
    try:
        resp = requests.get(YAHOO_HOME_URL, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
        }, timeout=15)
        if resp.status_code != 200:
            print(f"HTTP请求失败: {resp.status_code}")
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        feed = soup.find(id="newsFeed")
        if not feed:
            print("⚠️ 未找到 #newsFeed（备用HTTP方式无法获取登录后的个性化推荐）")
            return []

        items = feed.find_all("li", attrs={"data-ual-view-type": "list"})
        print(f"找到 {len(items)} 个 newsFeed 条目")

        news_list: list[dict] = []
        seen: set[str] = set()
        for li in items:
            if len(news_list) >= 20:
                break
            a = li.find("a", href=re.compile(r"/articles/"))
            if not a:
                continue
            href = a.get("href", "")
            if href in seen:
                continue
            seen.add(href)

            title_el = li.find("div", class_=re.compile(r"sc-3ls169"))
            title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
            if len(title) < 5:
                continue

            source_el = li.find("span", class_=re.compile(r"XhYZV"))
            source   = source_el.get_text(strip=True) if source_el else "Yahoo Japan"
            time_el  = li.find("time")
            pub_time = time_el.get_text(strip=True) if time_el else ""

            img_src = ""
            pic_source = li.find("source", attrs={"type": "image/jpeg"})
            if pic_source:
                img_src = pic_source.get("srcset", "").split(",")[0].strip().split(" ")[0]
            if not img_src:
                img_el = li.find("img")
                if img_el:
                    img_src = img_el.get("src", "")

            full_link = href if href.startswith("http") else YAHOO_BASE_URL + href
            news_list.append({
                "title_ja": title, "link": full_link,
                "source": source, "pub_time": pub_time, "image_url": img_src,
            })

        print(f"✅ 备用方法提取 {len(news_list)} 条新闻")
        return news_list

    except Exception as e:
        print(f"备用方法失败: {e}")
        return []


# ============ 主处理流程 ============

def process_recommendations(max_results: int = 20, no_filter: bool = False,
                              no_translate: bool = False,
                              existing_keys: set | None = None,
                              push: bool = False) -> List[Dict]:
    """抓取「あなたにおすすめ」列表，对每条新闻执行与 yahoo_news_auto 相同的处理流程"""
    filter_desc = "筛选中国相关" if not no_filter else "不过滤"
    print(f"\n{'━' * 60}")
    print(f"🔍 推荐内容抓取 | {filter_desc} | 最多 {max_results} 条")
    print(f"{'━' * 60}")

    news_list = fetch_recommendations_via_cdp(max_results)
    if not news_list:
        print("CDP 方法失败，尝试备用方法...")
        news_list = fetch_recommendations_fallback()
    if not news_list:
        print("❌ 未找到推荐内容")
        return []

    print(f"  ✅ 找到 {len(news_list)} 条推荐新闻\n")

    processed: list[dict] = []
    for i, news in enumerate(news_list, 1):
        print(f"  [{i}/{len(news_list)}] {news['title_ja'][:50]}...")

        if is_sensitive(news['title_ja']):
            print("    ⚠️ 包含敏感内容，跳过")
            continue
        if not no_filter and not is_china_related(news['title_ja']):
            print("    ⚠️ 不符合中国相关标准，跳过")
            continue

        key = extract_key_from_url(news['link'])
        if existing_keys and key in existing_keys:
            print("    ⚠️ 已存在，跳过")
            continue

        # 与 yahoo_news_auto 完全相同的后处理：翻译→AI→分类→文章详情→封面图
        process_news_item(news, no_translate=no_translate)
        processed.append(news)

        if push:
            push_with_gallery(news, existing_keys)

    return processed


# ============ 入口 ============

def main():
    parser = argparse.ArgumentParser(description='Yahoo Japan 推荐内容抓取器')
    parser.add_argument('--push', '-p', action='store_true', help='自动推送到 Notion')
    parser.add_argument('--max', '-m', type=int, default=20, help='最大抓取数量（默认20）')
    parser.add_argument('--no-filter', action='store_true', help='关闭中国相关性过滤')
    parser.add_argument('--no-translate', action='store_true', help='跳过翻译')
    parser.add_argument('--output', '-o', type=str, help='结果输出到 JSON 文件')
    args = parser.parse_args()

    print("=" * 60)
    print("📰 Yahoo Japan 推荐内容抓取器")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}\n")

    print("✅ LiteLLM 已配置" if LITELLM_API_KEY else "⚠️ LiteLLM 未配置，将跳过翻译")
    print("📡 检查 Chrome CDP...")
    if not check_chrome_cdp():
        return 1

    existing_keys = load_today_keys() if args.push else set()

    results = process_recommendations(
        max_results=args.max,
        no_filter=args.no_filter,
        no_translate=args.no_translate,
        existing_keys=existing_keys,
        push=args.push,
    )

    if not results:
        print("\n❌ 未找到推荐内容")
        return 1

    print(f"\n{'=' * 60}")
    print(f"📊 共处理 {len(results)} 条新闻")

    if args.push:
        print(f"✅ 完成！已推送 {len(results)} 条")
        print(f"🔗 查看: https://www.notion.so/{NOTION_DATABASE_ID}")
    else:
        print("使用 --push 或 -p 参数自动推送到 Notion")

    if args.output:
        import json as _json, os
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, 'w', encoding='utf-8') as f:
            _json.dump(results, f, ensure_ascii=False, indent=2)
        print(f"✅ 结果已保存: {args.output}")

    print("=" * 60)
    for i, news in enumerate(results, 1):
        title = news.get('title_zh', news['title_ja'])
        print(f"[{i}] {title[:60]}...")
        print(f"     分类: {news.get('category', '一般')} | {', '.join(news.get('tags', [])[:3])}")
        print(f"     链接: {news['link']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
