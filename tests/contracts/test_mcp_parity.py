"""契约：MCP 皮 == service 芯（直接调 tool 函数）。"""
import xhs_services_server as srv
from services import word_count as wc
from services import schedule as sch


def test_mcp_word_count_parity():
    assert srv.xhs_word_count("hello\nworld")["content_len"] == wc.content_len("hello\nworld")


def test_mcp_word_count_title():
    assert srv.xhs_word_count("标题", mode="title")["title_len"] == wc.title_len("标题")


def test_mcp_schedule_deterministic():
    a = srv.xhs_schedule_plan("2026-10-09", seed=42)
    b = srv.xhs_schedule_plan("2026-10-09", seed=42)
    assert a["plan"] == b["plan"]
    assert all(p["time"][-2:] != "00" for p in a["plan"])


def test_mcp_precheck_parity():
    r = srv.xhs_precheck("## 标题\n## A\n啊", fmt="story", lf=1, ja=100)
    assert any("< 800" in p for p in r["problems"])
