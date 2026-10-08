"""编排器工具集 —— 把 services 暴露为 LLM 可调用的函数（function calling）。

三张皮的第三种用法：内置编排器通过本模块调用 services。
只暴露确定性能力；不暴露任意 shell。
"""
import datetime as dt
import json
import random

from services import (
    word_count as _wc, precheck as _pc, schedule as _sch,
    dunhao as _dh, validate_tables as _vt, news as _news,
)

__all__ = ["TOOL_SCHEMAS", "TOOL_FUNCS", "dispatch"]


def _fn(name, desc, props, required):
    return {"type": "function", "function": {
        "name": name, "description": desc,
        "parameters": {"type": "object", "properties": props, "required": required}}}


def _word_count(text: str, mode: str = "content") -> dict:
    if mode == "title":
        return {"mode": "title", "title_len": _wc.title_len(text)}
    return {"mode": "content", "content_len": _wc.content_len(text)}


def _precheck(text: str, fmt: str = "news", lf: int = 0, ja: int = 0) -> dict:
    r = _pc.check_text(text, {"fmt": fmt, "lf": lf, "ja": ja})
    r.pop("dunhao", None)
    return r


def _schedule_plan(date: str = "", seed: int = 0, jitter: int = 8) -> dict:
    day = dt.date.fromisoformat(date) if date else (dt.datetime.now(_sch.JST) + dt.timedelta(days=1)).date()
    plan = _sch.build_plan(day, _sch.parse_slots(",".join(_sch.DEFAULT_SLOTS)), jitter, random.Random(seed))
    return {"date": str(day), "plan": [{"slot": s, "time": t} for s, t in plan]}


def _dunhao_normalize(text: str) -> dict:
    new, hits = _dh.fix_text(text)
    return {"text": new, "hits": hits}


def _validate_tables(text: str) -> dict:
    tables, problems = _vt.validate_text(text)
    return {"tables": tables, "problems": problems}


def _news_list(limit: int = 20, search: str = "", publish_xhs: str = "", fmt: str = "",
               status: str = "active", date_from: str = "", date_to: str = "") -> dict:
    rows = _news.query_news(date_from=date_from, date_to=date_to, status=status,
                            search=search, publish_xhs=publish_xhs, fmt=fmt,
                            limit=min(limit, 50))
    slim = [{k: r.get(k) for k in ("key", "title", "format", "is_long_form", "title_score",
                                   "content_score", "publish_xhs", "preselected", "xhs_pub_time")}
            for r in rows]
    return {"rows": slim, "count": len(slim)}


def _news_get(key: str) -> dict:
    a = _news.get_by_key(key)
    if not a:
        return {"error": f"not found: {key}"}
    return {k: a.get(k) for k in ("key", "title", "content_ja", "format", "is_long_form",
                                  "rewritten_title", "rewritten_content", "publish_mode",
                                  "preselected", "publish_xhs", "related_keys")}


def _news_update(key: str, fields: dict) -> dict:
    _news.update_news(key, fields)
    return {"ok": True, "key": key}


TOOL_SCHEMAS = [
    _fn("word_count", "小红书字数：mode=content 正文 / mode=title 标题",
        {"text": {"type": "string"}, "mode": {"type": "string", "enum": ["content", "title"]}}, ["text"]),
    _fn("precheck", "草稿机械门禁（标题/字数/##/顿号/假名/新字体/密度）。spec 来自 DB 字段",
        {"text": {"type": "string"}, "fmt": {"type": "string"}, "lf": {"type": "integer"},
         "ja": {"type": "integer"}}, ["text"]),
    _fn("schedule_plan", "生成预发布时间计划（JST，分钟非整点）",
        {"date": {"type": "string"}, "seed": {"type": "integer"}, "jitter": {"type": "integer"}}, []),
    _fn("dunhao_normalize", "顿号行归零（行内第2个及以后「、」→间隔号）",
        {"text": {"type": "string"}}, ["text"]),
    _fn("validate_tables", "Markdown 表格格式校验",
        {"text": {"type": "string"}}, ["text"]),
    _fn("news_list", "查询素材列表（轻字段）",
        {"limit": {"type": "integer"}, "search": {"type": "string"}, "publish_xhs": {"type": "string"},
         "fmt": {"type": "string"}, "status": {"type": "string"}, "date_from": {"type": "string"},
         "date_to": {"type": "string"}}, []),
    _fn("news_get", "读取单条素材（含 content_ja/改写字段）",
        {"key": {"type": "string"}}, ["key"]),
    _fn("news_update", "更新素材字段（写库）",
        {"key": {"type": "string"}, "fields": {"type": "object"}}, ["key", "fields"]),
]

TOOL_FUNCS = {
    "word_count": _word_count, "precheck": _precheck, "schedule_plan": _schedule_plan,
    "dunhao_normalize": _dunhao_normalize, "validate_tables": _validate_tables,
    "news_list": _news_list, "news_get": _news_get, "news_update": _news_update,
}


def dispatch(name: str, args: dict):
    fn = TOOL_FUNCS.get(name)
    if not fn:
        return {"error": f"unknown tool: {name}"}
    try:
        return fn(**(args or {}))
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}
