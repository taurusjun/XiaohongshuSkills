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
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))  # project root for config
import json
import time
import random
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from datetime import datetime

# 直连 session：Notion API + DeepSeek API 不走系统代理
_direct_session = requests.Session()
_direct_session.trust_env = False
from typing import List, Dict, Tuple

# ── .env ──────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))
except ImportError:
    pass


def check_proxy(interactive: bool = True) -> bool:
    """启动时检测代理是否可用。interactive=True 时代理挂了会询问用户。
    返回 True 表示继续，False 表示用户选择退出。"""
    from config.yahoo_conf import USE_PROXY, PROXY_URL
    if not USE_PROXY:
        _disable_proxy()
        return True
    if not PROXY_URL:
        print("ℹ️ 未配置代理，使用直连")
        return True

    # 设置代理环境变量
    os.environ['HTTP_PROXY'] = PROXY_URL
    os.environ['HTTPS_PROXY'] = PROXY_URL
    os.environ['http_proxy'] = PROXY_URL
    os.environ['https_proxy'] = PROXY_URL

    # 先测代理
    try:
        r = requests.get("https://news.yahoo.co.jp/", timeout=8)
        if r.status_code == 200:
            print(f"✅ 代理可用 ({PROXY_URL})")
            return True
    except Exception:
        pass

    # 代理不通，测一下直连是否可行
    print(f"\n⚠️  代理不可用 ({PROXY_URL})")
    direct_ok = False
    try:
        r = requests.get("https://news.yahoo.co.jp/",
                          proxies={"http": None, "https": None}, timeout=8)
        if r.status_code == 200:
            direct_ok = True
    except Exception:
        pass

    if not interactive:
        if direct_ok:
            print("已自动回退直连")
            _disable_proxy()
            return True
        else:
            print("❌ 代理和直连均不可用")
            return False

    # 交互模式：问用户
    if direct_ok:
        choice = input("是否降级为直连模式继续？[Y/n]: ").strip().lower()
    else:
        choice = input("代理和直连均不可用，是否仍继续尝试？[y/N]: ").strip().lower()
    if choice in ("", "y", "yes"):
        _disable_proxy()
        print("已切换直连模式\n")
        return True
    else:
        print("已取消\n")
        return False


def _disable_proxy():
    """清除代理环境变量"""
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        os.environ.pop(k, None)


# ============ 配置 ============

NOTION_API_KEY    = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")

from config.yahoo_conf import STORAGE_BACKEND, GALLERY_CACHE_DIR

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



def call_litellm(prompt: str, system_prompt: str = "", max_tokens: int = 1000,
                 response_format: dict | None = None, temperature: float = 0.7) -> str:
    """调用 LiteLLM API，返回文本；未配置或失败时返回空字符串"""
    if not LITELLM_API_KEY:
        return ""
    import time as _time
    for attempt, wait in enumerate([0, 5, 15]):
        if wait:
            _time.sleep(wait)
        try:
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})

            body = {"model": LITELLM_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": temperature}
            if response_format:
                body["response_format"] = response_format

            resp = _direct_session.post(
                f"{LITELLM_URL}/chat/completions",
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {LITELLM_API_KEY}"},
                json=body,
                timeout=(30, 600),
            )
            if resp.status_code == 200:
                msg = resp.json().get("choices", [{}])[0].get("message", {})
                content = msg.get("content", "").strip()
                if not content:
                    reasoning = msg.get("reasoning_content", "").strip()
                    if reasoning:
                        # 多层 try 提取 JSON
                        import re as _re2
                        try:
                            import json as _json2
                            _json2.loads(reasoning)
                            content = reasoning
                        except Exception:
                            m = _re2.search(r'\{.*\}', reasoning, _re2.DOTALL)
                            if m:
                                content = m.group(0)
                        if not content:
                            content = reasoning
                return content.strip()
            if resp.status_code < 500:
                break  # 4xx 不重试
        except Exception as e:
            if attempt == 2:
                print(f"    ⚠️ LiteLLM 调用失败(最终): {e}")
    return ""


def generate_video_caption(title_zh: str, summary: str, content: str, tags: list) -> str:
    """为视频发布生成简洁配文（80-120字）。
    结构：悬念/共鸣句 + 1-2句补充上下文 + 互动召唤 + tags

    使用【短配文】标记提取，与 GLM-5 推理模式兼容。
    """
    prompt = f"""你是小红书日本新闻博主。根据新闻写一段简短解说，严格按格式输出。

新闻标题：{title_zh}
新闻要点：{content[:300]}

【短配文】
（直接写解说正文，不提"视频""MV"等媒体形式。
每句话单独一行，句与句之间空一行。
第一句：悬念或共鸣，≤15字，不说答案。
中间2-3句：补充新闻背景或值得关注的细节，共40-60字。
最后一句：互动召唤，≤10字，如"你怎么看？""你知道吗？"。
全文80-120字，口语化，不要新闻腔。）"""

    result = call_litellm(prompt, system_prompt="只输出短配文正文，不要任何分析、推理过程、字数检查。", max_tokens=3000)
    if not result:
        return ""

    import re as _re

    def _trim_reasoning(text: str) -> str:
        """截断推理文本：遇到空行+星号行、或星号开头行即停止"""
        lines = text.splitlines()
        out = []
        for line in lines:
            stripped = line.strip()
            # 推理标志：*开头、数字列表、「等等」「字数」「草稿」
            if _re.match(r'^\s*(\*|等等|字数|草稿|\d+\.\s)', stripped):
                break
            out.append(line)
        return "\n".join(out).rstrip()

    # 情况1：模型输出了 【短配文】 标记 → 取最后一个标记后的内容
    parts = result.split("【短配文】")
    if len(parts) >= 2:
        raw = parts[-1].strip()
        # 去掉括号说明（模型有时把格式说明也输出）
        raw = _re.sub(r'^[（(][^）)]{0,200}[）)]', '', raw).strip()
        raw = _re.split(r'【[^】]+】', raw)[0].strip()
        caption = _trim_reasoning(raw)
    else:
        # 情况2：模型直接输出配文（无标记）→ 清理分析行后直接使用
        lines = result.strip().splitlines()
        clean = []
        skip = _re.compile(r'^\s*(\d+\.\s|\*\s|[-•▸]\s|【|分析|要求|角色|任务|草稿|字数检查)')
        for line in lines:
            line = _re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', line.strip())
            if not line or skip.match(line):
                continue
            clean.append(line)
        caption = "\n".join(clean).strip()

    # 兜底：若整段没有换行，按句末标点自动断行
    if caption and "\n" not in caption:
        import re as _re2
        caption = _re2.sub(r'([。？！~～]+)', r'\1\n', caption).strip()

    return caption


# 日式汉字 → 简体中文 推荐转换表（LLM 偶尔遗漏的）
_SHINJITAI_MAP = str.maketrans({
    # 人名常见繁体/旧字体
    '環': '环', '奈': '奈', '濱': '滨', '濵': '滨', '渕': '渊', '眞': '真',
    '惠': '惠', '榮': '荣', '寛': '宽', '彩': '彩', '穗': '穗', '梨': '梨',
    '繪': '绘', '禮': '礼', '壽': '寿', '壱': '壹', '貳': '贰',
    # 通用繁体
    '実': '实', '實': '实', '愛': '爱', '島': '岛', '澤': '泽', '櫻': '樱', '桜': '樱',
    '結': '结', '黒': '黑', '會': '会', '園': '园', '戦': '战', '円': '圆',
    '関': '关', '気': '气', '楽': '乐', '読': '读', '語': '语', '長': '长',
    '門': '门', '間': '间', '時': '时', '開': '开', '個': '个', '寫': '写',
    '畫': '画', '體': '体', '萬': '万', '両': '两', '東': '东', '電': '电',
    '車': '车', '馬': '马', '魚': '鱼', '鳥': '鸟', '飛': '飞', '風': '风',
    '貝': '贝', '見': '见', '話': '话', '説': '说', '認': '认', '識': '识',
    '誰': '谁', '違': '违', '連': '连', '遠': '远', '選': '选', '進': '进',
    '過': '过', '運': '运', '動': '动', '働': '动', '労': '劳', '榮': '荣',
    '營': '营', '單': '单', '嚴': '严', '兒': '儿', '處': '处', '據': '据',
    '應': '应', '當': '当', '聲': '声', '點': '点', '變': '变', '讓': '让',
    '對': '对', '難': '难', '雲': '云', '電': '电', '預': '预', '頭': '头',
    '髮': '发', '龍': '龙', '龜': '龟', '齊': '齐', '齋': '斋', '齊': '齐',
    '賣': '卖', '買': '买', '來': '来', '飲': '饮', '飯': '饭', '鉄': '铁',
    '転': '转', '軽': '轻', '芸': '艺', '殺': '杀', '雑': '杂', '亞': '亚',
    '圖': '图', '團': '团', '圍': '围', '壓': '压', '價': '价', '係': '系',
    '狀': '状', '條': '条', '獨': '独', '獸': '兽', '産': '产', '異': '异',
    '畫': '画', '發': '发', '縣': '县', '號': '号', '裝': '装', '裏': '里',
    '製': '制', '複': '复', '規': '规', '親': '亲', '覺': '觉', '覽': '览',
    '觀': '观', '試': '试', '討': '讨', '計': '计', '記': '记', '証': '证',
    '評': '评', '課': '课', '調': '调', '議': '议', '護': '护', '讓': '让',
    '穀': '谷', '経': '经', '統': '统', '総': '总', '練': '练', '縁': '缘',
    '線': '线', '編': '编', '績': '绩', '織': '织', '絵': '绘', '給': '给',
    '続': '续', '終': '终', '級': '级', '組': '组', '細': '细', '約': '约',
    '紅': '红', '納': '纳', '純': '纯', '紙': '纸', '素': '素', '網': '网',
    '緊': '紧', '総': '总', '緑': '绿', '線': '线', '編': '编', '緩': '缓',
})

def _normalize_kanji(text: str) -> str:
    """将日式汉字（旧字体/繁体）转为简体中文"""
    return text.translate(_SHINJITAI_MAP)

def translate_and_classify(title_ja: str, body_ja: str = "") -> dict:
    """翻译标题 + 判断体裁适用性，一次 LLM 调用完成。
    返回 {"title_zh": str, "format_suitability": list[str], "is_long_form": bool}
    """
    import re as _re, json as _json
    FORMAT_TYPES = ["news", "story", "ranking", "comparison"]

    body_snippet = body_ja if body_ja else ""  # 長度由調用方控制
    format_guide = (
        "体裁判断标准：\n"
        "- news（资讯体）：任何内容都适用，是兜底选项\n"
        "- story（故事体）：内容有时间弧度或前后变化，如「从默默无闻到走红」「克服困难最终成功」「意外转折」。适用示例：长访谈/周年回顾/突发反转新闻\n"
        "- ranking（盘点体）：可以提炼出≥3个并列元素，如代表作Top5/历代比较。适用示例：周年精选/历代比较/分类推荐\n"
        "- comparison（对比体）：含两个或以上可比较对象，如两人对比/前后对比。适用示例：同一人不同时期/两位艺人对比\n"
    )

    prompt = (
        f"任务1：将以下日文标题翻译为中文（一行，不加解释）\n"
        f"任务2：判断这篇文章适合哪些体裁\n\n"
        f"日文标题：{title_ja}\n"
        f"正文摘要（前500字）：{body_snippet}\n\n"
        f"{format_guide}\n"
        f"返回严格JSON（两个任务独立）：\n"
        f'{{\"title_zh\": \"中文标题\", \"format_suitability\": [\"news\"], \"reason\": \"简短说明\"}}'
    )

    result_str = call_litellm(
        prompt, max_tokens=300, temperature=0.2,
        response_format={"type": "json_object"},
    ) if LITELLM_API_KEY else None

    title_zh = ""
    formats = ["news"]
    if result_str:
        try:
            data = _json.loads(result_str)
            title_zh = data.get("title_zh", "").strip()
            fs = data.get("format_suitability", ["news"])
            if isinstance(fs, str):
                fs = [fs]
            formats = [f for f in fs if f in FORMAT_TYPES] or ["news"]
        except Exception:
            pass

    # fallback：仅翻译
    if not title_zh:
        title_zh = translate_title(title_ja)

    # Q&A格式检测：不自动注入 story
    qa_markers = sum(1 for m in _re.finditer(r'(?:^|\n)\s*[Ｑq][:：]', body_ja or ""))
    if qa_markers >= 3 and "story" in formats:
        formats = [f for f in formats if f != "story"]
        if not formats:
            formats = ["news"]

    return {
        "title_zh": _normalize_kanji(title_zh),
        "format_suitability": formats,
        "is_long_form": False,
    }


def translate_title(title_ja: str) -> str:
    """将日文标题翻译为中文（默认用 LLM，可开关切回 Google）"""
    use_google = os.environ.get("USE_GOOGLE_TRANSLATE", "").lower() in ("1", "true", "yes")

    if use_google:
        try:
            from deep_translator import GoogleTranslator
            result = GoogleTranslator(source='ja', target='zh-CN').translate(title_ja)
            if result and len(result) > 2:
                return _normalize_kanji(result)
        except Exception:
            pass

    # LLM 翻译（max_tokens=500，结构化输出防止前缀泄漏）
    if LITELLM_API_KEY:
        result = call_litellm(
            prompt=f"日译中，只输出一行中文译文，不要任何解释：\n\n{title_ja}\n\n译文：",
            max_tokens=300,
        )
        if result and len(result) > 2 and result != title_ja:
            result = result.strip()
            # 取第一行或【译文】后的内容
            import re
            # 尝试提取【译文】标记后的内容
            m = re.search(r'【译文】\s*(.+?)$', result, re.DOTALL)
            if m:
                result = m.group(1).strip().split('\n')[0]
            # 如果包含明显不是译文的元描述（如"我们被要求""这是一段"），取最后一行
            meta_patterns = ["我们被要求", "我们收到", "我们需要", "请将以下", "这是一段", "以下是将", "翻译任务", "翻译内容"]
            if any(p in result[:60] for p in meta_patterns):
                lines = result.split('\n')
                # 取最后一个非空行
                for line in reversed(lines):
                    line = line.strip()
                    if line and len(line) > 3 and not any(p in line for p in meta_patterns):
                        result = line
                        break
            # 洗掉常见短前缀
            short_prefixes = ["翻译：", "译文：", "中文：", "**"]
            for p in short_prefixes:
                if result.startswith(p):
                    result = result[len(p):].strip()
            # 洗掉 markdown 加粗包裹
            if result.startswith("**") and "**" in result[2:]:
                result = result[2:result.index("**", 2)].strip()
            return _normalize_kanji(result)

    return _normalize_kanji(title_ja)


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


def generate_content_and_comment(title_ja: str, title_zh: str, ja_summary: str = "",
                                  keyword: str = "", body_text: str = "",
                                  hint: str = "", content_format: str = "news") -> Tuple[str, str, str, str, str, list]:
    """生成 SEO标题、总结、新闻要点、我的解读、N1/N2词汇、话题标签列表

    Args:
        keyword: 搜索关键词，非空时标题中必须包含对应中文名（引流用）
        body_text: 文章正文内容，提供更多上下文给 LLM

    Returns:
        (seo_title, summary, content, comment, vocab, topic_tags)
    """
    # 傲娇模式：约 1/3 概率启用
    tsundere_mode = random.random() < 0.33
    tsundere_instruction = ""
    if tsundere_mode:
        tsundere_instruction = """
本篇文章启用「傲娇语气」。在【我的解读】中融入 1 处傲娇表达（仅 1 处，不要多）。

傲娇核心话术：「否认 + 强硬理由 + 心口不一」
▸ 关心的傲娇：「真拿你没办法，就帮你一次……别得寸进尺！」「受伤了？笨蛋，下次注意啊！」
▸ 羞涩的傲娇：「才、才没有一直盯着看呢！」「如果是你的话，也不是不可以啦……」
▸ 嘴硬心虚：「哼，我又不是特意看的」「我只是顺手点进去了而已」「才不是为了你才发的」
▸ 委婉认可：「找我帮忙？早了一百年呢……（但也不是不行）」

禁止：不要用动漫腔（笨蛋/八嘎/无路赛），不要整段傲娇，1 处就够了。
"""
    else:
        tsundere_instruction = """
本篇文章使用正常语气，不要使用傲娇句式。
"""

    # 构建上下文：优先用正文，不够再用摘要
    context = ""
    if body_text:
        context = f"\n文章正文：\n{body_text[:2000]}\n"
    elif ja_summary:
        context = f"\n新闻摘要：{ja_summary[:600]}\n"

    prompt = f"""你是小红书日语学习博主。请根据以下新闻内容，严格按照下方格式输出全部6个字段。

新闻标题：{title_zh}
日文原文：{title_ja}{context}{tsundere_instruction}
{f"【修正要求】{hint}" if hint else ""}
输出格式（必须包含全部6个字段）：

【SEO标题】
（20字上限。汉字/日语/标点各1字，英文字母2个算1字。每次从以下4种策略中选一种：

▸ 反差提问 → 反常细节 + 问句收尾，不答：
  「H罩杯钢琴家，演出时弦断了？」「39公斤的她，全网在担心什么？」

▸ 数字先行 → 数字打头，制造信息差：
  「半年减28kg，没节食没运动」「40秒一台车，特斯拉上海厂实拍」

▸ 身份带入 → 让读者觉得「跟我有关」：
  「在日华人这张卡别忘了续」「去过东迪的人都不知道的规定」

▸ 细节钩子 → 一个小细节引出故事：
  「退社信只有一行字」「合照少了一个人，粉丝一眼发现」

铁律：
- 前7字必须出现人物的全名或具体数字，禁止用「乃木坂成员」「AKB偶像」「前女团成员」这类泛称。有人名必须用人名。例如：
  差：「乃木坂成员，毕业信一句话」← 哪个人？读者没感觉
  好：「远藤樱毕业信只有一行字」← 具体到人，粉丝立刻认出
  差：「前女团成员晒大胆照」← 谁？
  好：「田中美久晒照，评论区风向变了」← 具体
- 标题要有故事感，不要平铺直叙。给读者一条时间线或一个变化，让标题自己就能讲一个微型故事：
  差：「梅泽美波朗读剧，牧岛辉是她搭档」← 只说了谁和谁，没故事
  好：「梅泽美波毕业首次出演朗读剧」← 毕业→转型→新舞台，有叙事线
  差：「铃木优香晒新写真」← 看了等于没看
  好：「铃木优香脱下AKB制服，换上蕾丝泳装」← 变化→反差→故事
- 20字上限，超了砍修饰词
- 标题统一用简体中文，日式汉字（旧字体/繁体）必须转为简体中文对应字。必查清单：実→实、愛→爱、島→岛、澤→泽、櫻→樱、結→结、黒→黑、會→会、園→园、戦→战、円→圆、関→关、気→气、楽→乐、読→读、語→语、長→长、門→门、間→间、時→时、開→开、個→个、寫→写。日本团体名如「乃木坂」已是通用写法保持不变）
- 以下50+个词/句式一个都不能出现在标题里（犯规则重写）：
  破防 / 慌了 / 沉默了 / 画风突变 / 评论区炸了 / 粉丝沸腾了 /
  网友坐不住 / 看完沉默 / 全网震惊 / 不敢认 /
  太值了 / 绝了 / 炸裂 / 天花板 / 必看 /
  不看你亏 / 建议收藏 / 干货 / 话不多说 /
  下一秒 / 那一刻 / 这一刻 / 所有人 /
  宝子们 / 姐妹们 / 给大家分享 /
  太美了吧 / 也太美了 / 美到不敢认 /
  震惊 / 太 / 超 / 极（夸张副词）
- 标题不要重复原新闻标题的句式，用自己的话重新表达
- 不要用「！」，用句号或问号）

【引流摘要】
（15-30字。独立于标题，给读者一个点进来看的理由。
核心原则：要有故事感，不要复述标题。给读者一个变化、一个转折、或一个你不知道的背景。

▸ 艺能 → 人物变化 + 时间线（从XX到XX）：
  好：「从前AKB总监督到首次晒恩爱，中间隔了7年」（有故事线）
  差：「高桥南晒合照，粉丝感动留言」（复述了标题）
▸ 学习 → 结果+反常识：
  「3个月从零到能追番，关键竟然不是背单词」
▸ 时事 → 信息差：
  「同一条新闻，日文版比中文版多写了一段」

禁止：「给大家分享」「宝子们！」「干货预警」「建议收藏」「话不多说」）

【新闻要点】
• （要点1，一句话说清楚）
• （要点2，一句话说清楚）
• （要点3，一句话说清楚）
• （要点4，可选）

【我的解读】
（80-120字。第一人称，像发微信给朋友。必须包含以下3点中的至少2点：
1. 个人感受 — 从你的角度出发的真实反应（不要用"说实话""讲真""有一说一""咱就是说"开头，太假了）
2. 信息增量 — 补充一条文章里没有的背景知识
3. 互动召唤 — 结尾抛一个问题给读者
4. 日语嵌入 — 每篇解读中自然嵌入1个日语关键词，括号注音解释。例如：
   「台本上只写了"キス（吻）"，具体怎么亲全是现场商量的」
   让单词出现在上下文里，不要单独列词汇表

语气要求：
- 基础人设：口语化少女博主，自然不做作，像班里那个爱分享八卦的女生
- 禁止AI套话：我们可以看出/值得关注的是/从XX角度来看/不得不说/无疑/由此可见/综上所述
- 禁止假人设常用语：说实话/讲真/有一说一/咱就是说/谁懂啊/我直接一个好家伙/谁顶得住/这也太会了吧/反差感拉满/我哭死/杀疯了/第一反应是/我第一眼/看到XX我整个人都
- 语气词适量（每段不超过3处）：吧/嘛/呢/啦/咯/呀/哦/呗/欸
- 不完美感 > 工整感（"挺""有点""蛮"））

【话题标签】
（5-8个标签，#开头。优先从以下热门标签中选择合适的：
偶像/艺人类（首选）：#乃木坂46 #AKB48 #日向坂46 #欅坂46 #日本偶像 #日本艺人 #日本写真集 #日本性感女星
娱乐资讯类：#日本娱乐 #日本综艺 #日本明星 #日本演员
写真/グラビア类：#日本写真 #グラビア #日本模特
仅当内容明确涉及时，才可使用：#日本新闻 #日本文化 #日本生活
禁止使用：#看新闻学日语 #日语学习（这是偶像内容账号，非语言学习账号）
再根据文章中的具体人名/团体名补充精准标签）"""

    result = call_litellm(prompt, max_tokens=max(LITELLM_MAX_TOKENS, 8000))
    if not result:
        return None  # LLM 调用失败，由调用方决定是否跳过

    seo_title = title_zh
    summary = content = comment = ""
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
        cut = len(s)
        for i, ch in enumerate(s):
            total += 0.5 if ch.isascii() and ch.isalpha() else 1
            if total > max_w:
                cut = i
                break
        # 回退到自然边界：不截断在数字/英文中间
        while cut > 3:
            prev_ch = s[cut - 1]
            # 数字结尾 → 回退到前一个字（"主持5" → "主持"）
            if prev_ch.isdigit():
                cut -= 1
                continue
            # 英文字母结尾 → 回退（"推しメ" → "推し"）
            if prev_ch.isascii() and prev_ch.isalpha():
                cut -= 1
                continue
            break
        return s[:cut]

    # story 体裁标题上限 64 字符权重，其他 20
    title_max_w = 64.0 if content_format == "story" else 20.0

    raw_seo = last_section("SEO标题")
    if raw_seo:
        seo_title = _truncate_title(raw_seo.split('\n')[0].strip(), max_w=title_max_w)
        # 兜底：日式汉字 → 简体中文
        jis_to_sc = str.maketrans({
            '実': '实', '愛': '爱', '黒': '黑', '會': '会', '園': '园',
            '戦': '战', '円': '圆', '関': '关', '気': '气', '楽': '乐',
            '読': '读', '語': '语', '長': '长', '門': '门', '間': '间',
            '時': '时', '開': '开', '個': '个', '寫': '写',
        })
        seo_title = seo_title.translate(jis_to_sc)

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

    raw_tags = last_section("话题标签")
    if raw_tags:
        tag_line = re.split(r'【[^】]+】', raw_tags)[0].strip().split('\n')[0].strip()
        topic_tags = [t.lstrip('#').strip() for t in re.findall(r'#\S+', tag_line) if t.lstrip('#').strip()]

    return seo_title, summary, content, comment, "", topic_tags


# ============ 质量评分 ============

def build_scoring_prompt(dims: list[dict]) -> str:
    """从维度定义列表构造 prompt 片段"""
    lines = []
    for d in dims:
        if d.get("definition"):
            lines.append(
                f"【{d['name']}】判断标准：{d['definition']}\n"
                f"  ✅ 给1示例：{d.get('example_1','')}\n"
                f"  ❌ 给0示例：{d.get('example_0','')}\n"
                f"  ⚠️ 边界说明：{d.get('edge_case','')}"
            )
        else:
            lines.append(f"【{d['name']}】")
    return "\n".join(lines)


def evaluate_quality(title_zh: str, content: str, comment: str,
                     title_ja: str = "", body_text: str = "") -> dict:
    """用 LLM 评估标题和内容质量，返回 {title_score, content_score, scores(维度+理由)}"""
    import json as _json

    # 从 DB 加载活跃维度定义，DB 为空时回退到 JSON 文件
    try:
        from scripts.sqlite_db import load_active_dimensions
        _dims_cfg = load_active_dimensions()
        _dim_version = ""
        from scripts.sqlite_db import _connect
        with _connect() as _db:
            _vr = _db.execute("SELECT version FROM scoring_dimension_versions WHERE is_active=1").fetchone()
            if _vr:
                _dim_version = _vr["version"]
    except Exception:
        _dims_cfg = []
        _dim_version = ""

    if not _dims_cfg:
        from pathlib import Path
        _dims_path = Path(__file__).parent.parent / "config" / "scoring_dimensions.json"
        try:
            _dims_cfg = _json.loads(_dims_path.read_text(encoding="utf-8"))["dimensions"]
        except Exception:
            _dims_cfg = []

    # 从 JSON 配置推导硬编码的 plus/minus 分类
    title_plus = [d["name"] for d in _dims_cfg if d.get("category") == "标题" and d.get("direction") == "plus"]
    title_minus = [d["name"] for d in _dims_cfg if d.get("category") == "标题" and d.get("direction") == "minus"]
    content_plus = [d["name"] for d in _dims_cfg if d.get("category") == "内容" and d.get("direction") == "plus"]
    content_minus = [d["name"] for d in _dims_cfg if d.get("category") == "内容" and d.get("direction") == "minus"]
    all_dims = title_plus + title_minus + content_plus + content_minus
    # 回退到硬编码
    if not all_dims:
        title_plus = ['剧情感','冲突感','猎奇感','用户共鸣','名人','热点']
        title_minus = ['简单通知','震惊体','概括全部']
        content_plus = ['原创度','趣味性','有用信息','对立信息','视频','收藏驱动','评论引导性']
        content_minus = ['离题','啰嗦重复','主动讨赏','负面情绪']
        all_dims = title_plus + title_minus + content_plus + content_minus

    dim_prompt = build_scoring_prompt(_dims_cfg) if _dims_cfg else ""
    body_snippet = (body_text or content)[:800]

    prompt = f"""评估笔记，{len(all_dims)}个维度各判0、0.5或1，每维度附一句理由(15-50字)。

{dim_prompt}

标题：{title_zh}
正文：{body_snippet}
解读：{comment[:150]}

返回严格 JSON，格式为：
{{"维度名": {{"value": 0或0.5或1, "reason": "15-50字理由"}}, ...}}
所有 {len(all_dims)} 个维度都必须出现，value 只能是 0、0.5、1 三个值之一。"""

    result = call_litellm(prompt, system_prompt="You are a JSON API. Output ONLY valid JSON.", max_tokens=4000, response_format={"type": "json_object"}, temperature=0.1)
    if not result:
        return {"title_score": 0, "content_score": 0, "scores": {}, "_dim_version": _dim_version}

    try:
        raw = _json.loads(result)
        raw.pop("title_score", None)
        raw.pop("content_score", None)
        # 解析 value + reason
        dim_scores = {}
        for d in all_dims:
            v = raw.get(d)
            if isinstance(v, dict):
                dim_scores[d] = {"value": float(v.get("value", 0) or 0), "reason": v.get("reason", "")}
            elif isinstance(v, (int, float)):
                dim_scores[d] = {"value": float(v), "reason": ""}
            else:
                dim_scores[d] = {"value": 0.0, "reason": ""}
        # 计算
        # 加权评分（权重缺失默认 1.0，行为等价于原等权）
        try:
            from scripts.sqlite_db import load_dim_weights
            weights = load_dim_weights()
        except Exception:
            weights = {}

        def weighted_sum(dim_list: list, minus: bool = False) -> float:
            total = 0.0
            for d in dim_list:
                w = weights.get(d, 1.0)
                v = dim_scores.get(d, {}).get("value", 0.0)
                total += w * float(v)
            return total if not minus else -total

        title_score = weighted_sum(title_plus) + weighted_sum(title_minus, minus=True)
        content_score = weighted_sum(content_plus) + weighted_sum(content_minus, minus=True)
        title_score = max(0.0, min(5.0, title_score))
        content_score = max(0.0, min(5.0, content_score))
        return {
            "title_score": title_score,
            "content_score": content_score,
            "scores": dim_scores,
            "_dim_version": _dim_version,
        }
    except (_json.JSONDecodeError, ValueError, KeyError) as e:
        print(f"    ⚠️ 评分JSON解析失败: {e} | 输出: {result[:150]}")
        return {"title_score": 0, "content_score": 0, "scores": {}, "_dim_version": _dim_version}


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


def _fetch_html_via_cdp(url: str, wait_sec: float = 4.0) -> str:
    """用 CDP 导航到 url，等待 JS 渲染后返回 outerHTML；失败返回空串。"""
    try:
        import websocket as _ws_module
        resp = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5)
        if resp.status_code != 200:
            return ""
        tabs = resp.json()
        ws_url = next((t.get("webSocketDebuggerUrl", "") for t in tabs
                       if t.get("type") == "page"), "")
        if not ws_url:
            return ""
        ws = _ws_module.create_connection(ws_url, timeout=20)
        try:
            ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
            ws.recv()
            ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))
            start = time.time()
            while time.time() - start < 20:
                msg = json.loads(ws.recv())
                if msg.get("method") == "Page.loadEventFired":
                    break
            time.sleep(wait_sec)  # 等 JS 懒加载
            ws.send(json.dumps({
                "id": 3, "method": "Runtime.evaluate",
                "params": {"expression": "document.documentElement.outerHTML"},
            }))
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == 3:
                    return msg.get("result", {}).get("result", {}).get("value", "")
        finally:
            try:
                ws.close()
            except Exception:
                pass
    except Exception as e:
        print(f"    ⚠️ CDP fetch 失败: {e}")
    return ""


def fetch_article_details(url: str) -> dict:
    """HTTP 请求文章页，抓取封面图、原标题、日文摘要。
    对 Yahoo Expert 长文（/expert/articles/）优先走 CDP 以获取 JS 渲染后的图片。"""
    result = {"image_url": "", "original_title": "", "summary": ""}
    try:
        # Yahoo 始终用直连（无代理），用代理会被 block
        resp = _direct_session.get(url, headers={
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

        # 摘要：优先 og:description，否则取正文首段
        og_desc = soup.find("meta", property="og:description")
        if og_desc and og_desc.get("content"):
            result["summary"] = og_desc["content"]
        else:
            article = soup.find("article") or soup.find(class_="article")
            if article:
                p = article.find("p")
                if p:
                    result["summary"] = p.get_text(strip=True)[:600]

        # 发布时间（多级回退：meta → header <time> → footer <time>）
        def _parse_time_text(txt):
            import re as _re
            m = _re.match(r'(\d{1,2})/(\d{1,2}).*?(\d{1,2}):(\d{2})', txt)
            if m:
                month, day, hour, minute = m.groups()
                now = datetime.now()
                return f"{now.year}-{int(month):02d}-{int(day):02d}T{int(hour):02d}:{minute}:00"
            return ""
        pub_time = ""
        # 优先：article:published_time meta
        pub_meta = soup.find("meta", property="article:published_time")
        if pub_meta and pub_meta.get("content"):
            pub_time = pub_meta["content"]
        # 次选：ld+json datePublished（Yahoo Expert 文章的主要来源）
        if not pub_time:
            import json as _json
            for sc in soup.find_all("script", type="application/ld+json"):
                try:
                    d = _json.loads(sc.string or "")
                    if d.get("datePublished"):
                        pub_time = d["datePublished"]; break
                except Exception:
                    pass
        # 兜底：<time> 标签文本
        if not pub_time:
            for time_tag in soup.find_all("time"):
                dt_attr = time_tag.get("datetime", "")
                if dt_attr and dt_attr != "datetime":  # 排除 datetime="datetime" 无效值
                    pub_time = dt_attr; break
                txt = time_tag.get_text(strip=True)
                pub_time = _parse_time_text(txt)
                if pub_time:
                    break
        if pub_time:
            result["pub_time"] = pub_time

        # 正文：提取段落 + 小标题，保留 h2/h3 结构
        body_text = ""
        article_images = []
        article = soup.find("article") or soup.find(class_="article")
        if article:
            body_parts = []
            for elem in article.find_all(['p', 'h2', 'h3', 'h4']):
                t = elem.get_text(strip=True)
                if not t:
                    continue
                if elem.name in ('h2', 'h3', 'h4'):
                    body_parts.append(f'## {t}')  # 用 ## 标记小标题
                elif len(t) > 20:
                    body_parts.append(t)
            body_text = "\n".join(body_parts)

            # 提取文章内嵌图片（优先用 #uamods-article 精确容器）
        story_container = soup.find("article", id="uamods-article") or article
        if story_container:
            skip_kw = ["logo", "icon", "ico_", "banner", "ad/", "sprite", "dummy",
                       "avatar", "profile", "favicon", "tracking", "pixel",
                       "h30.png", "h20.png", "h16.png", "profile_images"]
            for img in story_container.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if not src or not src.startswith("http"):
                    continue
                if any(k in src.lower() for k in skip_kw):
                    continue
                # Twitter 媒体图片（pbs.twimg.com/media/）直接收录，无需尺寸检查
                is_tweet_media = "pbs.twimg.com/media/" in src
                if not is_tweet_media:
                    try:
                        w = int(img.get("width", 0))
                        h = int(img.get("height", 0))
                        if (w and w < 200) or (h and h < 150):
                            continue
                    except (ValueError, TypeError):
                        pass
                if src not in article_images:
                    article_images.append(src)

        # 提取 Twitter/X 嵌入推文链接
        # CDP 渲染后 blockquote 被替换为 iframe，优先从 iframe src 的 ?id= 参数提取
        twitter_embeds = []
        story_container2 = soup.find("article", id="uamods-article") or article
        if story_container2:
            from urllib.parse import parse_qs, urlparse as _urlparse
            # 方案A：CDP 渲染后，从 platform.twitter.com iframe 的 id 参数提取
            # 静态 HTML：从 blockquote.twitter-tweet 提取推文 URL
            for bq in story_container2.find_all("blockquote", class_="twitter-tweet"):
                links = bq.find_all("a", href=True)
                tweet_url = next((a["href"] for a in reversed(links)
                                  if "twitter.com" in a["href"] or "x.com" in a["href"]), "")
                if tweet_url:
                    twitter_embeds.append(tweet_url)
            # CDP 渲染后 blockquote 被替换为 iframe：从 platform.twitter.com iframe 的 id 参数提取
            if not twitter_embeds:
                from urllib.parse import parse_qs, urlparse as _urlparse
                for iframe in story_container2.find_all("iframe"):
                    src = iframe.get("src", "")
                    if "platform.twitter.com/embed" in src:
                        qs = parse_qs(_urlparse(src).query)
                        tweet_id = qs.get("id", [""])[0]
                        if tweet_id:
                            twitter_embeds.append(f"https://x.com/i/web/status/{tweet_id}")
        result["twitter_embeds"] = twitter_embeds

        result["body_text"] = body_text  # 完整正文，不截断（SQLite TEXT 无长度限制）
        result["article_images"] = article_images[:10]  # 最多10张
    except Exception as e:
        print(f"    ⚠️ 抓取文章详情失败: {e}")
    return result


def generate_story_article(title_ja: str, title_zh: str, body_ja: str,
                           twitter_embeds: list[str] | None = None) -> dict | None:
    """生成故事体文章（导语 + 翻译正文 + 结语），风格对齐严肃新闻媒体。

    与 generate_content_and_comment 完全不同的格式，不含娱乐化语气和词汇教学。
    返回 {"title": str, "intro": str, "body": str, "outro": str}
    """
    if not LITELLM_API_KEY:
        return None

    img_note = ""
    if twitter_embeds:
        tweet_info_lines = []
        for i, url in enumerate(twitter_embeds, 1):
            tweet_id = re.search(r'/status/(\d+)', url)
            if not tweet_id:
                tweet_info_lines.append(f"  推文{i}：（来源未知）")
                continue
            meta = _get_tweet_metadata(tweet_id.group(1))
            if meta:
                screen = meta.get('screen_name', '')
                text = meta.get('text', '')
                tweet_info_lines.append(f"  推文{i}：@{screen} — {text}")
            else:
                tweet_info_lines.append(f"  推文{i}：（无法获取内容）")

        tweet_list = "\n".join(tweet_info_lines)
        img_note = (
            f"\n\n原文共嵌入 {len(twitter_embeds)} 条推文，内容如下：\n{tweet_list}\n\n"
            "⚠️ 重要：翻译正文时，每条推文必须在其对应的叙事段落后立即插入图片占位符，"
            "分散分布在全文各处，绝不能将所有占位符集中堆放在正文末尾。\n"
            "格式：【图片N：@screen_name — 一句话说明】（N从1开始按出现顺序递增）\n"
            "占位符单独成行，上下各留一个空行。"
        )

    prompt = f"""你是一名专业翻译和新闻编辑，风格对标《财新》《36氪》《澎湃》等严肃媒体。

请将以下日文新闻长文翻译并改写为适合中文读者的故事体文章。

【第一步：判断文章类型】
阅读原文后，判断属于以下哪种类型，并将类型名写入 JSON 的 "story_type" 字段：
- 对谈型：以多人问答/对话为主要形式（采访、座谈）
- 叙事型：以单一人物或事件为主线的叙述性文章（人物特写、事件报道）
- 对比型：文中明确呈现多人/多方的命运差异或观点冲突
- 评介型：对作品（专辑、歌单、影视、书籍）进行介绍或分析

【第二步：按类型生成标题】
不超过25字，全部简体中文，人名不加【】括号，参考原标题：{title_zh}

对谈型 → 从以下选一种最合适的：
  ①「人物A与人物B谈X：核心话题一句话」
  ②「从X到Y：人物A和人物B共同面对的事」
  ③「人物A：用一句话概括她/他在这次对话中说的最重要的话」

叙事型 → 从以下选一种最合适的：
  ①「一件事/一句话+改变了什么」（如"伯乐一句话，救了沉底十年的演员"）
  ②「人物A的X年：核心转折或结果」
  ③ 直接用动词开头描述核心事件（如"骂完东大生，他被自己的旧账淹没了"）

对比型 → 从以下选一种最合适的：
  ①「人物A和人物B，走向了两个结局」
  ②「同一件事：A的结果 vs B的结果」
  ③「人物A×人物B：命运分岔的那一刻」（仅此类型可用×，且不加——）

评介型 → 从以下选一种最合适的：
  ①「X首含"关键词"的歌，藏着怎样的情感」
  ②「从选曲看人物A：她/他想说什么」
  ③「专辑/歌单名：用音乐讲述的那些故事」

通用禁止：×和——同时出现、"深度""探讨""揭秘""光与影""温柔与坚韧"等抽象对仗套话。

【导语要求】
一句话金句，不超过30字，不出现标题已有的词语。
从本文的核心悬念、规律、反差或制度空白切入——揭示一个读者读完全文才能理解的隐含逻辑。
不得是对标题的重复或扩写，不得是"欢迎阅读"等套话。

【正文要求】
完整翻译原文，保留叙事结构、因果逻辑和原文的小标题。原文中以 ## 开头的行是小标题（section header），翻译时在对应位置保留并用 ## 前缀标记（如 ## 小标题译文）。用简洁有力的句子，避免口语化。{img_note}

【结语要求】
1-2句话，点睛式收尾。可以是作者的判断、对行业的启示、或留给读者的问题。不得是「欢迎评论」等套话。

【人物/团体标签】
提取文章涉及的所有主要人物姓名、偶像团体名（用中文名或通用译名），以逗号分隔字符串输出。

---
日文原文标题：{title_ja}
日文正文：
{body_ja}
---

严格按如下 JSON 格式输出（body 中图片占位符示例：【图片1：橋本環奈14岁现场照爆红】）：
{{"story_type": "对谈型|叙事型|对比型|评介型", "title": "标题", "intro": "导语", "body": "正文（有图片时含【图片N：描述】占位符）", "outro": "结语", "persons": "人物1,人物2,团体名"}}"""

    result = call_litellm(
        prompt,
        system_prompt="你是严肃新闻媒体编辑。直接输出JSON，不要任何前置说明或思考过程。",
        max_tokens=6000,
        temperature=0.4,
        response_format={"type": "json_object"},
    )
    if not result:
        print(f"    ⚠️ 故事体 LLM 调用返回空")
        return None
    import json as _j, re as _re
    # 先尝试直接解析
    try:
        data = _j.loads(result)
        if data.get('body'):
            return data
    except Exception:
        pass
    # LLM 可能先输出思考过程，再输出 JSON——从文本中提取最后一个完整 JSON 对象
    try:
        # 找最后一个 { ... } 包含 "body" 关键字的块
        matches = list(_re.finditer(r'\{[\s\S]*?"body"[\s\S]*?\}(?=\s*$|\s*\n)', result))
        if not matches:
            # 宽松匹配：找所有 {...} 取最长的
            matches = list(_re.finditer(r'\{[\s\S]+\}', result))
        if matches:
            candidate = matches[-1].group()
            data = _j.loads(candidate)
            if data.get('body'):
                return data
    except Exception:
        pass
    print(f"    ⚠️ 故事体 JSON 解析失败，原始返回前300: {result[:300]}")
    return None


def _process_story_path(news: dict, keyword: str, extra_tags: list) -> dict:
    """故事体文章的完整独立处理路径：生成 → 评分 → 分类 → 图片 → 返回。"""
    story = generate_story_article(
        news['title_ja'], news['title_zh'],
        body_ja=news.get('content_ja', '') or news.get('body_text', ''),
        twitter_embeds=news.get('_twitter_embeds', []),
    )
    if not story:
        print("    ⚠️ 故事体生成失败，回退到资讯体")
        return _fallback_to_news(news, keyword, extra_tags)

    news['title']    = story.get('title', news['title_zh'])
    news['title_zh'] = story.get('title', news['title_zh'])
    news['content']  = f"{story['intro']}\n\n{story['body']}\n\n{story['outro']}"
    news['comment']  = story.get('outro', '')
    news['summary']  = story.get('intro', '')[:100]
    news['category'] = '新闻'
    news['is_long_form'] = True
    news['format_suitability'] = ['story']
    if story.get('story_type'):
        news['story_type'] = story['story_type']
    print(f"    故事体生成完成 [{story.get('story_type','?')}]: {len(news['content'])} 字")

    # 评分
    quality = evaluate_quality(news['title_zh'], news['content'], news['comment'])
    news['_quality']      = quality
    news['title_score']   = quality.get('title_score', 0)
    news['content_score'] = quality.get('content_score', 0)
    print(f"    📊 评分: 标题{news['title_score']:.2f} 内容{news['content_score']:.2f}")

    # 低分诊断（与资讯体一致）
    try:
        from scripts.scoring import diagnose_low_score, Action
        from scripts.sqlite_db import get_config
        publish_threshold = get_config("publish_threshold", default=3.0)
        retry_threshold = get_config("retry_threshold", default=2.0)
        action = diagnose_low_score(
            {"content_score": news["content_score"],
             "title_score": news["title_score"],
             "gallery_images": news.get("gallery_images", [])},
            quality["scores"],
            publish_threshold=publish_threshold, retry_threshold=retry_threshold,
        )
        if action == Action.DISCARD:
            news['_discard'] = True
            print(f"    📊 诊断: {action.value}")
    except Exception:
        pass

    # 分类 + 标签（story 体裁：用日文原文前500字做关键词匹配，避免长文噪音误匹配）
    classify_text = (news.get('content_ja', '') or '')[:500]
    category, tags = auto_classify(news['title_ja'], classify_text, keyword=keyword)
    news['category'] = category or '新闻'
    # 合并 auto_classify 标签 + LLM 提取的人物/团体标签
    person_tags = [p.strip() for p in story.get('persons', '').split(',') if p.strip()]
    news['tags'] = list({*tags, *person_tags, *extra_tags, *(news.get('tags') or [])})

    # 封面图（与资讯体一致）
    if news.get('original_image_url') and not news.get('image_url'):
        news['image_url'] = news['original_image_url']

    # 文章内嵌图片下载（Yahoo 文章本体图片）
    article_images = news.get('_article_images', [])
    if article_images:
        _download_article_images(news, article_images)

    # Twitter 嵌入推文媒体下载
    twitter_embeds = news.get('_twitter_embeds', [])
    if twitter_embeds:
        _download_twitter_embeds(news, twitter_embeds)

    # 生成短配文
    try:
        news['video_caption'] = generate_video_caption(
            news['title_zh'], news.get('content', ''))
    except Exception:
        news['video_caption'] = ""

    # 写入 DB（故事体走了独立路径，不会回到 process_news_item 的 insert_news）
    if STORAGE_BACKEND == "sqlite":
        try:
            from sqlite_db import insert_news as _sql_insert
            _sql_insert({
                'title': news.get('title_zh', news.get('title_ja', '')),
                'title_ja': news.get('title_ja', ''),
                'link': news.get('link', ''),
                'source': news.get('source', ''),
                'category': news.get('category', '新闻'),
                'content': news.get('content', ''),
                'comment': news.get('comment', ''),
                'summary': news.get('summary', ''),
                'tags': news.get('tags', []),
                'image_url': news.get('image_url', ''),
                'original_image_url': news.get('original_image_url', ''),
                'video_caption': news.get('video_caption', ''),
                'content_ja': news.get('body_text', '') or news.get('ja_summary', ''),
                'pub_time': news.get('pub_time', ''),
                'title_score': news.get('title_score', 0),
                'content_score': news.get('content_score', 0),
                'key': extract_key_from_url(news.get('link', '')),
                'status': 'discarded' if news.get('_discard') else 'active',
                'fetch_by': keyword if keyword else 'recomm',
                'format_suitability': news.get('format_suitability', ['story']),
                'is_long_form': news.get('is_long_form', True),
            })
            if news.get('_discard'):
                print(f"    🗑️ 已标记为 discarded")
            news_key = extract_key_from_url(news.get('link', ''))
            # 写入评分明细
            quality = news.get('_quality', {})
            if quality.get('scores'):
                from sqlite_db import upsert_score_dims
                upsert_score_dims(news_key, quality['scores'],
                                 dim_version=quality.get('_dim_version', ''))
                print(f"    📊 评分明细已写入: {len(quality['scores'])}项")
        except Exception as e:
            print(f"    ⚠️ 故事体入库失败: {e}")

    return news


def _fallback_to_news(news: dict, keyword: str, extra_tags: list) -> dict:
    """故事体失败时的回退：用标准资讯体路径处理。"""
    news['format_suitability'] = ['news']
    news['is_long_form'] = False
    return process_news_item(news, keyword=keyword, extra_tags=extra_tags)


def _download_article_images(news: dict, image_urls: list[str]) -> None:
    """将 Yahoo 文章内嵌图片下载到本地缓存，写入 news['gallery_images']。
    保护逻辑：gallery_images 已有内容则跳过（不依赖 meta.json，以 DB 字段为准）。
    gallery_fetch.py 的「重新下载」功能走自己的 meta.json 路径，互不干扰。
    """
    # 已有图片 → 不覆盖
    existing = news.get('gallery_images')
    if existing and (isinstance(existing, list) and len(existing) > 0):
        return

    key = news.get('key', '') or extract_key_from_url(news.get('link', ''))
    if not key:
        return

    cache_dir = Path(os.path.expanduser(GALLERY_CACHE_DIR)) / key
    cache_dir.mkdir(parents=True, exist_ok=True)

    local_paths = []
    for i, url in enumerate(image_urls[:10]):
        try:
            # Twitter 媒体图片用 unified_media_downloader.download_direct
            if "pbs.twimg.com/media/" in url:
                try:
                    from scripts.unified_media_downloader import download_direct
                    clean_url = url.split("&name=")[0] + "&name=large" if "?" in url else url
                    files = download_direct(clean_url, cache_dir)
                    for f in files:
                        fpath = Path(f)
                        if fpath.stat().st_size >= 20_000:
                            dest = cache_dir / f"article_{i:02d}{fpath.suffix or '.jpg'}"
                            fpath.rename(dest)
                            local_paths.append(str(dest))
                            print(f"    📷 推文图片[{i}]: {dest.name} ({dest.stat().st_size//1024}KB)")
                except Exception as e:
                    print(f"    ⚠️ 推文图片下载失败[{i}]: {e}")
                continue

            resp = _direct_session.get(url, headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://news.yahoo.co.jp/",
            }, timeout=15)
            if resp.status_code != 200:
                continue
            # 过滤过小的文件（< 20KB 通常是 icon/缩略图）
            if len(resp.content) < 20_000:
                continue
            ext = url.rsplit('.', 1)[-1].split('?')[0]
            if ext not in ('jpg', 'jpeg', 'png', 'webp'):
                ext = 'jpg'
            fpath = cache_dir / f"article_{i:02d}.{ext}"
            fpath.write_bytes(resp.content)
            local_paths.append(str(fpath))
            print(f"    📷 文章图片[{i}]: {fpath.name} ({len(resp.content)//1024}KB)")
        except Exception as e:
            print(f"    ⚠️ 文章图片下载失败: {e}")

    if local_paths:
        news['gallery_images'] = local_paths
        print(f"    ✅ 文章图片缓存完成: {len(local_paths)} 张")


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


def _get_tweet_image_urls(tweet_id: str) -> list[str]:
    """调 Twitter syndication API 获取推文图片 URL 列表（无需认证）。
    支持：mediaDetails（photo/gif）、Twitter Card 缩略图（card_img）。
    """
    try:
        api_url = (f"https://cdn.syndication.twimg.com/tweet-result"
                   f"?id={tweet_id}&lang=ja"
                   f"&features=tfw_timeline_list%3A%3Btfw_follower_count_sunset%3Atrue"
                   f"&token=4")
        resp = requests.get(api_url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://platform.twitter.com/",
        }, timeout=10)
        if resp.status_code != 200:
            return []
        data = resp.json()
        urls = []

        # 1. mediaDetails: photo 类型（主要图片）
        for m in data.get("mediaDetails", []):
            if m.get("type") in ("photo", "animated_gif"):
                base = m.get("media_url_https", "")
                if base:
                    urls.append(f"{base}:large")

        # 2. Twitter Card 缩略图（card_img — 链接预览图，适用于没有 photo 的推文）
        # 结构：card.legacy.binding_values[key=thumbnail_image*].value.image_value.url
        if not urls:
            card = data.get("card", {})
            bvs = (card.get("legacy") or {}).get("binding_values", [])
            for bv in bvs:
                bv_key = bv.get("key", "")
                if "thumbnail_image" not in bv_key:
                    continue
                iv = (bv.get("value") or {}).get("image_value", {})
                img_url = iv.get("url", "")
                w = iv.get("width", 0)
                h = iv.get("height", 0)
                # 请求 large 尺寸
                if img_url:
                    img_url = re.sub(r'name=\w+', 'name=large', img_url)
                if img_url and w >= 200 and h >= 150:
                    urls.append(img_url)
                    break  # 只取最高分辨率的那张

        return urls
    except Exception:
        return []


def _get_tweet_metadata(tweet_id: str) -> dict:
    """调 syndication API 获取推文作者名、screen_name、正文摘要。
    返回 {"author": str, "screen_name": str, "text": str}，失败返回空 dict。
    """
    try:
        api_url = (f"https://cdn.syndication.twimg.com/tweet-result"
                   f"?id={tweet_id}&lang=ja"
                   f"&features=tfw_timeline_list%3A%3Btfw_follower_count_sunset%3Atrue"
                   f"&token=4")
        resp = requests.get(api_url, headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://platform.twitter.com/",
        }, timeout=10)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        user = data.get("user", {})
        author = user.get("name", "")
        screen_name = user.get("screen_name", "")
        text = data.get("text", "").strip()
        # 去掉末尾的 t.co 短链
        text = re.sub(r'\s*https://t\.co/\S+$', '', text).strip()
        return {"author": author, "screen_name": screen_name, "text": text[:80]}
    except Exception:
        return {}


def _download_twitter_embeds(news: dict, tweet_urls: list[str]) -> None:
    """通过 syndication API 获取推文图片 URL，直接下载，追加到 news['gallery_images']。"""
    import re as _re
    key = news.get('key', '') or extract_key_from_url(news.get('link', ''))
    if not key:
        return
    cache_dir = Path(os.path.expanduser(GALLERY_CACHE_DIR)) / key
    cache_dir.mkdir(parents=True, exist_ok=True)

    existing = list(news.get('gallery_images') or [])
    new_paths = []
    file_idx = len(existing)

    tweet_meta_map = news.get('_tweet_meta', {})  # {tweet_id: {author, screen_name, text}}

    for tweet_url in tweet_urls[:7]:
        m_id = re.search(r'/status/(\d+)', tweet_url)
        if not m_id:
            continue
        tweet_id = m_id.group(1)

        # 获取推文元数据（作者 + 正文）
        meta = _get_tweet_metadata(tweet_id)
        if meta:
            tweet_meta_map[tweet_id] = meta
            screen = meta.get('screen_name', tweet_id)
            print(f"    📝 推文元数据: @{screen} — {meta.get('text','')[:40]}")

        img_urls = _get_tweet_image_urls(tweet_id)
        for img_url in img_urls:
            try:
                resp = requests.get(img_url, headers={
                    "User-Agent": "Mozilla/5.0",
                    "Referer": "https://twitter.com/",
                }, timeout=20)
                if resp.status_code != 200 or len(resp.content) < 20_000:
                    continue
                fpath = cache_dir / f"tweet_{file_idx:02d}.jpg"
                fpath.write_bytes(resp.content)
                new_paths.append(str(fpath))
                file_idx += 1
                screen = meta.get('screen_name', tweet_id) if meta else tweet_id
                print(f"    🐦 推文图片: {fpath.name} ({len(resp.content)//1024}KB) @{screen}")
            except Exception as e:
                print(f"    ⚠️ 推文图片下载失败: {e}")

    news['_tweet_meta'] = tweet_meta_map  # 保存元数据供后续使用

    if new_paths:
        news['gallery_images'] = existing + new_paths
        print(f"    ✅ 推文媒体缓存完成: {len(new_paths)} 张")


def process_news_item(news: dict, no_translate: bool = False,
                      extra_tags: list[str] | None = None,
                      keyword: str = "") -> dict:
    """对单条新闻执行完整后处理（翻译→AI生成→分类→文章详情→封面图上传）。
    
    原地修改并返回 news dict。
    """
    # 1. 翻译 + AI 生成
    details = {}  # 默认空，当不翻译时 pub_time 可用 now()
    if LITELLM_API_KEY and not no_translate:
        # 先抓文章详情，用 og:title 替换 Yahoo 搜索页的拼接/截断标题
        print("    抓取文章详情（供AI参考）...")
        details = fetch_article_details(news['link'])
        news['original_title']     = details.get("original_title", news['title_ja'])
        news['ja_summary']         = details.get("summary", "")
        news['original_image_url'] = details.get("image_url", "")
        news['body_text']          = details.get("body_text", "")
        # 日文原文同步写入 content_ja（供后续评分和体裁判断使用）
        if details.get("body_text") and not news.get('content_ja'):
            news['content_ja'] = details['body_text']
        # Twitter 嵌入推文 + 文章内嵌图片（供 story 路径使用）
        news['_twitter_embeds']  = details.get("twitter_embeds", [])
        news['_article_images']  = details.get("article_images", [])

        # 长文文章：将文章内嵌图片下载到本地缓存（gallery_images 已有内容则跳过）
        article_images = details.get("article_images", [])
        if article_images and news.get('is_long_form'):
            _download_article_images(news, article_images)

        og_title = details.get("original_title", "")
        if og_title and len(og_title) > 10:
            og_title_clean = re.sub(r'\s*[-—|]\s*Yahoo!.*$', '', og_title).strip()
            og_title_clean = re.sub(r'\s*（[^）]*Yahoo[^）]*）\s*$', '', og_title_clean).strip()
            if len(og_title_clean) > len(news['title_ja']) * 0.5:
                news['title_ja'] = og_title_clean

        # 文章被 region block 或无法访问时，跳过本条（没有正文没法生成靠谱内容）
        if not news['body_text'] or len(news['body_text']) < 50:
            print("    ⚠️ 文章无法完整访问（可能被 region block），跳过本条")
            news['_skip'] = True
            return news

        # 翻译 + 体裁前置判断（一次 LLM 调用）
        print("    翻译+体裁判断...")
        tc = translate_and_classify(
            news['title_ja'],
            body_ja=news.get('content_ja', '') or news.get('body_text', ''),
        )
        news['title_zh'] = tc['title_zh']
        news['format_suitability'] = tc['format_suitability']  # list[str]

        # 长文检测：分页文章 OR 正文超过800字的单页长文
        body_len = len(news.get('body_text', '') or news.get('content_ja', ''))
        if not news.get('is_long_form') and body_len > 800:
            news['is_long_form'] = True
            print(f"    📄 检测为长文（正文{body_len}字）")

        if news.get('is_long_form'):
            qa_markers = sum(1 for _ in __import__('re').finditer(
                r'(?:^|\n)\s*[Ｑq][:：]', news.get('content_ja', '')))
            if qa_markers < 3 and 'story' not in news['format_suitability']:
                news['format_suitability'] = ['story'] + news['format_suitability']

        # 从 format_suitability 中取体裁：
        # 优先使用规划层指定的 target_format（若 LLM 认为适用），否则用 LLM 的第一推荐
        target_fmt = news.get('_target_format', '')
        suitability = news['format_suitability'] or ['news']
        if target_fmt and target_fmt in suitability:
            selected_format = target_fmt
        else:
            selected_format = suitability[0]
        news['_selected_format'] = selected_format

        print(f"    体裁: {selected_format} (适用: {suitability}, 目标: {target_fmt or '无'})")
        print("    生成内容...")

        # pub_time 在 story/非story 路径前统一处理，避免 story 提前 return 导致遗漏
        _raw_time = details.get('pub_time', '')
        if _raw_time:
            try:
                from datetime import datetime as _dt
                _t = _dt.fromisoformat(_raw_time.replace('Z', '+00:00'))
                news['pub_time'] = _t.strftime('%Y.%m.%d %H:%M')
            except Exception:
                news['pub_time'] = datetime.now().strftime('%Y.%m.%d %H:%M')
        elif not news.get('pub_time'):
            news['pub_time'] = datetime.now().strftime('%Y.%m.%d %H:%M')

        # ── story 体裁：完全独立路径，生成完直接返回 ──────────────────────
        if selected_format == 'story':
            return _process_story_path(news, keyword, extra_tags or [])

        # ── 资讯体 / 盘点体 / 对比体：原有路径 ──────────────────────────────
        generated = generate_content_and_comment(
            news['title_ja'], news['title_zh'],
            ja_summary=news.get('ja_summary', ''),
            keyword=keyword,
            body_text=news.get('body_text', ''),
            hint=news.get('_regen_hint', ''),
            content_format=selected_format,
        )
        if generated is None:
            print("    ⚠️ LLM 调用失败，跳过此条新闻")
            news['_skip'] = True
            return news
        seo_title, summary, content, comment, _, topic_tags = generated
        news['title_zh'] = seo_title
        news['title'] = seo_title
        news['summary']  = summary
        news['content']  = content
        news['comment']  = comment

        # 质量评分 + 低分诊断 + 最多 2 次重试
        from scripts.scoring import diagnose_low_score, Action, get_failed_dims
        from scripts.agent_tools import regenerate_with_hint
        from scripts.sqlite_db import get_config

        publish_threshold = get_config("publish_threshold", default=3.0)
        retry_threshold = get_config("retry_threshold", default=2.0)
        final_action = Action.PUBLISH

        for attempt in range(3):
            quality = evaluate_quality(seo_title, content, comment,
                                       news.get('title_ja', ''), news.get('body_text', ''))
            action = diagnose_low_score(
                {"content_score": quality["content_score"],
                 "title_score": quality["title_score"],
                 "gallery_images": news.get("gallery_images", [])},
                quality["scores"],
                publish_threshold=publish_threshold,
                retry_threshold=retry_threshold,
            )
            final_action = action
            if action == Action.REGENERATE and attempt < 2:
                failed = get_failed_dims(quality["scores"])
                print(f"    🔄 低分重试 (attempt {attempt+1}/3): {failed}")
                news = regenerate_with_hint(news, failed)
                regen_hint = news.get("_regen_hint", "")
                # 重新生成内容（注入修正提示）
                generated = generate_content_and_comment(
                    news['title_ja'], news['title_zh'],
                    ja_summary=news.get('ja_summary', ''),
                    keyword=keyword,
                    body_text=news.get('body_text', ''),
                    hint=regen_hint,
                )
                if generated is None:
                    print("    ⚠️ 重生成失败，保留当前内容")
                    break
                seo_title, summary, content, comment, _, topic_tags = generated
                news['title_zh'] = seo_title
                news['summary'] = summary
                news['content'] = content
                news['comment'] = comment
            else:
                break

        news['_title_score'] = quality['title_score']
        news['_content_score'] = quality['content_score']
        news['_quality'] = quality
        print(f"    📊 评分: 标题{quality['title_score']} 内容{quality['content_score']} → {final_action.value}")
        if final_action == Action.DISCARD:
            news['_discard'] = True
        elif final_action == Action.HUMAN_REVIEW:
            news['_needs_review'] = True
            print(f"    ⚠️ 需要人工审核（评分边界，原因不明确）")
            # 异步通知飞书（fire-and-forget，失败不影响主流程）
            try:
                from scripts.feishu_bot import send_text, FEISHU_OPERATOR_OPEN_ID
                if FEISHU_OPERATOR_OPEN_ID:
                    msg = (
                        f"⚠️ 文章需要人工审核\n"
                        f"标题：{news.get('title_zh', news.get('title_ja', ''))[:40]}\n"
                        f"评分：标题{quality['title_score']:.1f} / 内容{quality['content_score']:.1f}\n"
                        f"key：{news.get('key', '')[:16]}..."
                    )
                    send_text(FEISHU_OPERATOR_OPEN_ID, msg)
            except Exception as _fe:
                print(f"    ℹ️ 飞书通知失败（不影响处理）: {_fe}")
        if quality['title_score'] < 2.0:
            print(f"    ⚠️ 标题质量偏低，建议人工复审")
        news['video_caption'] = ""  # 先占位，tags 确定后再填
    else:
        news.setdefault('title_zh', news['title_ja'])
        news.setdefault('content', f"• {news['title_ja']}")
        news.setdefault('comment', "")
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

    # 短配文（tags 确定后生成）
    if LITELLM_API_KEY and not no_translate and 'video_caption' in news:
        print("    生成短配文...")
        news['video_caption'] = generate_video_caption(
            news['title_zh'], news.get('summary', ''),
            news.get('content', ''), tags,
        )
    # pub_time：story 路径已提前设置，此处仅处理非 story 路径
    if not news.get('pub_time'):
        raw_time = details.get('pub_time', '')
        if raw_time:
            try:
                from datetime import datetime as dt
                t = dt.fromisoformat(raw_time.replace('Z', '+00:00'))
                fmt = '%Y.%m.%d %H:%M' if STORAGE_BACKEND == 'sqlite' else '%Y.%m.%d'
                news['pub_time'] = t.strftime(fmt)
            except Exception:
                news['pub_time'] = datetime.now().strftime('%Y.%m.%d %H:%M')
        else:
            date_fmt = '%Y.%m.%d %H:%M' if STORAGE_BACKEND == 'sqlite' else '%Y.%m.%d'
            news['pub_time'] = datetime.now().strftime(date_fmt)
    if keyword:
        news['keyword'] = keyword

    # 3. Cloudinary 上传（文章详情已在 AI 生成前抓取）
    news.setdefault('original_title', news['title_ja'])
    news.setdefault('ja_summary', '')
    news.setdefault('original_image_url', '')

    # 4. 封面图处理（SQLite 下载到本地，Notion 上传 Cloudinary）
    if news['original_image_url']:
        if STORAGE_BACKEND == 'sqlite':
            news['image_url'] = news['original_image_url']  # 暂存原URL，后面下载到本地
        else:
            cdn_url = upload_cover_image(news['original_image_url'])
            news['image_url'] = cdn_url if cdn_url else news['original_image_url']
    else:
        news.setdefault('image_url', "")
        print("    封面图: ⚠️ 未找到")

    print(f"    分类: {category} | 标签: {', '.join(tags[:3])}")

    # 存储后端写入
    if STORAGE_BACKEND == "sqlite":
        try:
            from sqlite_db import insert_news as sqlite_insert
            sqlite_insert({
                'title': news.get('title_zh', news['title_ja']),
                'title_ja': news.get('title_ja', ''),
                'link': news.get('link', ''),
                'source': news.get('source', ''),
                'category': category,
                'content': news.get('content', ''),
                'comment': news.get('comment', ''),
                'summary': news.get('summary', ''),
                'tags': tags,
                'image_url': news.get('image_url', ''),
                'original_image_url': news.get('original_image_url', ''),
                'video_path': news.get('video_path', ''),
                'video_caption': news.get('video_caption', ''),
                'content_ja': news.get('body_text', '') or news.get('ja_summary', ''),
                'pub_time': news.get('pub_time', ''),
                'title_score': news.get('_title_score', 0),
                'content_score': news.get('_content_score', 0),
                'key': extract_key_from_url(news.get('link', '')),
                'status': 'discarded' if news.get('_discard') else 'active',
                'fetch_by': keyword if keyword else 'recomm',
            })
            if news.get('_discard'):
                print(f"    🗑️ 已标记为 discarded")
            news_key = extract_key_from_url(news.get('link', ''))
            # 封面图存本地（SQLite 专属）
            cover_url = news.get('image_url', '')
            if cover_url and cover_url.startswith('http'):
                try:
                    import requests as _req, hashlib as _hl
                    cover_dir = Path(os.path.expanduser(GALLERY_CACHE_DIR)) / news_key
                    cover_dir.mkdir(parents=True, exist_ok=True)
                    resp = _req.get(cover_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=15)
                    ext = cover_url.rsplit('.', 1)[-1].split('?')[0] or 'jpg'
                    if ext not in ('jpg','jpeg','png','webp'): ext = 'jpg'
                    cover_path = cover_dir / f'cover.{ext}'
                    cover_path.write_bytes(resp.content)
                    from sqlite_db import update_news as _sql_update
                    _sql_update(news_key, {'image_url': str(cover_path)})
                    print(f"    封面图本地: {cover_path}")
                except Exception as e:
                    print(f"    ⚠️ 封面本地下载失败: {e}")
            # 写入评分维度（需在 insert_news 之后，满足外键约束）
            quality_scores = news.get('_quality', {}).get('scores')
            if quality_scores:
                try:
                    from sqlite_db import upsert_score_dims
                    upsert_score_dims(news_key, quality_scores)
                    print(f"    📊 评分明细已写入: {len(quality_scores)}项")
                except Exception as e:
                    print(f"    ⚠️ 评分写入失败: {e}")
        except ImportError:
            pass

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
        resp = _direct_session.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=headers, json=query,
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        for page in data.get("results", []):
            # 兼容两种去重逻辑：
            # 1. key 字段已存在 → 直接收集
            # 2. 没有 key 字段但有原文链接 → 从 URL 提取 key
            key_prop = page.get("properties", {}).get("key", {}).get("rich_text", [])
            key = "".join(r.get("plain_text", "") for r in key_prop)
            if not key:
                url = page.get("properties", {}).get("原文链接", {}).get("url", "")
                key = extract_key_from_url(url) if url else ""
            if key:
                keys.add(key)
        has_more = data.get("has_more", False)
        start_cursor = data.get("next_cursor")

    # 合并 SQLite 去重 key（created_at 格式为 YYYY-MM-DD）
    if STORAGE_BACKEND == "sqlite":
        try:
            from sqlite_db import load_today_keys as sqlite_keys
            sqlite_today = datetime.now().strftime('%Y-%m-%d')
            keys |= sqlite_keys(sqlite_today)
        except ImportError:
            pass

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
    resp = _direct_session.post(
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

    if news.get("video_caption"):
        blocks.append({"object": "block", "type": "heading_3", "heading_3": {
            "rich_text": [{"type": "text", "text": {"content": "🎬 短配文"}}]
        }})
        blocks.append({"object": "block", "type": "callout", "callout": {
            "icon": {"type": "emoji", "emoji": "🎬"},
            "rich_text": [{"type": "text", "text": {"content": news["video_caption"][:500]}}],
        }})
        blocks.append({"object": "block", "type": "divider", "divider": {}})

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

    blocks.append({"object": "block", "type": "divider", "divider": {}})
    blocks.append({"object": "block", "type": "heading_3", "heading_3": {
        "rich_text": [{"type": "text", "text": {"content": "📰 原文"}}]
    }})
    original_title = news.get("original_title") or news.get("title_ja", "")
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
    key       = extract_key_from_url(news.get("link", ""))
    image_url = news.get("image_url", "")
    orig_img  = news.get("original_image_url", "")

    props: dict = {
        "Name":     {"title":     [{"text": {"content": news.get("title_zh", news["title_ja"])}}]},
        "key":      {"rich_text": [{"text": {"content": key}}]},
        "分类":     {"select":    {"name": news.get("category", "经济")}},
        "标签":     {"multi_select": [{"name": t} for t in news.get("tags", [])]},
        "来源":     {"rich_text": [{"text": {"content": news.get("source", "Yahoo Japan")}}]},
        "发布时间": {"rich_text": [{"text": {"content": news.get("pub_time", datetime.now().strftime('%Y.%m.%d'))}}]},
        "原文链接": {"url": news["link"]},
    }
    if news.get('_title_score') is not None:
        props["标题评分"] = {"number": news['_title_score']}
    if news.get('_content_score') is not None:
        props["内容评分"] = {"number": news['_content_score']}
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

    resp = _direct_session.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    if resp.status_code != 200:
        print(f"    ⚠️ Notion 错误: {resp.status_code} {resp.text[:200]}")
        return ""
    return resp.json().get("id", "")


def push_with_gallery(news: dict, existing_keys: set | None = None) -> bool:
    """推送到 Notion/SQLite 并检测图集外链。返回是否成功"""
    key = extract_key_from_url(news["link"])
    if STORAGE_BACKEND == "notion":
        page_id = push_to_notion(news)
        if not page_id:
            print("    ❌ 推送失败")
            return False
        print("    ✅ 已推送到 Notion")
    else:
        print("    ✅ 已保存到 SQLite")
    if existing_keys is not None:
        existing_keys.add(key)

    # 图集检测
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from gallery_fetch import detect_gallery_link, update_notion_gallery_url
        gallery_url = detect_gallery_link(news["link"])
        if gallery_url:
            if STORAGE_BACKEND == "notion":
                update_notion_gallery_url(page_id, gallery_url)
            else:
                from sqlite_db import update_news
                update_news(key, {'gallery_url': gallery_url})
                print(f"    📸 图集: {gallery_url}")
        else:
            print("    — 未检测到图集外链")
    except Exception as e:
        print(f"    ⚠️ 图集检测失败: {e}")
    return True


def push_stub_to_notion(news: dict, existing_keys: set | None = None) -> bool:
    """推送最小化存根到 Notion/SQLite（仅用于去重记录：翻译标题 + 原地址 + key）"""
    if STORAGE_BACKEND != "notion":
        return True  # SQLite 已通过 process_news_item 写入
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        return False
    key = extract_key_from_url(news["link"])
    if existing_keys and key in existing_keys:
        return False
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    title = news.get("title_zh") or news.get("title_ja", "")
    props: dict = {
        "Name":     {"title":     [{"text": {"content": title[:20]}}]},
        "key":      {"rich_text": [{"text": {"content": key}}]},
        "分类":     {"select":    {"name": "存档"}},
        "原文链接": {"url": news["link"]},
        "来源":     {"rich_text": [{"text": {"content": news.get("source", "Yahoo Japan")}}]},
        "发布时间": {"rich_text": [{"text": {"content": news.get("pub_time", datetime.now().strftime('%Y.%m.%d'))}}]},
    }
    blocks = [
        {"object": "block", "type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": "🔗 原文链接：", "link": {"url": news["link"]}}}
        ]}}
    ]
    payload = {"parent": {"database_id": NOTION_DATABASE_ID}, "properties": props, "children": blocks}
    resp = _direct_session.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
    if resp.status_code != 200:
        return False
    if existing_keys is not None:
        existing_keys.add(key)
    return True


def check_chrome_cdp() -> bool:
    """检查 Chrome CDP 是否可用，未运行时自动启动，打印状态并返回 bool"""
    import chrome_launcher
    headless = os.environ.get("CDP_HEADLESS", "").lower() in ("1", "true", "yes")
    if chrome_launcher.ensure_chrome(port=CDP_PORT, headless=headless):
        print("✅ Chrome 已就绪")
        return True
    print("❌ Chrome 无法启动")
    return False
