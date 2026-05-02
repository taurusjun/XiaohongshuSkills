#!/usr/bin/env python3
"""
yahoo_common.py — Yahoo Japan 新闻脚本公共模块

被 yahoo_news_auto.py 和 yahoo_recommendations.py 共同引用，包含：
- 配置常量（从 .env 读取）
- 过滤工具函数：is_sensitive / is_china_related
- AI 工具函数：call_litellm / translate_title / generate_content_and_comment
- 分类函数：auto_classify
- CDP 工具：get_yahoo_tab_ws_url
- 文章工具：fetch_article_details / extract_key_from_url
- Notion 工具：parse_markdown_line / load_today_keys / is_duplicate / push_to_notion
"""

import os
import re
import sys
import json
import time

import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Tuple

# ── .env ──────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass

# ============ 配置 ============

NOTION_API_KEY    = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

LITELLM_URL        = os.environ.get("LITELLM_URL", "https://litellm-prod.toolsfdg.net")
LITELLM_API_KEY    = os.environ.get("LITELLM_API_KEY", "")
LITELLM_MODEL      = os.environ.get("LITELLM_MODEL", "")
LITELLM_MAX_TOKENS = int(os.environ.get("LITELLM_MAX_TOKENS", "4000"))

if not LITELLM_MODEL:
    print("❌ 未配置 LITELLM_MODEL，请在 scripts/.env 中设置后重试")
    sys.exit(1)

CDP_HOST = "127.0.0.1"
CDP_PORT = 9222

YAHOO_BASE_URL   = "https://news.yahoo.co.jp"
YAHOO_SEARCH_URL = "https://news.yahoo.co.jp/search"
YAHOO_HOME_URL   = "https://news.yahoo.co.jp/"

# ── 关键词表 ──────────────────────────────────────────────
SENSITIVE_KEYWORDS = ["天安门", "六四", "法轮功", "藏独", "疆独", "杀人预告", "爆破预告", "皆殺し"]

CHINA_KEYWORDS = [
    "中国", "中華", "北京", "上海", "深圳", "広州", "習近平", "王毅", "李強",
    "BYD", "比亚迪", "華為", "华为", "阿里", "テンセント", "腾讯", "吉利", "小鵬", "蔚来",
    "テスラ中国", "特斯拉上海", "中国製造", "中国工場",
    "日中", "中日", "訪中", "訪問中国", "中国外務省", "中国大使館",
    "台湾", "香港", "ホルムズ", "イラン", "北朝鮮",
]

JAPAN_REGION_KEYWORDS = [
    "中国電力", "中国銀行", "中国放送", "中国地方", "中国新聞",
    "広島", "岡山", "山口", "鳥取", "島根", "中国運輸", "RCC",
]


# ============ 过滤工具 ============

def is_sensitive(text: str) -> bool:
    """检查是否含敏感词"""
    return any(kw in text for kw in SENSITIVE_KEYWORDS)


def is_china_related(title: str) -> bool:
    """判断是否与中国相关（排除日本中国地方）"""
    if any(kw in title for kw in JAPAN_REGION_KEYWORDS):
        if not any(kw in title for kw in ["中国本土", "中国人", "中国経済", "中国政府", "BYD", "テスラ中国"]):
            return False
    return any(kw in title for kw in CHINA_KEYWORDS)


# ============ LiteLLM / AI ============

print(f"🤖 LLM: {LITELLM_MODEL}  max_tokens={LITELLM_MAX_TOKENS}  ({LITELLM_URL})")


def call_litellm(prompt: str, system_prompt: str = "", max_tokens: int = 1000) -> str:
    """调用 LiteLLM API，返回文本；未配置或失败时返回空字符串"""
    if not LITELLM_API_KEY:
        return ""
    try:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        resp = requests.post(
            f"{LITELLM_URL}/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={"model": LITELLM_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.7},
            timeout=60,
        )
        if resp.status_code == 200:
            msg = resp.json().get("choices", [{}])[0].get("message", {})
            return (msg.get("content") or msg.get("reasoning_content", "")).strip()
        print(f"    ⚠️ LiteLLM 错误: {resp.status_code}")
    except Exception as e:
        print(f"    ⚠️ LiteLLM 调用失败: {e}")
    return ""


def translate_title(title_ja: str) -> str:
    """将日文标题翻译为中文"""
    result = call_litellm(
        f"请将以下日文新闻标题翻译成简洁的中文。\n\n要求：\n1. 只输出翻译结果\n2. 不要添加任何解释\n3. 保持简洁\n\n日文标题：{title_ja}\n\n中文翻译：",
        max_tokens=200,
    )
    if result:
        for line in reversed(result.strip().split('\n')):
            line = line.strip()
            if line and not line.startswith(('分析', '翻译', '1.', '2.', '3.', '*', '**', '-', '源文本', '目标')):
                line = line.replace('中文翻译：', '').replace('翻译结果：', '').strip()
                if 3 < len(line) < 150:
                    return line
    return title_ja


# 搜索引流关键词 → 标题中必须出现的中文名称
# 格式：搜索词 → 标题中使用的标准中文名
SEARCH_KEYWORD_TITLE_MAP: dict[str, str] = {
    "AKB":      "AKB",
    "乃木坂":   "乃木坂",
    "日向坂":   "日向坂",
    "欅坂":     "樱坂",
    "櫻坂":     "樱坂",
    "樱坂":     "樱坂",
    "坂道":     "坂道",
    "SKE":      "SKE",
    "NMB":      "NMB",
    "HKT":      "HKT",
    "STU":      "STU",
    "NGT":      "NGT",
    "モーニング娘": "早安少女",
    "ハロプロ":  "HelloProject",
}


def generate_content_and_comment(title_ja: str, title_zh: str, keyword: str = "") -> Tuple[str, str, str, str, str, list]:
    """生成 SEO标题、总结、新闻要点、我的解读、N1/N2词汇、话题标签列表

    Args:
        keyword: 搜索关键词，非空时标题中必须包含对应中文名（引流用）

    Returns:
        (seo_title, summary, content, comment, vocab, topic_tags)
    """
    # 确定标题中必须出现的关键词（用于引流）
    required_kw = SEARCH_KEYWORD_TITLE_MAP.get(keyword, keyword) if keyword else ""

    kw_instruction = ""
    if required_kw:
        kw_instruction = f"\n引流要求：标题中必须出现「{required_kw}」，这是搜索引流关键词，不可省略。\n"

    prompt = f"""你是小红书日语学习博主。请根据新闻标题，严格按照下方格式输出全部6个字段。

新闻标题：{title_zh}
日文原文：{title_ja}{kw_instruction}

输出格式（必须包含全部6个字段）：

【SEO标题】
（字数规则：最大20字。汉字/日语/标点各算1字，英文字母2个算1字（如「AKB48」算2.5字）。按新闻类型选对应策略：

▸ 艺能/追星类 → 社会证明 + 情绪反差，事件具体，情绪词放句末，不说答案：
  「他退社那一刻，粉丝群沉默了」「拓哉这次回应，和大家想的不一样」
  「山内铃兰官宣，松井珠理奈的回应亮了」

▸ 学习/语言类 → 结果前置 + 数字锚定，制造「这方法我也能用」的代入感：
  「不背单词，3个月能追番了」「这10个N1词，外国人全栽在这」
  「N1词汇Top10，第一名出乎意料」

▸ 时事/社会类 → 信息差 + 身份代入，暗示「你不知道但应该知道」：
  「在日华人速看，这个制度要变了」「同一条新闻，日文版多了这几句话」
  「数十万人受影响，真正的重点在这」

铁律（每次生成后自检）：
- 标题留悬念，不说答案，看完还想点进来
- 前7字出现具体信息，不以「日本」「日语」泛泛打头
- 字数上限20：汉字/日语/标点各1字，英文字母2个=1字，超了必须砍
- 禁用套话：震惊/绝了/炸裂/天花板/粉丝集合/必看/N1党狂喜
- 禁用重复句式：评论区画风突变/粉丝沸了/网友坐不住了/看完沉默了 ← 这类句末模板已被滥用，必须换表达
- 每次根据当前新闻内容重新构思，句末情绪词不得与上次相同）

【引流摘要】
（15-30字。这是小红书笔记卡片的副标题，独立于标题单独工作。
公式：话题（讲什么）+ Hook（为什么看）+ 悬念（不给答案）

按类型写法——
艺能/追星 → 场景还原，制造画面感，不给结论：
  好：「消息公布那一刻，我看了评论区整整五分钟没说话」
  差：「木村拓哉回应粉丝担忧，感动了很多人」（直接给了结论）

学习/语言 → 结果或数字前置，后接悬念：
  好：「3个月从听不懂到能追番，关键不是单词量」
  差：「分享一下我学日语的方法，希望对大家有帮助」

时事/社会 → 信息差或反问，不复述标题：
  好：「同样一件事，日文报道和中文报道省了不一样的东西」
  差：「日本政府宣布修改制度，影响很多外国人」（复述了标题）

禁止：「今天给大家分享」「宝子们！」「干货预警」「建议收藏」「话不多说」）

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
中日双语类：#中日双语 #中日翻译
日本资讯类：#日本新闻 #日本热点 #日本资讯 #看新闻学日语
综合类：#日语学习打卡 #日本文化 #日本生活
时尚穿搭类（内容涉及时尚/穿搭时用）：#日系穿搭 #日本穿搭 #穿搭分享 #今日穿搭 #日系风格
美妆护肤类（内容涉及化妆/护肤时用）：#日系妆容 #日本化妆 #日本美妆 #日本护肤 #护肤分享
再根据内容补充行业相关标签）"""

    result = call_litellm(prompt, max_tokens=LITELLM_MAX_TOKENS)
    if not result:
        return None  # LLM 调用失败，由调用方决定是否跳过

    seo_title = title_zh
    summary = content = comment = vocab = ""
    topic_tags: list[str] = []

    # GLM-5 may repeat field headers during its analysis phase — use the last occurrence of each
    def last_section(field: str) -> str:
        parts = re.split(rf'【{field}】', result)
        if len(parts) < 2:
            return ""
        return parts[-1].strip()

    def _title_weight(s: str) -> float:
        """汉字/日语/标点各1，英文字母2个=1（权重0.5）"""
        return sum(0.5 if ch.isascii() and ch.isalpha() else 1 for ch in s)

    def _truncate_title(s: str, max_w: float = 20) -> str:
        total = 0.0
        for i, ch in enumerate(s):
            total += 0.5 if ch.isascii() and ch.isalpha() else 1
            if total > max_w:
                return s[:i]
        return s

    raw_seo = last_section("SEO标题")
    if raw_seo:
        seo_title = _truncate_title(raw_seo.split('\n')[0].strip())
        # 兜底：关键词未出现时强制插入到标题开头
        if required_kw and required_kw not in seo_title:
            seo_title = _truncate_title(f"{required_kw}{seo_title}")

    raw_summary = last_section("引流摘要")
    if raw_summary:
        summary = raw_summary.split('\n')[0].strip()

    raw_content = last_section("新闻要点")
    if raw_content:
        # strip everything after the next 【 field header
        content = re.split(r'【[^】]+】', raw_content)[0].strip()

    raw_comment = last_section("我的解读")
    if raw_comment:
        comment = re.split(r'【[^】]+】', raw_comment)[0].strip()

    raw_vocab = last_section("N1/N2词汇")
    if raw_vocab:
        vocab = re.split(r'【[^】]+】', raw_vocab)[0].strip()

    raw_tags = last_section("话题标签")
    if raw_tags:
        tag_line = re.split(r'【[^】]+】', raw_tags)[0].strip().split('\n')[0].strip()
        topic_tags = [t.lstrip('#').strip() for t in re.findall(r'#\S+', tag_line) if t.lstrip('#').strip()]

    return seo_title, summary, content, comment, vocab, topic_tags


# ============ 分类 ============

def auto_classify(title: str, content: str = "", keyword: str = "") -> Tuple[str, List[str]]:
    """自动分类和打标签"""
    category = "经济"
    tags: list[str] = []
    text_all = title + " " + content

    text = text_all.lower()

    if any(k in text for k in ["ev", "电动车", "汽车", "日産", "本田", "丰田", "比亚迪", "特斯拉", "サクラ", "インサイト", "byd", "吉利"]):
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

    brand_map = {
        "特斯拉": ["テスラ", "tesla"],
        "比亚迪": ["byd", "比亚迪"],
        "本田": ["ホンダ", "honda", "インサイト"],
        "日产": ["日産", "nissan", "サクラ"],
        "丰田": ["トヨタ", "toyota"],
        "吉利": ["吉利", "geely"],
    }
    for tag, kws in brand_map.items():
        if any(k in text for k in kws):
            tags.append(tag)

    if "ホルムズ" in text or "霍尔木兹" in text:
        tags.append("中东局势")
    if "イラン" in text or "伊朗" in text:
        tags.append("伊朗")
    if "日中" in text or "中日" in text:
        tags.append("中日关系")
    if "韓中" in text or "中韩" in text:
        tags.append("中韩关系")

    entertainment_map = {
        "AKB": ["akb48", "akb", "akb47"],
        "乃木坂": ["乃木坂46", "乃木坂"],
        "欅坂": ["欅坂46", "欅坂", "櫻坂"],
        "cosplay": ["コスプレ", "コスプ", "cosplay"],
        "动漫": ["アニメ", "anime"],
        "游戏": ["ゲーム", "game"],
        "鸣潮": ["鳴潮"],
        "崩坏": ["崩壊", "崩坏"],
        "星穹铁道": ["スターレイル"],
    }
    for tag, kws in entertainment_map.items():
        if any(k in text for k in kws):
            tags.append(tag)

    return category, list(set(tags))


# ============ CDP 工具 ============

def get_yahoo_tab_ws_url() -> Tuple[str, bool]:
    """从 CDP /json 列表中找到 Yahoo 新闻首页的 page tab。
    
    Returns:
        (ws_url, already_on_yahoo)
        already_on_yahoo=True 表示当前 tab 已在 news.yahoo.co.jp，可直接读取。
        ws_url="" 表示找不到任何可用 tab。
    """
    try:
        resp = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=10)
        if resp.status_code != 200:
            return "", False
        tabs = resp.json()
    except Exception:
        return "", False

    # 优先找已打开 Yahoo 新闻首页的 tab（搜索/文章页没有 #newsFeed）
    for tab in tabs:
        if tab.get("type") == "page" and "news.yahoo.co.jp" in tab.get("url", ""):
            url = tab.get("url", "")
            # 只有首页才有 #newsFeed 容器（URL 不含 /search /articles 等子路径）
            from urllib.parse import urlparse
            path = urlparse(url).path.rstrip("/")
            if path in ("", "/"):
                return tab.get("webSocketDebuggerUrl", ""), True

    # 退而求其次：任意普通 page tab（用于导航）
    for tab in tabs:
        if tab.get("type") == "page":
            return tab.get("webSocketDebuggerUrl", ""), False

    return "", False


# ============ 文章工具 ============

def extract_key_from_url(url: str) -> str:
    """从 Yahoo 新闻 URL 提取文章唯一 key（hex 段）"""
    m = re.search(r'/articles/([a-f0-9]+)', url)
    return m.group(1) if m else ""


def fetch_article_details(url: str) -> dict:
    """HTTP 请求文章页，抓取封面图、原标题、日文摘要"""
    result = {"image_url": "", "original_title": "", "summary": ""}
    try:
        resp = requests.get(url, headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        }, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")

        og = soup.find("meta", property="og:image")
        if og and og.get("content"):
            result["image_url"] = og["content"]

        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            result["original_title"] = og_title["content"]
        else:
            h1 = soup.find("h1")
            if h1:
                result["original_title"] = h1.get_text(strip=True)

        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            result["summary"] = og_desc["content"]
        else:
            article = soup.find("article") or soup.find(class_="article")
            if article:
                p = article.find("p")
                if p:
                    result["summary"] = p.get_text(strip=True)[:300]
    except Exception as e:
        print(f"    ⚠️ 抓取文章详情失败: {e}")
    return result


def upload_cover_image(image_url: str) -> str:
    """上传封面图到 Cloudinary，返回永久 URL；失败则返回原 URL"""
    if not image_url:
        return ""
    try:
        from image_uploader import upload_image
        permanent = upload_image(image_url)
        if permanent:
            print(f"    封面图: {permanent[:60]}...")
            return permanent
    except ImportError:
        pass
    print(f"    封面图(原始): {image_url[:60]}...")
    return image_url


def process_news_item(news: dict, no_translate: bool = False,
                      extra_tags: list[str] | None = None,
                      keyword: str = "") -> dict:
    """对单条新闻执行完整后处理（翻译→AI生成→分类→文章详情→封面图上传）。
    
    原地修改并返回 news dict。
    """
    # 1. 翻译 + AI 生成
    if LITELLM_API_KEY and not no_translate:
        print("    翻译...")
        news['title_zh'] = translate_title(news['title_ja'])
        print("    生成内容...")
        generated = generate_content_and_comment(
            news['title_ja'], news['title_zh'], keyword=keyword
        )
        if generated is None:
            print("    ⚠️ LLM 调用失败，跳过此条新闻")
            news['_skip'] = True
            return news
        seo_title, summary, content, comment, vocab, topic_tags = generated
        news['title_zh'] = seo_title
        news['summary']  = summary
        news['content']  = content
        news['comment']  = comment
        news['vocab']    = vocab
    else:
        news.setdefault('title_zh', news['title_ja'])
        news.setdefault('content', f"• {news['title_ja']}")
        news.setdefault('comment', "")
        news.setdefault('vocab', "")
        topic_tags: list[str] = []

    # 2. 分类 + 标签（auto_classify + extra_tags + AI话题标签）
    category, tags = auto_classify(news['title_ja'], news.get('content', ''), keyword=keyword)
    for t in topic_tags:
        if t not in tags:
            tags.append(t)
    if extra_tags:
        for t in extra_tags:
            if t not in tags:
                tags.append(t)
    news['category'] = category
    news['tags']     = tags
    news['source']   = news.get('source', 'Yahoo Japan')
    # pub_time：强制统一为 YYYY.MM.DD，忽略来源页的日文时间格式
    news['pub_time'] = datetime.now().strftime('%Y.%m.%d')
    if keyword:
        news['keyword'] = keyword

    # 3. 文章详情（封面图、原标题、日文摘要）
    print("    抓取文章详情...")
    details = fetch_article_details(news['link'])
    news['original_title']     = details.get("original_title", news['title_ja'])
    news['ja_summary']         = details.get("summary", "")
    news['original_image_url'] = details.get("image_url", "")

    # 4. Cloudinary 上传
    if news['original_image_url']:
        news['image_url'] = upload_cover_image(news['original_image_url'])
    else:
        news.setdefault('image_url', "")
        print("    封面图: ⚠️ 未找到")

    print(f"    分类: {category} | 标签: {', '.join(tags[:3])}")
    return news


# ============ Notion ============

def parse_markdown_line(line: str) -> List[Dict]:
    """将含 **bold** 的 markdown 行转换为 Notion rich_text 格式"""
    rich_text = []
    for part in re.split(r'(\*\*.*?\*\*)', line):
        if part.startswith("**") and part.endswith("**"):
            rich_text.append({"type": "text", "text": {"content": part[2:-2]},
                               "annotations": {"bold": True}})
        elif part:
            rich_text.append({"type": "text", "text": {"content": part}})
    return rich_text or [{"type": "text", "text": {"content": line}}]


def load_today_keys() -> set:
    """从 Notion 加载今天已存在的文章 key 集合（用于去重）"""
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return set()

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    today = datetime.now().strftime('%Y.%m.%d')
    keys: set[str] = set()
    has_more, start_cursor = True, None

    while has_more:
        query: dict = {
            "page_size": 100,
            "filter": {"property": "发布时间", "rich_text": {"equals": today}},
        }
        if start_cursor:
            query["start_cursor"] = start_cursor
        resp = requests.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=headers, json=query,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        for page in data.get("results", []):
            rich = page.get("properties", {}).get("key", {}).get("rich_text", [])
            key = "".join(r.get("plain_text", "") for r in rich)
            if key:
                keys.add(key)
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    print(f"📋 已加载今天的 {len(keys)} 个已存在 key")
    return keys


def is_duplicate(key: str) -> bool:
    """检查 key 是否已存在于 Notion"""
    if not key or not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return False
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=headers,
        json={"filter": {"property": "key", "rich_text": {"equals": key}}, "page_size": 1},
    )
    return resp.status_code == 200 and len(resp.json().get("results", [])) > 0


def push_to_notion(news: Dict) -> str:
    """推送单条新闻到 Notion，返回 page_id（失败返回空字符串）"""
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        print("    ⚠️ Notion 配置不完整，跳过推送")
        return ""

    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }

    # ── 内容块 ──────────────────────────────────────────
    blocks: list[dict] = []

    if news.get("summary"):
        blocks.append({"object": "block", "type": "callout", "callout": {
            "icon": {"type": "emoji", "emoji": "💡"},
            "rich_text": [{"type": "text", "text": {"content": news["summary"][:200]}}],
        }})
        blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": []}})

    if news.get("content"):
        blocks.append({"object": "block", "type": "heading_3", "heading_3": {
            "rich_text": [{"type": "text", "text": {"content": "📰 新闻要点"}}]
        }})
        for line in news["content"].split("\n"):
            line = line.strip()
            if not line:
                continue
            if line.startswith(("•", "-")):
                blocks.append({"object": "block", "type": "bulleted_list_item",
                                "bulleted_list_item": {"rich_text": parse_markdown_line(line.lstrip("•- ")[:2000])}})
            else:
                blocks.append({"object": "block", "type": "paragraph",
                                "paragraph": {"rich_text": parse_markdown_line(line[:2000])}})

    if news.get("comment"):
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        blocks.append({"object": "block", "type": "heading_3", "heading_3": {
            "rich_text": [{"type": "text", "text": {"content": "💭 我的解读"}}]
        }})
        blocks.append({"object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": news["comment"][:2000]}}]}})

    if news.get("vocab"):
        blocks.append({"object": "block", "type": "divider", "divider": {}})
        blocks.append({"object": "block", "type": "heading_3", "heading_3": {
            "rich_text": [{"type": "text", "text": {"content": "📝 今日词汇 (N1/N2)"}}]
        }})
        for line in news["vocab"].split("\n"):
            line = line.strip()
            if not line:
                continue
            btype = "quote" if line.startswith("例句") else "paragraph"
            blocks.append({"object": "block", "type": btype,
                            btype: {"rich_text": [{"type": "text", "text": {"content": line[:500]}}]}})

    blocks.append({"object": "block", "type": "divider", "divider": {}})
    blocks.append({"object": "block", "type": "heading_3", "heading_3": {
        "rich_text": [{"type": "text", "text": {"content": "📰 原文"}}]
    }})
    original_title = news.get("original_title") or news["title_ja"]
    if original_title:
        blocks.append({"object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": original_title[:500]}}]}})
    if news.get("ja_summary"):
        blocks.append({"object": "block", "type": "paragraph",
                        "paragraph": {"rich_text": [{"type": "text", "text": {"content": news["ja_summary"][:500]}}]}})

    blocks.append({"object": "block", "type": "divider", "divider": {}})
    blocks.append({"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
        {"type": "text", "text": {"content": "🔗 原文链接：", "link": {"url": news["link"]}}}
    ]}})

    # ── 属性 ─────────────────────────────────────────────
    key       = extract_key_from_url(news["link"])
    image_url = news.get("image_url", "")
    orig_img  = news.get("original_image_url", "")

    props: dict = {
        "Name":     {"title":     [{"text": {"content": news.get("title_zh", news["title_ja"])[:20]}}]},
        "key":      {"rich_text": [{"text": {"content": key}}]},
        "分类":     {"select":    {"name": news.get("category", "经济")}},
        "标签":     {"multi_select": [{"name": t} for t in news.get("tags", [])]},
        "来源":     {"rich_text": [{"text": {"content": news.get("source", "Yahoo Japan")}}]},
        "发布时间": {"rich_text": [{"text": {"content": news.get("pub_time", datetime.now().strftime('%Y.%m.%d'))}}]},
        "原文链接": {"url": news["link"]},
    }
    if image_url:
        props["封面图"] = {"url": image_url}
    if orig_img:
        props["原图链接"] = {"url": orig_img}

    payload: dict = {
        "parent":   {"database_id": NOTION_DATABASE_ID},
        "properties": props,
        "children": blocks,
    }
    if image_url:
        payload["cover"] = {"type": "external", "external": {"url": image_url}}

    resp = requests.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    if resp.status_code != 200:
        print(f"    ⚠️ Notion 错误: {resp.status_code} {resp.text[:200]}")
        return ""
    return resp.json().get("id", "")


def push_with_gallery(news: dict, existing_keys: set | None = None) -> bool:
    """推送到 Notion 并检测图集外链。返回是否成功"""
    key = extract_key_from_url(news["link"])
    page_id = push_to_notion(news)
    if not page_id:
        print("    ❌ 推送失败")
        return False

    print("    ✅ 已推送到 Notion")
    if existing_keys is not None:
        existing_keys.add(key)

    # 图集检测
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from gallery_fetch import detect_gallery_link, update_notion_gallery_url
        gallery_url = detect_gallery_link(news["link"])
        if gallery_url:
            update_notion_gallery_url(page_id, gallery_url)
        else:
            print("    — 未检测到图集外链")
    except Exception as e:
        print(f"    ⚠️ 图集检测失败: {e}")
    return True


def check_chrome_cdp() -> bool:
    """检查 Chrome CDP 是否可用，打印状态并返回 bool"""
    try:
        resp = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5)
        if resp.status_code == 200:
            print("✅ Chrome 已就绪")
            return True
        print("❌ Chrome 未响应")
    except Exception:
        print("❌ Chrome 未运行，请先启动: python scripts/chrome_launcher.py")
    return False
