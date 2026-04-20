#!/usr/bin/env python3
"""
日本 Yahoo 新闻抓取器 - 抓取与中国相关的新闻并生成小红书发布格式（含评论解读）
"""

import json
from datetime import datetime

# 敏感词过滤
SENSITIVE_KEYWORDS = [
    "天安门", "六四", "法轮功", "藏独", "疆独",
    "杀人预告", "爆破预告", "皆殺し",
]


def is_sensitive(text: str) -> bool:
    """检查是否包含敏感内容"""
    return any(kw in text for kw in SENSITIVE_KEYWORDS)


# 今日新闻数据（含详细内容和评论）
TODAY_NEWS = [
    {
        "title_ja": "「40秒弱」で自動車1台を生産！中国政府が「テスラ巨大工場」を公開",
        "title_zh": "40秒就能生产一台汽车！中国政府公开特斯拉巨型工厂",
        "pub_time": "4/16(木) 17:06",
        "source": "TBS NEWS DIG",
        "link": "https://news.yahoo.co.jp/articles/6b99ad47493ef394fdbe1a675c3e51cb8da8be7e",
        "content_zh": """• 特斯拉上海工厂约40秒即可组装一辆汽车
• 去年特斯拉全球交付的EV中，半数来自此工厂
• 工厂约500台机器人运作，大部分自动化
• 李强总理亲自与马斯克会谈，中国将特斯拉作为"对外开放的象征"
• 特斯拉在上海设立了美国以外首个研发中心
• 中国一季度GDP增长5%，出口中外资企业约占3成
• 但外资撤退也在增加：伊势丹从6家缩减至1家，欧洲车企撤退
• 去年外资直接投资额降至2021年峰值的四分之一以下""",
        "comment": """这个效率确实惊人！40秒一台车，相当于每分钟就能下线1.5台。这就是为什么特斯拉能把价格打下来的原因——极致的自动化+中国供应链优势。

但新闻里也提到了一个矛盾：一边是特斯拉这样的外企在华扩产，一边是大量外资撤离。说明中国市场的"分化"越来越明显——头部企业享受优待，普通外企却面临激烈竞争和消费低迷。

对中国来说，特斯拉是"对外开放的招牌"，所以给足了政策支持。但这种模式能持续多久，可能取决于整体经济环境能否改善。"""
    },
    {
        "title_ja": "ホンダ、中国製造EV「逆輸入」　新型インサイト、17日から発売",
        "title_zh": "本田逆进口中国制造EV，新款Insight 17日起发售",
        "pub_time": "4/16(木) 16:37",
        "source": "共同通信",
        "link": "https://news.yahoo.co.jp/articles/4a832fbddfde10015d43b4cfd52be3615ca0846a",
        "content_zh": """• 本田17日发售新型EV「Insight」，在中国制造后进口日本
• 限量3000台，售价550万日元（约26万人民币）
• 以中国合资公司生产的EV SUV为基础，调整为日本规格
• 这是本田第二款在日本销售的中国制造车辆（首款是Odyssey）
• Insight于1999年作为本田首款混动车型登场，本次为第4代
• 背景：本田日本国内EV产品线不足，需要扩充""",
        "comment": """日本车企"逆进口"中国制造的车型，这在几年前还很难想象。

本田这个动作很能说明问题：日本本土的EV研发和生产节奏已经跟不上市场了，不得不借助中国供应链。550万日元的价格在日本市场算是中等价位，但如果是"中国制造+日本品牌"，性价比优势应该很明显。

不过限量3000台说明本田也在试探市场反应。日本消费者对"中国制造"的接受度如何，还需要观察。这可能是未来更多日本车企的选择——研发在日本，生产在中国，销售全球。"""
    },
    {
        "title_ja": "日産、軽ＥＶ「サクラ」を一部改良…中国ＢＹＤやスズキに対抗",
        "title_zh": "日产轻型EV「樱花」部分改良，对抗中国比亚迪和铃木",
        "pub_time": "4/16(木) 17:09",
        "source": "读卖新闻",
        "link": "https://news.yahoo.co.jp/articles/aecfca4e39d929ca31ac897868966961b8b4d0d8",
        "content_zh": """• 日产轻型EV「Sakura」部分改良，今夏发售
• 降价应对竞争：含税价244.86万日元起，比现款降低15万日元
• 补贴后约187万日元（约9万人民币）起可购买
• 续航180公里不变，等级从2个增至3个
• 上位等级前脸改为类似「Leaf」的设计
• 销量：22-23年度超3万台，25年度约1万台
• 目标：对抗今年进入轻型EV市场的铃木和中国比亚迪""",
        "comment": """这个价格战已经开始了！日本轻型EV市场本来是日产一家独大，现在铃木入场、比亚迪也要来，不得不降价应战。

补贴后187万日元（约9万人民币）买一台日本本土品牌的小车，性价比其实不错。但问题是——比亚迪同价位的车可能配置更高、续航更长。日产这次改款降价，更像是被动防守。

日本轻型车市场是本土品牌的最后堡垒，如果比亚迪在这个细分市场也站稳脚跟，对日本车企的冲击会很大。未来一年这个市场的竞争会很激烈。"""
    },
    {
        "title_ja": "中国、観光経済で世界首位へ",
        "title_zh": "中国在旅游经济领域将跃居世界首位",
        "pub_time": "4/16(木) 17:32",
        "source": "TBS CROSS DIG / Bloomberg",
        "link": "https://news.yahoo.co.jp/articles/4b164c596725415e92bf1023f3c7a991d067334e",
        "content_zh": """• 世界旅行旅游理事会(WTTC)数据显示：
• 中国旅游经济去年增长9.9%，是美国0.9%的10倍以上
• 2025年访华外国游客支出增长超10%
• 同期访美游客支出减少约5%
• 若保持同样增速，中国3-4年内可能超越美国
• 成为世界最大旅游经济体
• 美国去年访美外国人约6800万，同比减少5.5%
• 原因：入国管制强化、地缘政治紧张""",
        "comment": """这个趋势值得注意！疫情后中国的入境游恢复速度超出预期，而美国因为签证政策收紧、地缘政治等因素，吸引力在下降。

中国最近对多个国家实施免签政策，效果开始显现。加上144小时过境免签的推广，确实拉动了不少入境客流。

不过也要看到，中国的旅游收入和国际游客数量距离美国还有差距。能否真的在3-4年内超越，取决于免签政策能否继续扩大、旅游服务质量能否提升。但方向是对的——开放带来机遇。"""
    },
]


def generate_xiaohongshu_content(news: dict) -> dict:
    """生成适合小红书发布的内容格式（含评论解读）"""

    title_zh = news["title_zh"]
    source = news["source"]
    pub_time = news["pub_time"]
    content_zh = news.get("content_zh", "")
    comment = news.get("comment", "")
    link = news["link"]

    # 生成小红书标题
    xhs_title = f"🇯🇵日本媒体报道：{title_zh[:30]}"

    # 生成正文（含评论）
    xhs_content = f"""📰 来源：{source}
📅 时间：{pub_time}

【新闻要点】
{content_zh}

【我的解读】
{comment}

【原文标题】
{news['title_ja']}

🔗 原文链接见评论

#日本新闻 #中日资讯 #日本视角"""

    return {
        "title": xhs_title,
        "content": xhs_content,
        "original_link": link,
        "source": source,
    }


def main():
    """主函数"""
    print("=" * 60)
    print("🇯🇵 日本 Yahoo 新闻 - 中国相关（含评论解读）")
    print("=" * 60)
    print(f"📅 时间：{datetime.now().strftime('%Y.%m.%d %H:%M')}")
    print()

    results = []

    for i, news in enumerate(TODAY_NEWS, 1):
        if is_sensitive(news.get("title_zh", "")):
            print(f"[{i}] ⚠️ 敏感内容，已跳过")
            continue

        xhs = generate_xiaohongshu_content(news)
        news["xhs"] = xhs
        results.append(news)

        print(f"{'━' * 60}")
        print(f"📌 【第 {i} 条】")
        print(f"\n📰 标题：{xhs['title']}\n")
        print(xhs['content'])
        print()

    # 保存到文件
    output_file = f"yahoo_china_news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"✅ 已保存到: {output_file}")

    return results


if __name__ == "__main__":
    main()
