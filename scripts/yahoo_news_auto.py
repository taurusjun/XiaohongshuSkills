#!/usr/bin/env python3
"""
日本 Yahoo 新闻自动抓取器
- 通过 Chrome CDP 抓取中国相关新闻
- 智能筛选与中国直接相关的新闻
- AI 翻译标题和生成评论解读
- 自动推送到 Notion 数据库
"""

import requests
from bs4 import BeautifulSoup
import json
import re
from datetime import datetime
from typing import List, Dict, Tuple
import time
import os
import sys
import argparse

# 加载 .env 文件
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

# ============ 配置 ============

# Notion 配置
NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

# LiteLLM 配置
LITELLM_URL = os.environ.get("LITELLM_URL", "https://litellm-prod.toolsfdg.net")
LITELLM_API_KEY = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL = os.environ.get("LITELLM_MODEL", "GLM-5")
CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

# Yahoo 配置
YAHOO_SEARCH_URL = "https://news.yahoo.co.jp/search"
YAHOO_BASE_URL = "https://news.yahoo.co.jp"

# 敏感词
SENSITIVE_KEYWORDS = ["天安门", "六四", "法轮功", "藏独", "疆独", "杀人预告", "爆破预告", "皆殺し"]

# 中国相关关键词（必须包含才算相关）
CHINA_KEYWORDS = [
    # 中国名称
    "中国", "中華", "北京", "上海", "深圳", "広州", "習近平", "王毅", "李強",
    # 中国企业
    "BYD", "比亚迪", "華為", "华为", "阿里", "テンセント", "腾讯", "吉利", "小鵬", "蔚来",
    "テスラ中国", "特斯拉上海", "中国製造", "中国工場",
    # 中日关系
    "日中", "中日", "訪中", "訪問中国", "中国外務省", "中国大使館",
    # 其他
    "台湾", "香港", "ホルムズ", "イラン", "北朝鮮"
]

# 日本地方关键词（需要排除）
JAPAN_REGION_KEYWORDS = [
    "中国電力", "中国銀行", "中国放送", "中国地方", "中国新聞",
    "広島", "岡山", "山口", "鳥取", "島根", "中国運輸", "RCC"
]


# ============ 工具函数 ============

def is_sensitive(text: str) -> bool:
    """检查敏感内容"""
    return any(kw in text for kw in SENSITIVE_KEYWORDS)


def is_china_related(title: str) -> bool:
    """判断是否与中国相关"""
    # 排除日本中国地方
    has_japan_region = any(kw in title for kw in JAPAN_REGION_KEYWORDS)
    if has_japan_region:
        # 除非同时包含明确的中国指标
        has_china_indicator = any(kw in title for kw in ["中国本土", "中国人", "中国経済", "中国政府", "BYD", "テスラ中国"])
        if not has_china_indicator:
            return False

    # 必须包含中国相关关键词
    return any(kw in title for kw in CHINA_KEYWORDS)


def call_litellm(prompt: str, system_prompt: str = "", max_tokens: int = 1000) -> str:
    """调用 LiteLLM API"""
    if not LITELLM_API_KEY:
        return ""

    try:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LITELLM_API_KEY}"
        }

        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": LITELLM_MODEL,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0.7
        }

        resp = requests.post(
            f"{LITELLM_URL}/chat/completions",
            headers=headers,
            json=payload,
            timeout=60
        )

        if resp.status_code == 200:
            result = resp.json()
            message = result.get("choices", [{}])[0].get("message", {})
            # GLM-5 推理模型返回 reasoning_content，普通模型返回 content
            content = message.get("content") or message.get("reasoning_content", "")
            if content:
                return content.strip()
            return ""
        else:
            print(f"    ⚠️ LiteLLM 错误: {resp.status_code}")
            return ""

    except Exception as e:
        print(f"    ⚠️ LiteLLM 调用失败: {e}")
        return ""


def translate_title(title_ja: str) -> str:
    """翻译标题"""
    prompt = f"""请将以下日文新闻标题翻译成简洁的中文。

要求：
1. 只输出翻译结果
2. 不要添加任何解释
3. 保持简洁

日文标题：{title_ja}

中文翻译："""

    result = call_litellm(prompt, max_tokens=1000)
    # 清理结果，提取最终翻译
    if result:
        # 尝试找到翻译结果（通常在最后部分）
        lines = result.strip().split('\n')
        # 从后往前找有意义的行
        for line in reversed(lines):
            line = line.strip()
            # 跳过分析性文字和标号
            if line and not line.startswith(('分析', '翻译', '1.', '2.', '3.', '*', '**', '-', '源文本', '目标')):
                # 清理可能的前缀
                line = line.replace('中文翻译：', '').replace('翻译结果：', '').strip()
                if len(line) > 3 and len(line) < 150:
                    return line
    return title_ja


def generate_content_and_comment(title_ja: str, title_zh: str) -> Tuple[str, str, str, str, str]:
    """生成一句话总结、新闻要点、评论、SEO优化标题和N1/N2词汇"""
    prompt = f"""你是小红书内容运营专家兼日语教学专家。请根据新闻标题，严格按照下方格式输出全部6个字段。

新闻标题：{title_zh}
日文原文：{title_ja}

输出格式（必须包含全部6个字段）：

【SEO标题】
（严格控制在20字符以内！加入情绪词如"慌了/震惊/破防"，加入数字，吸引点击）

【总结】
（15-25字，概括新闻核心，吸引读者继续看）

【新闻要点】
• （要点1，一句话说清楚）
• （要点2，一句话说清楚）
• （要点3，一句话说清楚）
• （要点4，可选）

【我的解读】
（80-120字，从中国视角分析这条新闻的意义，语气口语化，像在和朋友聊天）

【N1/N2词汇】
（从新闻内容推测可能出现的词汇，提取3个N1或N2级别日语词汇，格式如下）
1. 单词 (假名) [词性] 中文释义
   例句：简单的日语例句

【话题标签】
（5-8个标签，#开头。优先从以下热门标签中选择合适的：
日语学习类：#日语学习 #日语N1 #日语N2 #日语单词
中日双语类：#中日双语 #中日对照 #中日翻译
日本资讯类：#日本新闻 #日本热点 #日本资讯 #看新闻学日语
综合类：#日语学习打卡 #日本文化 #日本生活
再根据内容补充行业相关标签）"""

    result = call_litellm(prompt, max_tokens=4000)

    if not result:
        return title_zh, "", f"• {title_zh}", "暂无解读", ""

    # 解析结果 — 按段落切割
    seo_title = title_zh
    summary = ""
    content = ""
    comment = ""
    vocab = ""

    sections = re.split(r'【(SEO标题|总结|新闻要点|我的解读|N1/N2词汇|话题标签)】', result)
    for i, sec in enumerate(sections):
        if sec == "SEO标题" and i + 1 < len(sections):
            seo_title = sections[i + 1].strip().split('\n')[0].strip()[:20]  # 强制截断20字符
        elif sec == "总结" and i + 1 < len(sections):
            summary = sections[i + 1].strip().split('\n')[0].strip()
        elif sec == "新闻要点" and i + 1 < len(sections):
            content = sections[i + 1].strip()
        elif sec == "我的解读" and i + 1 < len(sections):
            comment = sections[i + 1].strip()
        elif sec == "N1/N2词汇" and i + 1 < len(sections):
            vocab = sections[i + 1].strip()

    return seo_title, summary, content, comment, vocab


def auto_classify(title: str, content: str = "", keyword: str = "中国") -> Tuple[str, List[str]]:
    """自动分类和打标签"""
    category = "经济"
    tags = []

    # 只有内容真的涉及中国才加中国相关标签
    text_all = (title + " " + content)
    if any(k in text_all for k in CHINA_KEYWORDS):
        tags.append("中国相关")

    text = (title + " " + content).lower()

    # 分类
    if any(k in text for k in ["ev", "电动车", "汽车", "日产", "本田", "丰田", "比亚迪", "特斯拉", "サクラ", "インサイト", "byd", "吉利"]):
        category = "汽车"
        tags.append("EV电动车")
    elif any(k in text for k in ["旅游", "观光", "游客", "観光"]):
        category = "旅游"
    elif any(k in text for k in ["科技", "ai", "人工智能", "半导体", "芯片", "华为"]):
        category = "科技"
    elif any(k in text for k in ["サッカー", "足球", "スポーツ", "u-20", "代表"]):
        category = "体育"
    elif any(k in text for k in ["外相", "外務省", "会談", "訪問", "大使館", "イラン", "ホルムズ"]):
        category = "政治"
    elif any(k in text for k in ["akb", "idol", "アイドル", "芸能", "女優", "俳優", "歌手", "アニメ", "声優", "48", "乃木坂", "欅坂"]):
        category = "娱乐"

    # 品牌标签
    brand_map = {
        "特斯拉": ["テスラ", "tesla"],
        "比亚迪": ["byd", "比亚迪"],
        "本田": ["ホンダ", "honda", "インサイト"],
        "日产": ["日産", "nissan", "サクラ"],
        "丰田": ["トヨタ", "toyota"],
        "吉利": ["吉利", "geely"],
    }
    for tag, keywords in brand_map.items():
        if any(k in text for k in keywords):
            tags.append(tag)

    # 话题标签
    if "ホルムズ" in text or "霍尔木兹" in text:
        tags.append("中东局势")
    if "イラン" in text or "伊朗" in text:
        tags.append("伊朗")
    if "日中" in text or "中日" in text:
        tags.append("中日关系")
    if "韓中" in text or "中韩" in text:
        tags.append("中韩关系")

    return category, list(set(tags))


# ============ CDP 抓取 ============

def fetch_article_details(url: str) -> dict:
    """从 Yahoo 新闻文章页抓取封面图、原标题和摘要"""
    result = {"image_url": "", "original_title": "", "summary": ""}
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        # 封面图
        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            result["image_url"] = og["content"]

        # 原标题（og:title 或 h1）
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            result["original_title"] = og_title["content"]
        else:
            h1 = soup.find("h1")
            if h1:
                result["original_title"] = h1.get_text(strip=True)

        # 摘要（og:description 或 article 内首段）
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            result["summary"] = og_desc["content"]
        else:
            # 尝试从文章正文获取
            article = soup.find("article") or soup.find(class_="article")
            if article:
                p = article.find("p")
                if p:
                    result["summary"] = p.get_text(strip=True)[:300]

    except Exception as e:
        print(f"    ⚠️ 抓取文章详情失败: {e}")
    return result


def fetch_news_via_cdp(keyword: str = "中国", max_results: int = 5, china_filter: bool = True, existing_keys: set = None) -> List[Dict]:
    """通过 CDP 抓取新闻"""
    news_list = []

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

        try:
            import websocket
        except ImportError:
            print("❌ 需要安装: pip install websocket-client --break-system-packages")
            return []

        ws = websocket.create_connection(ws_url)

        try:
            ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            ws.recv()

            url = f"{YAHOO_SEARCH_URL}?p={keyword}&ei=UTF-8"
            ws.send(json.dumps({
                "id": 2,
                "method": "Page.navigate",
                "params": {"url": url}
            }))

            print("等待页面加载...")
            start = time.time()
            while time.time() - start < 15:
                msg = json.loads(ws.recv())
                if msg.get("method") == "Page.loadEventFired":
                    break

            time.sleep(3)

            ws.send(json.dumps({
                "id": 3,
                "method": "Runtime.evaluate",
                "params": {"expression": "document.documentElement.outerHTML"}
            }))

            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 3:
                    html = msg.get("result", {}).get("result", {}).get("value", "")
                    break

        finally:
            ws.close()

        if not html:
            return []

        soup = BeautifulSoup(html, "html.parser")
        all_links = soup.find_all('a')
        news_links = [a for a in all_links if '/articles/' in a.get('href', '')]

        print(f"找到 {len(news_links)} 个文章链接")

        seen = set()
        for link in news_links:
            if len(news_list) >= max_results * 2:
                break

            href = link.get("href", "")
            if href in seen:
                continue
            seen.add(href)

            title = link.get_text(strip=True)
            if len(title) < 15:
                continue

            # 筛选中国相关（可选）
            if china_filter and not is_china_related(title):
                continue

            # 检查敏感
            if is_sensitive(title):
                continue

            full_link = href if href.startswith("http") else YAHOO_BASE_URL + href

            # 检查是否已存在
            if existing_keys:
                key = extract_key_from_url(full_link)
                if key in existing_keys:
                    continue

            # 提取来源：li 内的媒体名称（排除日期时间格式）
            source = "Yahoo Japan"
            li = link.find_parent("li")
            if li:
                all_texts = [t.strip() for t in li.stripped_strings if t.strip()]
                # 来源通常是较短的文本，但不是日期/时间格式
                date_pattern = re.compile(r'\d+/\d+|^\d+:\d+|^20\d\d')
                for t in reversed(all_texts):
                    if (2 < len(t) < 30
                            and not date_pattern.search(t)
                            and t not in title[:30]
                            and '…' not in t
                            and '。' not in t):
                        source = t
                        break

            news_list.append({
                "title_ja": title,
                "link": full_link,
                "source": source
            })

    except Exception as e:
        print(f"抓取出错: {e}")

    return news_list[:max_results]


# ============ Notion 推送 ============

def parse_markdown_line(line: str) -> List[Dict]:
    """将含 **bold** 的 markdown 行转换为 Notion rich_text 格式"""
    rich_text = []
    parts = re.split(r'(\*\*.*?\*\*)', line)
    for part in parts:
        if part.startswith("**") and part.endswith("**"):
            rich_text.append({
                "type": "text",
                "text": {"content": part[2:-2]},
                "annotations": {"bold": True}
            })
        elif part:
            rich_text.append({
                "type": "text",
                "text": {"content": part}
            })
    return rich_text or [{"type": "text", "text": {"content": line}}]


def extract_key_from_url(url: str) -> str:
    """从 Yahoo 新闻 URL 提取文章 key"""
    m = re.search(r'/articles/([a-f0-9]+)', url)
    return m.group(1) if m else ""


def load_today_keys() -> set:
    """从 Notion 加载今天的 key"""
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # 获取今天的日期范围
    today = datetime.now().strftime('%Y.%m.%d')

    keys = set()
    has_more = True
    start_cursor = None

    while has_more:
        query = {
            "page_size": 100,
            "filter": {
                "property": "发布时间",
                "rich_text": {"equals": today}
            }
        }
        if start_cursor:
            query["start_cursor"] = start_cursor

        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=headers,
            json=query
        )
        if resp.status_code != 200:
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            rich = props.get("key", {}).get("rich_text", [])
            key = "".join(r.get("plain_text", "") for r in rich)
            if key:
                keys.add(key)

        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    print(f"📋 已加载今天的 {len(keys)} 个已存在 key")
    return keys


def is_duplicate(key: str) -> bool:
    """检查 key 是否已存在于 Notion 数据库"""
    if not key:
        return False
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=headers,
        json={"filter": {"property": "key", "rich_text": {"equals": key}}, "page_size": 1}
    )
    if resp.status_code == 200:
        return len(resp.json().get("results", [])) > 0
    return False


def push_to_notion(news: Dict) -> bool:
    """推送到 Notion"""
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28"
    }

    # 内容块
    blocks = []

    # 一句话总结（置顶）
    if news.get("summary"):
        blocks.append({
            "object": "block",
            "type": "callout",
            "callout": {
                "icon": {"type": "emoji", "emoji": "💡"},
                "rich_text": [{"type": "text", "text": {"content": news["summary"][:200]}}]
            }
        })
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}})

    # 新闻要点
    if news.get("content"):
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": "📰 新闻要点"}}]}
        })
        for line in news["content"].split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("•") or line.startswith("-"):
                text = line.lstrip("•- ").strip()
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": parse_markdown_line(text[:2000])}
                })
            else:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": parse_markdown_line(line[:2000])}
                })

    # 我的解读
    if news.get("comment"):
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": "💭 我的解读"}}]}
        })
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": news["comment"][:2000]}}]}
        })

    # N1/N2词汇
    if news.get("vocab"):
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        blocks.append({
            "object": "block",
            "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text", "text": {"content": "📝 今日词汇 (N1/N2)"}}]}
        })
        for line in news["vocab"].split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith("例句"):
                blocks.append({
                    "object": "block",
                    "type": "quote",
                    "quote": {"rich_text": [{"type": "text", "text": {"content": line}}]}
                })
            elif line[0].isdigit() and "." in line[:3]:
                # 词汇编号行
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[:500]}}]}
                })
            else:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[:500]}}]}
                })

    # 原文标题和摘要
    blocks.append({"object": "block", "type": "divider", "divider": {}})
    blocks.append({
        "object": "block",
        "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": "📰 原文"}}]}
    })

    # 日文原标题
    original_title = news.get("original_title", news["title_ja"])
    if original_title:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": original_title[:500]}}]}
        })

    # 日文摘要
    ja_summary = news.get("ja_summary", "")
    if ja_summary:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": ja_summary[:500]}}]}
        })

    # 原文链接
    blocks.append({"object": "block", "type": "divider", "divider": {}})
    blocks.append({
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": "🔗 原文链接：", "link": {"url": news["link"]}}}
        ]}
    })

    key = extract_key_from_url(news["link"])
    image_url = news.get("image_url", "")
    original_image_url = news.get("original_image_url", "")

    props = {
        "Name": {"title": [{"text": {"content": news["title_zh"][:100]}}]},
        "key": {"rich_text": [{"text": {"content": key}}]},
        "分类": {"select": {"name": news.get("category", "经济")}},
        "标签": {"multi_select": [{"name": t} for t in news.get("tags", [])]},
        "来源": {"rich_text": [{"text": {"content": news.get("source", "Yahoo Japan")}}]},
        "发布时间": {"rich_text": [{"text": {"content": news.get("pub_time", "")}}]},
        "原文链接": {"url": news["link"]},
    }
    if image_url:
        props["封面图"] = {"url": image_url}
    if original_image_url:
        props["原图链接"] = {"url": original_image_url}

    payload = {
        "parent": {"database_id": NOTION_DATABASE_ID},
        "properties": props,
        "children": blocks
    }

    # 设置页面封面图（Notion 页面顶部大图）
    if image_url:
        payload["cover"] = {"type": "external", "external": {"url": image_url}}

    resp = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    if resp.status_code != 200:
        print(f"    ⚠️ Notion 错误: {resp.status_code} {resp.text[:200]}")
    return resp.status_code == 200


# ============ 主程序 ============

# 默认搜索关键词配置：(关键词, 每次抓取数量, 是否开启中国相关过滤)
DEFAULT_KEYWORDS = [
    ("中国",  3, True),
    ("AKB48", 3, False),
    ("美人",  3, False),
    ("原神", 3, False),
    ("鳴潮", 3, False),
    ("コスプレ", 3, False),
]


def process_keyword(keyword: str, max_results: int, china_filter: bool, no_translate: bool, existing_keys: set = None, push: bool = False) -> List[Dict]:
    """抓取并处理单个关键词的新闻"""
    filter_desc = "筛选中国相关" if china_filter else "不过滤"
    print(f"\n{'━' * 60}")
    print(f"🔍 关键词: 【{keyword}】| {filter_desc} | 最多 {max_results} 条")
    print(f"{'━' * 60}")

    news_list = fetch_news_via_cdp(keyword=keyword, max_results=max_results, china_filter=china_filter, existing_keys=existing_keys)

    if not news_list:
        print(f"  ❌ 未找到相关新闻")
        return []

    print(f"  ✅ 找到 {len(news_list)} 条\n")

    processed = []
    for i, news in enumerate(news_list, 1):
        print(f"  [{i}/{len(news_list)}] {news['title_ja'][:45]}...")

        if LITELLM_API_KEY and not no_translate:
            print("    翻译...")
            news['title_zh'] = translate_title(news['title_ja'])
            print("    生成内容...")
            seo_title, summary, content, comment, vocab = generate_content_and_comment(news['title_ja'], news['title_zh'])
            news['title_zh'] = seo_title
            news['summary'] = summary
            news['content'] = content
            news['comment'] = comment
            news['vocab'] = vocab
        else:
            news['title_zh'] = news['title_ja']
            news['content'] = f"• {news['title_ja']}"
            news['comment'] = ""
            news['vocab'] = ""

        category, tags = auto_classify(news['title_ja'], news.get('content', ''), keyword=keyword)
        # 非中国关键词时，将关键词本身加入标签
        if keyword != '中国' and keyword not in tags:
            tags.append(keyword)
        news['category'] = category
        news['tags'] = tags
        news['source'] = news.get('source', 'Yahoo Japan')
        news['pub_time'] = datetime.now().strftime('%Y.%m.%d')
        news['keyword'] = keyword

        print("    抓取文章详情...")
        details = fetch_article_details(news['link'])
        image_url = details.get("image_url", "")
        news['original_title'] = details.get("original_title", news['title_ja'])
        news['ja_summary'] = details.get("summary", "")
        news['original_image_url'] = image_url
        if image_url:
            # 上传到 Cloudinary 获取永久链接
            try:
                from image_uploader import upload_image
                permanent_url = upload_image(image_url)
                if permanent_url:
                    news['image_url'] = permanent_url
                    print(f"    封面图: {permanent_url[:50]}...")
                else:
                    news['image_url'] = image_url
                    print(f"    封面图(原始): {image_url[:50]}...")
            except ImportError:
                news['image_url'] = image_url
                print(f"    封面图: {image_url[:50]}...")
        else:
            news['image_url'] = ""
            print("    封面图: ⚠️ 未找到")
        print(f"    分类: {category} | 标签: {', '.join(tags[:3])}")

        processed.append(news)

        # 立即推送到 Notion
        if push:
            key = extract_key_from_url(news["link"])
            if push_to_notion(news):
                print(f"    ✅ 已推送到 Notion")
                if existing_keys is not None:
                    existing_keys.add(key)
            else:
                print(f"    ❌ 推送失败")

    return processed


def main():
    parser = argparse.ArgumentParser(description='日本 Yahoo 新闻自动抓取器')
    parser.add_argument('--push', '-p', action='store_true', help='自动推送到 Notion')
    parser.add_argument('--max', '-m', type=int, default=None, help='每个关键词最大抓取数量（默认各3条）')
    parser.add_argument('--keyword', '-k', type=str, default=None, help='指定单个搜索关键词')
    parser.add_argument('--no-filter', action='store_true', help='关闭中国相关性过滤')
    parser.add_argument('--no-translate', action='store_true', help='跳过翻译')
    args = parser.parse_args()

    print("=" * 60)
    print("🇯🇵 日本 Yahoo 新闻自动抓取器")
    print("=" * 60)
    print(f"📅 {datetime.now().strftime('%Y.%m.%d %H:%M')}\n")

    if LITELLM_API_KEY:
        print("✅ LiteLLM 已配置")
    else:
        print("⚠️ LiteLLM 未配置，将跳过翻译和评论生成")

    print("📡 检查 Chrome CDP...")
    try:
        resp = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5)
        if resp.status_code == 200:
            print("✅ Chrome 已就绪")
        else:
            print("❌ Chrome 未响应\n")
            return
    except:
        print("❌ Chrome 未运行，请先启动: python scripts/chrome_launcher.py\n")
        return

    # 构建搜索任务列表
    if args.keyword:
        # 指定单个关键词
        china_filter = not args.no_filter and args.keyword == '中国'
        tasks = [(args.keyword, args.max or 5, china_filter)]
    else:
        # 默认多关键词模式
        tasks = [
            (kw, args.max or cnt, cf)
            for kw, cnt, cf in DEFAULT_KEYWORDS
        ]

    # 加载今天已存在的 keys（用于去重）
    existing_keys = load_today_keys() if args.push else set()

    # 逐个关键词抓取
    all_processed = []
    for keyword, max_results, china_filter in tasks:
        results = process_keyword(keyword, max_results, china_filter, args.no_translate, existing_keys, args.push)
        all_processed.extend(results)

    if not all_processed:
        print("\n❌ 所有关键词均未找到新闻")
        return

    print(f"\n{'=' * 60}")
    print(f"📊 共处理 {len(all_processed)} 条新闻")

    # 显示 Notion 链接
    if args.push:
        print(f"✅ 完成！已推送 {len(all_processed)} 条")
        print(f"🔗 查看: https://www.notion.so/{NOTION_DATABASE_ID}")
    else:
        print("使用 --push 或 -p 参数自动推送到 Notion")
        print("=" * 60)
        for i, news in enumerate(all_processed, 1):
            print(f"[{i}] [{news['keyword']}] {news['title_zh'][:40]}...")
            print(f"     分类: {news['category']} | {', '.join(news['tags'][:3])}")


if __name__ == "__main__":
    main()
