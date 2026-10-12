"""review 报告渲染：结构化数据 → Markdown（不依赖 LLM）。"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from services import review_render as R
from services import validate_tables as VT

SAMPLE = {
    "date_trap": [{"key": "81c2d36a5a33", "kind": "前日重复", "note": "同事件|含管道符"}],
    "clusters": [{"keys": ["a", "b"], "main": "a", "note": "同事件", "grade": "S"}],
    "grades": {"a": {"grade": "S", "reason": "事件爆发"},
               "b": {"grade": "C", "reason": "重复"},
               "c": {"grade": "AKB大TOP", "reason": "晒照"}},
    "advice": {"a": {"title_dir": "弓道", "how": "深挖", "richness": "高", "priority": "P0", "emotion": "期待"}},
    "cross_time": {"a": [{"key": "z", "hit": "中日双查命中"}]},
    "layer4": {"conclusion": "本期无发布数据", "top": "X"},
}


def test_render_sections_and_valid_tables():
    md = R.render(SAMPLE, {"a": "标题A", "b": "标题B", "c": "标题C"})
    for sec in ("## 二、", "## 三、", "## 四、", "## 五、", "## 六、"):
        assert sec in md, sec
    assert "### S级" in md and "### C级" in md and "AKB大TOP" in md
    nt, probs = VT.validate_text(md)          # 表格铁律校验（列数/紧邻标题/无多余|）
    assert probs == [], probs
    assert nt >= 2


def test_render_empty():
    assert R.render({}, {}).strip() == ""


def test_render_partial():
    md = R.render({"layer4": {"conclusion": "x"}}, {})
    assert "## 四、" not in md and "## 六、" in md


def test_cell_sanitize():
    md = R.render({"date_trap": [{"key": "k", "kind": "a|b", "note": "x\ny"}]}, {})
    assert "a｜b" in md and "x y" in md          # | → ｜，换行 → 空格


def test_grade_grouping_order():
    md = R.render(SAMPLE, {})
    i_s, i_akb, i_c = md.index("### S级"), md.index("AKB大TOP"), md.index("### C级")
    assert i_s < i_akb < i_c                      # 分级按 S→A→AKB→B→C 顺序
