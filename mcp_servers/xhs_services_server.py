"""xhs-services MCP server —— 把 services 暴露为 MCP 工具（三张皮之一）。

只做薄封装：解析参数 → 调 services → 返回 dict。禁止在此实现业务逻辑。
"""
import datetime as dt
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from fastmcp import FastMCP  # noqa: E402
from services import word_count as _wc, schedule as _sch, precheck as _pc  # noqa: E402

mcp = FastMCP("xhs-services")


@mcp.tool()
def xhs_word_count(text: str, mode: str = "content") -> dict:
    """小红书字数：mode=content 正文字数 / mode=title 标题长度。"""
    if mode == "title":
        return {"mode": "title", "title_len": _wc.title_len(text)}
    return {"mode": "content", "content_len": _wc.content_len(text)}


@mcp.tool()
def xhs_schedule_plan(date: str = "", seed: int = 0, jitter: int = 8,
                      slots: str = "") -> dict:
    """生成预发布时间计划（JST，明天为默认；分钟绝不为 :00）。不写 DB。"""
    day = dt.date.fromisoformat(date) if date else (
        dt.datetime.now(_sch.JST) + dt.timedelta(days=1)).date()
    slot_list = _sch.parse_slots(slots or ",".join(_sch.DEFAULT_SLOTS))
    plan = _sch.build_plan(day, slot_list, jitter, random.Random(seed))
    return {"date": str(day), "plan": [{"slot": s, "time": t} for s, t in plan]}


@mcp.tool()
def xhs_precheck(text: str, fmt: str = "news", lf: int = 0, ja: int = 0) -> dict:
    """对草稿文本跑机械门禁（标题/字数/##/顿号/假名/新字体/密度）。"""
    r = _pc.check_text(text, {"fmt": fmt, "lf": lf, "ja": ja})
    r.pop("dunhao", None)
    return r


if __name__ == "__main__":
    mcp.run()
