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
import random

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
    proxy_url = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY") or ""
    if not proxy_url:
        print("ℹ️ 未配置代理，使用直连")
        return True

    # 先测代理
    try:
        r = requests.get("https://news.yahoo.co.jp/", timeout=8)
        if r.status_code == 200:
            print(f"✅ 代理可用 ({proxy_url})")
            return True
    except Exception:
        pass

    # 代理不通，测一下直连是否可行
    print(f"\n⚠️  代理不可用 ({proxy_url})")
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



def call_litellm(prompt: str, system_prompt: str = "", max_tokens: int = 1000) -> str:
    """调用 LiteLLM API，返回文本；未配置或失败时返回空字符串"""
    if not LITELLM_API_KEY:
        return ""
    try:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        resp = _direct_session.post(
            f"{LITELLM_URL}/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {LITELLM_API_KEY}"},
            json={"model": LITELLM_MODEL, "messages": messages, "max_tokens": max_tokens, "temperature": 0.7},
            timeout=(30, 600),
        )
        if resp.status_code == 200:
            msg = resp.json().get("choices", [{}])[0].get("message", {})
            # 优先取 content（最终回答），不存在时才取 reasoning_content
            content = msg.get("content")
            if not content:  # None or empty string
                content = msg.get("reasoning_content", "")
            return content.strip()
        print(f"    ⚠️ LiteLLM 错误: {resp.status_code}")
    except Exception as e:
        print(f"    ⚠️ LiteLLM 调用失败: {e}")
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

    result = call_litellm(prompt, max_tokens=3000)
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


def translate_title(title_ja: str) -> str:
    """将日文标题翻译为中文（默认用 LLM，可开关切回 Google）"""
    use_google = os.environ.get("USE_GOOGLE_TRANSLATE", "").lower() in ("1", "true", "yes")

    if use_google:
        try:
            from deep_translator import GoogleTranslator
            result = GoogleTranslator(source='ja', target='zh-CN').translate(title_ja)
            if result and len(result) > 2:
                return result
        except Exception:
            pass

    # LLM 翻译（max_tokens 设大到 800，保证推理模型有空间同时输出 reasoning + content）
    if LITELLM_API_KEY:
        result = call_litellm(
            prompt=f"将以下日文翻译为中文。只输出译文，不要解释。\n\n{title_ja}",
            system_prompt="你是日译中翻译器。只输出中文译文，不要加任何前缀、解释或描述。",
            max_tokens=500,
        )
        if result and len(result) > 2 and result != title_ja:
            result = result.strip()
            # 洗掉模型偶尔带的格式前缀（含长句描述型前缀）
            prefixes = [
                "以下是中文翻译：", "中文翻译：", "**中文翻译：**", "以下是翻译：", "翻译结果：",
                "以下是翻译成中文的内容：", "译文：", "翻译：", "翻译如下：",
                "翻译成中文如下：", "以下是中文译文：",
                "我们收到一个翻译任务：", "我们被要求", "我们需要", "请将以下", "这是一段日文",
                "以下是将日文", "将以下日文", "以下为翻译", "翻译内容为",
            ]
            for prefix in prefixes:
                if prefix in result[:50]:  # check first 50 chars
                    # 找到真正的译文（通常是前缀后的冒号或换行后的内容）
                    # 尝试取前缀后的内容
                    idx = result.find(prefix)
                    if idx >= 0:
                        after = result[idx + len(prefix):].lstrip("：:。，\n ")
                        # 如果 after 仍然很长且不像翻译，保留 result
                        if len(after) > 3 and len(after) < len(title_ja) * 3:
                            result = after
                        break
            # 洗掉 markdown 加粗包裹
            if result.startswith("**") and "**" in result[2:]:
                result = result[2:result.index("**", 2)].strip()
            return result

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


def generate_content_and_comment(title_ja: str, title_zh: str, ja_summary: str = "",
                                  keyword: str = "", body_text: str = "") -> Tuple[str, str, str, str, str, list]:
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
- 标题统一用简体中文，日式汉字必须转为简体中文对应字（如「水島」→ 水岛、「澤」→ 泽、「櫻」→ 樱、「結」→ 结、日语专有名词中的汉字也要转简。日本团体名如「乃木坂」已是中文通用写法，保持不变）
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
日本资讯类：#日本新闻 #日本资讯 #看新闻学日语
综合类：#日本文化 #日本生活
时尚穿搭类（内容涉及时尚/穿搭时用）：#日系穿搭 #日本穿搭 #穿搭分享 #今日穿搭 #日系风格
美妆护肤类（内容涉及化妆/护肤时用）：#日系妆容 #日本化妆 #日本美妆 #日本护肤 #护肤分享
再根据内容补充行业相关标签）"""

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

    raw_seo = last_section("SEO标题")
    if raw_seo:
        seo_title = _truncate_title(raw_seo.split('\n')[0].strip())

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

def evaluate_quality(title_zh: str, content: str, comment: str,
                     title_ja: str = "", body_text: str = "") -> dict:
    """用 LLM 评估标题和内容质量，返回 {title_score, content_score, issues}"""
    prompt = f"""请根据以下维度，给这条小红书笔记的标题和内容打分。

【标题评分维度】（0-5分）
加分项（各+1）：剧情感 | 冲突感 | 猎奇感 | 用户共鸣 | 名人 | 热点
减分项（各-1）：简单通知 | 震惊体词汇 | 概括全部内容

【内容评分维度】（0-5分）
加分项（各+1）：原创度高 | 趣味性 | 有用信息非简单复述 | 补充对立信息 | 配视频
减分项（各-1）：离题 | 啰嗦重复 | 主动讨赏

---
标题：{title_zh}
正文摘要：{content[:200]}
解读：{comment[:150]}
---

只输出一行，格式：
TITLE_SCORE=数字 CONTENT_SCORE=数字 扣分: xxx（没有则写无）
"""

    result = call_litellm(prompt, max_tokens=200)
    if not result:
        return {"title_score": 0, "content_score": 0, "issues": []}

    import re
    ts = re.search(r"TITLE_SCORE=([\d.]+)", result)
    cs = re.search(r"CONTENT_SCORE=([\d.]+)", result)
    issues_m = re.search(r"扣分:\s*(.+)", result)

    return {
        "title_score": float(ts.group(1)) if ts else 0,
        "content_score": float(cs.group(1)) if cs else 0,
        "issues": [i.strip() for i in (issues_m.group(1).split(",") if issues_m else []) if i.strip() != "无"],
    }


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

        # 正文：提取 article 内所有段落，给 LLM 提供更多上下文
        body_text = ""
        article = soup.find("article") or soup.find(class_="article")
        if article:
            paras = article.find_all("p")
            body_parts = []
            for p in paras:
                t = p.get_text(strip=True)
                if len(t) > 20:
                    body_parts.append(t)
            body_text = "\n".join(body_parts)
        result["body_text"] = body_text[:2000]
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
        # 先抓文章详情，用 og:title 替换 Yahoo 搜索页的拼接/截断标题
        print("    抓取文章详情（供AI参考）...")
        details = fetch_article_details(news['link'])
        news['original_title']     = details.get("original_title", news['title_ja'])
        news['ja_summary']         = details.get("summary", "")
        news['original_image_url'] = details.get("image_url", "")
        news['body_text']          = details.get("body_text", "")

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

        # 用清洗后的标题翻译
        print("    翻译...")
        news['title_zh'] = translate_title(news['title_ja'])

        print("    生成内容...")
        generated = generate_content_and_comment(
            news['title_ja'], news['title_zh'],
            ja_summary=news.get('ja_summary', ''),
            keyword=keyword,
            body_text=news.get('body_text', ''),
        )
        if generated is None:
            print("    ⚠️ LLM 调用失败，跳过此条新闻")
            news['_skip'] = True
            return news
        seo_title, summary, content, comment, _, topic_tags = generated
        news['title_zh'] = seo_title
        news['summary']  = summary
        news['content']  = content
        news['comment']  = comment
        # 质量评分
        quality = evaluate_quality(seo_title, content, comment, news.get('title_ja',''), news.get('body_text',''))
        news['_title_score'] = quality['title_score']
        news['_content_score'] = quality['content_score']
        if quality['issues']:
            print(f"    📊 评分: 标题{quality['title_score']} 内容{quality['content_score']} | 扣分: {', '.join(quality['issues'])}")
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
    # pub_time：强制统一为 YYYY.MM.DD，忽略来源页的日文时间格式
    news['pub_time'] = datetime.now().strftime('%Y.%m.%d')
    if keyword:
        news['keyword'] = keyword

    # 3. Cloudinary 上传（文章详情已在 AI 生成前抓取）
    news.setdefault('original_title', news['title_ja'])
    news.setdefault('ja_summary', '')
    news.setdefault('original_image_url', '')

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


def push_stub_to_notion(news: dict, existing_keys: set | None = None) -> bool:
    """推送最小化存根到 Notion（仅用于去重记录：翻译标题 + 原地址 + key）"""
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
