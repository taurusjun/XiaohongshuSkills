"""services_views.py — 把 services 暴露为 REST（三张皮之一）。

只做薄封装：解析请求 → 调 services → 返回 JSON。禁止在此实现业务逻辑。
"""
import datetime as dt
import os
import random
import sys

from flask import Blueprint, request, jsonify

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import word_count as _wc, schedule as _sch, precheck as _pc  # noqa: E402

services_bp = Blueprint("services", __name__)


@services_bp.post("/api/services/word-count")
def api_word_count():
    body = request.get_json(silent=True) or {}
    text = body.get("text", "")
    if body.get("mode") == "title":
        return jsonify({"ok": True, "mode": "title", "title_len": _wc.title_len(text)})
    return jsonify({"ok": True, "mode": "content", "content_len": _wc.content_len(text)})


@services_bp.get("/api/services/schedule")
def api_schedule():
    date = request.args.get("date")
    if date:
        day = dt.date.fromisoformat(date)
    else:
        day = (dt.datetime.now(_sch.JST) + dt.timedelta(days=1)).date()
    slots = _sch.parse_slots(request.args.get("slots", ",".join(_sch.DEFAULT_SLOTS)))
    jitter = request.args.get("jitter", default=8, type=int)
    seed = request.args.get("seed", type=int)
    plan = _sch.build_plan(day, slots, jitter, random.Random(seed))
    return jsonify({"ok": True, "date": str(day),
                    "plan": [{"slot": s, "time": t} for s, t in plan]})


@services_bp.post("/api/services/precheck")
def api_precheck():
    body = request.get_json(silent=True) or {}
    r = _pc.check_text(body.get("text", ""), body.get("spec") or {})
    r.pop("dunhao", None)
    return jsonify({"ok": True, **r})
