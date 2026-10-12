"""把 review 的**结构化数据**渲染成 Markdown 报告（二~六节）。报告=数据的模板渲染，格式由代码保证。"""
__all__ = ["render"]


def _c(x):
    return str(x if x is not None else "").replace("|", "｜").replace("\n", " ").strip()


def _tbl(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    for r in rows:
        out.append("| " + " | ".join(_c(c) for c in r) + " |")
    return "\n".join(out)


_ORDER = [("S", "S级（强烈推荐）🔥"), ("A", "A级（可选）"),
          ("AKB大TOP", "✅ AKB大TOP（必须入库）"), ("B", "B级（轻量跑量）"), ("C", "C级（跳过）")]


def render(data, titles=None):
    data = data or {}
    titles = titles or {}

    def _t(k):
        return (titles.get(k) or "")[:26]

    parts = []

    # 二、日期陷阱
    dt = [x for x in (data.get("date_trap") or []) if isinstance(x, dict)]
    if dt:
        parts.append("## 二、前一天日期陷阱检查")
        parts.append(_tbl(["素材", "处理", "说明"],
                          [[x.get("key"), x.get("kind"), x.get("note")] for x in dt]))

    # 三、聚类
    cl = [x for x in (data.get("clusters") or []) if isinstance(x, dict)]
    if cl:
        parts.append("## 三、跨来源聚类分析")
        parts.append(_tbl(["主条", "组内素材", "说明", "分级"],
                          [[x.get("main"), "、".join(x.get("keys") or []), x.get("note"), x.get("grade")]
                           for x in cl]))

    # 四、分级结果
    grades = data.get("grades") or {}
    if grades:
        parts.append("## 四、分级结果")
        for lv, title in _ORDER:
            items = [(k, v) for k, v in grades.items()
                     if (v.get("grade") if isinstance(v, dict) else v) == lv]
            if not items:
                continue
            parts.append(f"### {title}（{len(items)} 条）")
            for k, v in items:
                reason = (v.get("reason") if isinstance(v, dict) else "") or ""
                parts.append(f"- `{k}` 《{_t(k)}》 — {_c(reason)}")

    # 五、价值建议 + 跨时间关联
    adv = data.get("advice") or {}
    ct = data.get("cross_time") or {}
    if adv or ct:
        parts.append("## 五、第2层价值建议 + 第3层跨时间关联")
        if adv:
            rows = []
            for k, v in adv.items():
                if not isinstance(v, dict):
                    continue
                rows.append([k, _t(k), v.get("title_dir"), v.get("how"),
                             v.get("richness"), v.get("priority"), v.get("emotion")])
            if rows:
                parts.append(_tbl(["key", "标题", "标题方向", "写法建议", "充实度", "优先级", "情绪"],
                                  rows))
        if ct:
            parts.append("**跨时间关联**：")
            for k, lst in ct.items():
                if isinstance(lst, list) and lst:
                    txt = "；".join(f"{x.get('key')}({x.get('hit')})" for x in lst if isinstance(x, dict))
                    parts.append(f"- `{k}`：{_c(txt)}")

    # 六、发布数据回顾
    l4 = data.get("layer4") or {}
    if isinstance(l4, dict) and any(l4.values()):
        parts.append("## 六、第4层发布数据回顾")
        if l4.get("conclusion"):
            parts.append(_c(l4.get("conclusion")))
        for lab, key in (("Top", "top"), ("最低", "low"), ("零曝光", "zero"), ("异常", "anomaly")):
            if l4.get(key):
                parts.append(f"- **{lab}**：{_c(l4.get(key))}")

    return "\n\n".join(parts) + "\n"
