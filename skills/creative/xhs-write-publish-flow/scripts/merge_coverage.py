#!/usr/bin/env python3
"""合并稿覆盖体检 —— 判断一篇 related_keys 稿到底是「综合改写」还是「主素材译文」。

为什么需要（10/31 实测）
----------------------
① 第一版 `c0040119` 正文只有主素材译文，6 条 related_keys 一条事实都没用上；
② 补改后又只是「译文 + 末尾贴一段」，用户原话：「你只是翻译了一下，根本不是做
   多篇文章的综合改写」。

根因：`related_keys` 只挂了个 ID，没有任何东西检查「这些素材的内容进没进正文」。

判据（跨语言可用）
------------------
- **同源重复**：与主素材的数字集合重叠 Jnums > 0.5（且 ≥3 个数字）或 3-gram
  Jaccard > 0.55 → 自动剔除，不参与统计。5 家媒体同稿属于正常情况。
- **独有数字**：数字是跨语言唯一可靠的锚。日文 11名 ↔ 中文「十一个人」也算命中
  （脚本会把正文里的中文数字转成阿拉伯数字再比）。
- 商品型番（SRCL-13940 这类 5 位以上数字）不计入。

用法
----
    python3 merge_coverage.py <40位key>          # 覆盖报告
    python3 merge_coverage.py <40位key> --raw    # 连未采纳的数字一起列出

判读
----
非同源素材的独有数字采纳合计 = 0 → **这是主素材译文，不是合并稿**：补素材，或清空
related_keys 按单篇写。
"""
import json
import os
import re
import sys
import urllib.request

API = os.environ.get("XHS_API_BASE", "http://127.0.0.1:5000")
NUM_RE = re.compile(r"\d{1,4}(?:\.\d+)?")
CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
CN_RE = re.compile(r"[一二两三四五六七八九十]{1,3}")
SAME_SRC_JACCARD = 0.55
SAME_SRC_NUMS = 0.50


def get(key):
    with urllib.request.urlopen(f"{API}/api/news/{key}", timeout=60) as r:
        return json.loads(r.read().decode())


def grams(text, n=3):
    t = re.sub(r"\s+", "", text or "")
    return {t[i:i + n] for i in range(max(0, len(t) - n + 1))}


def cn_bigrams(text):
    """关联素材的中文要点 vs 正文的中文 2-gram —— 覆盖「借了事实但没有数字」的情况。"""
    t = re.sub(r"[^\u4e00-\u9fa5]", "", text or "")
    return {t[i:i + 2] for i in range(len(t) - 1)}


COV_MIN = 0.35   # 中文要点覆盖率 ≥0.35 视为实质采纳（10/31 校准：真整合 0.50 / 顺带提到 0.19~0.23）


def jaccard(a, b):
    return len(a & b) / len(a | b) if a and b else 0.0


def nums_of(text):
    """阿拉伯数字 + 中文数字（转成阿拉伯），剔除 5 位以上型番。"""
    out = set(NUM_RE.findall(text or ""))
    for chunk in CN_RE.findall(text or ""):
        if chunk == "十":
            out.add("10")
            continue
        if "十" in chunk:
            head, _, tail = chunk.partition("十")
            tens = CN_NUM.get(head, 1) if head else 1
            ones = CN_NUM.get(tail, 0) if tail else 0
            out.add(str(tens * 10 + ones))
        elif len(chunk) == 1 and chunk in CN_NUM:
            out.add(str(CN_NUM[chunk]))
    return out


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    key = sys.argv[1]
    raw = "--raw" in sys.argv

    m = get(key)
    body = m.get("rewritten_content") or ""
    if not body.strip():
        sys.exit("这篇还没有 rewritten_content，先写稿再体检")
    ja = m.get("content_ja") or ""
    m_g, m_nums = grams(ja), nums_of(ja)
    body_nums = nums_of(body)
    body_big = cn_bigrams(body)

    rel = [x for x in (m.get("related_keys") or "").split(",") if x]
    print(f"主素材 {key[:12]} | ja={len(ja)} | 正文={len(body)} | related={len(rel)} 条\n")

    used_total, indep, cov_hit = 0, 0, 0
    for rk in rel:
        try:
            row = get(rk)
        except Exception as e:  # noqa: BLE001
            print(f"{rk[:12]}  读取失败：{e}")
            continue
        rja = row.get("content_ja") or row.get("content") or ""
        g_j = jaccard(m_g, grams(rja))
        r_nums = nums_of(rja)
        n_j = jaccard(m_nums, r_nums)
        dup = (n_j > SAME_SRC_NUMS and len(r_nums) >= 3) or g_j > SAME_SRC_JACCARD
        uniq = r_nums - m_nums
        used = uniq & body_nums
        rc_big = cn_bigrams(row.get("content") or "")
        cov = (len(rc_big & body_big) / len(rc_big)) if rc_big else None
        cov_txt = f"cov={cov:.2f}" if cov is not None else "cov=n/a"
        print(f"{rk[:12]}  numJ={n_j:.2f} gramJ={g_j:.2f} {cov_txt}  {'同源重复' if dup else '独立素材'}"
              f"  独有数字{len(uniq):2} → 采纳{len(used):2}  | {row.get('title','')[:30]}")
        if not dup:
            indep += 1
            used_total += len(used)
            if cov is not None and cov >= COV_MIN:
                cov_hit += 1
            miss = sorted(uniq - used)
            if miss and (raw or (not used and (cov is None or cov < COV_MIN))):
                print(f"            未采纳: {' '.join(miss[:14])}")

    print(f"\n独立素材 {indep} 条 | 独有数字采纳 {used_total} 个 | 中文要点实质覆盖 {cov_hit} 条")
    if indep and used_total == 0 and cov_hit == 0:
        print("❌ 判定：这不是合并稿。related 素材一条事实都没进正文 —— 补素材，或清空 related_keys 按单篇写。")
        return 1
    if indep == 0:
        print("ℹ️  全部 related 都是同源重复稿：本篇按单篇口径即可，不算漏读。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
