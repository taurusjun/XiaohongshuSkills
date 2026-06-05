#!/usr/bin/env python3
"""
小红书文章审查 agent — 评估稿件质量：情绪价值、爆发点、标题吸引力
审查标准比脚本版更严格，参考 delegate_task 的评判逻辑
"""

import sys, os, json

REVIEW_PROMPT = """你是一个严格的小红书内容审查员。你的标准是：**不要轻易给高分，8分以上必须真正出色**。

请从以下 3 个维度对文章进行评审，每项给出 **评分（1-10分）** 和 **具体理由**。

---

### 维度1：情绪价值（1-10分）

情绪价值 = 读完能否让读者产生情绪波动。

**高分需要（8-10分）：**
- 读者会产生强烈共鸣，读完想转发
- 情绪强烈：感动/愤怒/心疼/兴奋，而不只是"嗯，挺好的"
- 有人物情感的细节描写，不只是事实罗列
- 触及普世情感（亲情/友情/奋斗/不公平）

**低分（1-4分）：**
- 读完毫无感觉，像新闻摘要
- 纯信息堆砌，没有人情味
- 故事平淡如水

**注意：7分代表"合格但不出彩"，合理利用这个区间。**

---

### 维度2：爆发点（1-10分）

爆发点 = 文章是否有一个或多个让人"哇"一下的具体记忆点。

**高分需要（8-10分）：**
- 有具体数字、数据（不是模糊的"很多年"，而是"12次"）
- 有戏剧性场景（教练抱着哭/讲台正前方/白墙前报名照）
- 反转/冲突集中，读者会产生"然后呢？"的追读欲望
- 长文的话，必须有至少一个"一击即炸"的绝对爆点

**低分（1-4分）：**
- 平铺直叙，没有高潮
- 信息太分散，读者看完说不出"最印象深刻的是什么"

**特别提醒：** 长文内容容易有"爆发点密集但分散"的问题——每段都有亮点，但没有一个让人真正记住的中心爆点。这种情况打 7-8 分，不要因为素材多就给高分。

---

### 维度3：标题吸引力（1-10分）

标题 = 读者是否愿意点进来。上限64字。

**高分需要（8-10分）：**
- 数字+矛盾+具体名词，1秒内让读者好奇
- 有冲突感（"失败12次"+"教练哭了" = 为什么？）
- 具体而非抽象（"考级失败12次" > "她的坚持"）

**低分（1-4分）：**
- 太抽象文艺，读者看不懂标题想说什么
- 像新闻标题（"XXX谈偶像转折点"）
- 没有矛盾/冲突/未解之谜

**注意：如果人物在国内认知度低，标题可能需要更强的 hook 来补偿。这种可以扣1分。**

---

### 输出格式

严格按照以下 JSON 格式输出：

```json
{
  "emotional_value": {
    "score": 0,
    "reason": "评分理由",
    "strength": "最强的一点",
    "weakness": "最弱的一点"
  },
  "explosion_point": {
    "score": 0,
    "reason": "评分理由",
    "strength": "最强的一点",
    "weakness": "最弱的一点"
  },
  "title_attractiveness": {
    "score": 0,
    "reason": "评分理由",
    "strength": "最强的一点",
    "weakness": "最弱的一点"
  },
  "overall_assessment": "整体评价，一句话说清最强项和最弱项",
  "suggested_publish": true,
  "suggested_reason": "建议发布/暂缓的理由",
  "improvement_suggestion": "如果要改，从哪个维度入手最有效？"
}
```"""

def review_article(title: str, content: str) -> dict:
    from yahoo_common import call_litellm

    prompt = f"请严格审查以下小红书文章：\n\n标题：{title}\n\n正文：\n{content[:4000]}"

    result = call_litellm(
        prompt,
        system_prompt=REVIEW_PROMPT,
        temperature=0.2,
        max_tokens=2000,
        response_format={"type": "json_object"},
    )

    if not result:
        return {"error": "LLM 返回为空"}
    try:
        return json.loads(result)
    except json.JSONDecodeError as e:
        return {"error": f"解析 JSON 失败: {e}"}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="小红书内容审查 agent")
    parser.add_argument("key", help="文章 key")
    parser.add_argument("--mode", choices=["rewritten", "original"], default="rewritten")
    args = parser.parse_args()

    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
    from scripts.sqlite_db import get_by_key

    row = get_by_key(args.key)
    if not row:
        print(f"❌ 未找到文章: {args.key}"); return

    title = row.get('rewritten_title') or row.get('title', '')
    content = row.get('rewritten_content' if args.mode == 'rewritten' else 'content', '') or ''
    if not content:
        print(f"❌ 内容为空 (mode={args.mode})"); return

    print(f"📄 审查中: {title[:40]}... ({len(content)}字)")

    result = review_article(title, content)
    if result.get("error"):
        print(f"❌ {result['error']}"); return

    ev, ep, ta = result.get("emotional_value",{}), result.get("explosion_point",{}), result.get("title_attractiveness",{})

    print("\n" + "=" * 48)
    print("📊  审查报告")
    print("=" * 48)
    print(f"\n🫰  情绪价值：  {ev.get('score','?')}/10")
    print(f"    理由：{ev.get('reason','')}")
    if ev.get('weakness'): print(f"    弱项：{ev['weakness']}")
    print(f"\n💥  爆发点：    {ep.get('score','?')}/10")
    print(f"    理由：{ep.get('reason','')}")
    if ep.get('weakness'): print(f"    弱项：{ep['weakness']}")
    print(f"\n🎯  标题吸引力：{ta.get('score','?')}/10")
    print(f"    理由：{ta.get('reason','')}")
    if ta.get('weakness'): print(f"    弱项：{ta['weakness']}")
    print(f"\n{'─' * 48}")
    print(f"📝 {result.get('overall_assessment','')}")
    if result.get('improvement_suggestion'):
        print(f"💡 改进方向：{result['improvement_suggestion']}")
    verdict = result.get('suggested_publish', False)
    if verdict:
        print(f"\n✅ 建议发布 — {result.get('suggested_reason','')}")
    else:
        print(f"\n❌ 建议暂缓 — {result.get('suggested_reason','')}")
    print()


if __name__ == "__main__":
    main()
