#!/usr/bin/env python3
"""一次性回填 feedback_patterns 的结构化 facets（对缺 category 的历史行）。"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent import llm  # noqa: E402
from services import feedback_patterns as fp  # noqa: E402

SYS = ("你是发布数据规律标注器。给每条规律(title/body)抽取结构化字段，只输出严格 JSON："
       '{"<no>": {"category": "娱乐|经济|体育|社会|其他", '
       '"genre": "题材(如 争议发言/行业分析/亲情/偶像退圈/写真/宣传)", '
       '"title_style": "标题类型(如 分析性长标题/数字对比/不点名争议/半开玩笑请求/悬念)", '
       '"publish_mode": "normal|rewritten|caption|free|any", '
       '"direction": "爆发|稳态|零曝光|延迟归位|降级|中性", '
       '"action": "优先|慎用|改道gzh|降级|跳过|中性", '
       '"confidence": "待验证|待第2例|已确认", "entities": "相关人物/IP，逗号分隔"}}。'
       "不要任何多余文字。")


def chunks(a, n):
    for i in range(0, len(a), n):
        yield a[i:i + n]


def main():
    rows = [r for r in fp.all_patterns() if not (r.get("category") or "").strip()]
    print("待回填:", len(rows))
    done = 0
    for b in chunks(rows, 5):
        items = [{"no": r["no"], "title": r["title"], "body": (r.get("body") or "")[:1500]} for r in b]
        try:
            raw = llm.chat([{"role": "system", "content": SYS},
                            {"role": "user", "content": json.dumps(items, ensure_ascii=False)}],
                           max_tokens=4000)
            d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception as e:  # noqa: BLE001
            print("  批失败:", e)
            continue
        for no, f in d.items():
            try:
                fp.update_facets(int(no), f)
                done += 1
            except Exception:  # noqa: BLE001
                pass
        print(f"  已回填 {done}/{len(rows)}")
    print("完成:", done)
    return 0


if __name__ == "__main__":
    sys.exit(main())
