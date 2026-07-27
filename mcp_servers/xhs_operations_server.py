#!/usr/bin/env python3
"""xhs_operations MCP server — 运营数据读写工具"""

import sys, os, json, uuid, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastmcp import FastMCP
from scripts.sqlite_db import (
    query_news, get_by_key, update_news,
    get_score_dims, load_active_dimensions,
    get_top_topics, get_config, set_config,
    rollback_dimension_version, commit_dimension_version,
    upsert_score_dims, load_dim_weights, recalculate_scores,
)

mcp = FastMCP("xhs-operations")


def _ok(data: dict = None) -> dict:
    d = {"ok": True}
    if data:
        d.update(data)
    return d


def _error(code: str, message: str) -> dict:
    return {"error": True, "code": code, "message": message}


@mcp.tool()
def get_candidate_articles(date: str = "", status: str = "active") -> dict:
    """获取候选文章列表"""
    try:
        articles = query_news(date_from=date or "", status=status, limit=50)
        result = []
        for a in articles:
            result.append({
                "news_key": a["key"],
                "title": a.get("title_zh") or a["title"],
                "title_score": a.get("title_score", 0),
                "content_score": a.get("content_score", 0),
                "category": a.get("category", ""),
                "tags": a.get("tags", ""),
                "publish_xhs": a.get("publish_xhs", 0),
                "status": a.get("status", "active"),
            })
        return _ok({"articles": result, "count": len(result)})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def get_article_detail(news_key: str) -> dict:
    """获取单篇文章完整详情（含评分维度）"""
    try:
        article = get_by_key(news_key)
        if not article:
            return _error("NOT_FOUND", f"Article {news_key} not found")
        dims = get_score_dims(news_key)
        return _ok({
            "article": dict(article),
            "dim_scores": [dict(d) for d in dims],
        })
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def update_article_status(news_key: str, status: str, note: str = "") -> dict:
    """更新文章状态"""
    valid = {"active", "discarded", "skipped", "archived"}
    if status not in valid:
        return _error("INVALID_VALUE", f"status must be one of {valid}")
    try:
        update_news(news_key, {"status": status})
        return _ok()
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def get_dimension_versions() -> dict:
    """获取评分维度版本列表"""
    try:
        from scripts.sqlite_db import _connect
        with _connect() as db:
            rows = db.execute(
                "SELECT id, version, created_at, created_by, change_note, is_active FROM scoring_dimension_versions ORDER BY id DESC"
            ).fetchall()
        return _ok({"versions": [dict(r) for r in rows]})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def activate_dimension_version(version: str) -> dict:
    """激活指定版本"""
    try:
        ok = rollback_dimension_version(version)
        if not ok:
            return _error("NOT_FOUND", f"Version {version} not found")
        return _ok()
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def get_topic_performance(limit: int = 20, window_days: int = 90) -> dict:
    """获取话题表现排名"""
    try:
        topics = get_top_topics(n=limit, window_days=window_days)
        return _ok({"topics": topics})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def get_weekly_stats(week_start: str = "") -> dict:
    """聚合本周发布统计"""
    try:
        from scripts.sqlite_db import _connect
        from datetime import datetime, timedelta
        if not week_start:
            today = datetime.now()
            week_start = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
        with _connect() as db:
            rows = db.execute(
                "SELECT key, title, xhs_views, xhs_likes, xhs_saves, xhs_comments, category, tags FROM news WHERE status='active' AND publish_xhs=1 AND publish_time >= ?",
                (week_start,)
            ).fetchall()
        articles = [dict(r) for r in rows]
        total = len(articles)
        total_saves = sum(a.get("xhs_saves", 0) or 0 for a in articles)
        total_comments = sum(a.get("xhs_comments", 0) or 0 for a in articles)
        total_views = sum(a.get("xhs_views", 0) or 0 for a in articles)
        return _ok({
            "week_start": week_start, "total_published": total,
            "total_saves": total_saves, "total_comments": total_comments,
            "total_views": total_views, "articles": articles,
        })
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def override_dim_score(news_key: str, dim_name: str, value: float, note: str) -> dict:
    """人工纠正评分维度"""
    if value not in (0, 0.5, 1):
        return _error("INVALID_VALUE", "value must be 0, 0.5, or 1")
    try:
        from scripts.sqlite_db import _connect
        with _connect() as db:
            existing = db.execute(
                "SELECT value FROM score_dims WHERE news_key=? AND dimension=?", (news_key, dim_name)
            ).fetchone()
            if not existing:
                return _error("NOT_FOUND", f"Dimension {dim_name} not found for {news_key}")
            db.execute(
                "UPDATE score_dims SET llm_value=value, human_value=?, human_override=1, override_note=?, value=? WHERE news_key=? AND dimension=?",
                (value, note, value, news_key, dim_name),
            )
        scores = recalculate_scores(news_key)
        return _ok(scores)
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def run_reflection(mode: str = "quick") -> dict:
    """启动反思分析（异步后台执行）"""
    task_id = str(uuid.uuid4())[:8]
    try:
        from scripts.sqlite_db import set_state
        set_state(f"task_{task_id}", {"status": "running", "mode": mode})

        def _run():
            try:
                from scripts.reflection_runner import run
                run(dry_run=(mode == "quick"), min_samples=30 if mode == "full" else 10)
                from scripts.sqlite_db import set_state
                set_state(f"task_{task_id}", {"status": "completed", "mode": mode})
            except Exception as e:
                from scripts.sqlite_db import set_state
                set_state(f"task_{task_id}", {"status": "failed", "error": str(e)})

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        return _ok({"started": True, "task_id": task_id})
    except Exception as e:
        return _error("TASK_ERROR", str(e))


@mcp.tool()
def get_task_status(task_id: str) -> dict:
    """查询异步任务状态"""
    try:
        from scripts.sqlite_db import get_state
        status = get_state(f"task_{task_id}")
        if status is None:
            return _error("NOT_FOUND", f"Task {task_id} not found")
        return _ok(status)
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def batch_update_articles(news_keys: list[str], status: str, note: str = "") -> dict:
    """批量更新文章状态"""
    valid = {"active", "discarded", "skipped", "archived"}
    if status not in valid:
        return _error("INVALID_VALUE", f"status must be one of {valid}")
    try:
        count = 0
        for key in news_keys:
            if update_news(key, {"status": status}):
                count += 1
        return _ok({"updated": count})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool()
def update_dim_weights(weights: dict) -> dict:
    """更新评分权重配置"""
    try:
        set_config("dim_weights", weights)
        return _ok({"weights": weights})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool(name="xhs_send_review")
def xhs_send_review(content: str, chat_id: str = "") -> dict:
    """将每日素材 review 报告的 Markdown 内容分段发送到 Telegram。

    参数：
      content  — 完整 Markdown 内容（非文件路径）
      chat_id  — 可选，指定 Telegram chat ID；为空时使用默认配置

    返回：{"ok": true, "segments": N} 或 {"error": true, "message": "..."}
    """
    import re, json, os, tempfile, subprocess, sys
    from pathlib import Path

    # 读取 bot_token
    TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if not TOKEN:
        try:
            cfg_text = (Path.home() / ".hermes/config.yaml").read_text()
            m = re.search(r"bot_token:\s*(\S+)", cfg_text)
            TOKEN = m.group(1) if m else ""
        except Exception:
            pass
    if not TOKEN:
        return _error("NO_TOKEN", "TELEGRAM_BOT_TOKEN not set")

    # 読取 chat_id
    effective_chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not effective_chat_id:
        try:
            raw = json.loads((Path.home() / ".hermes/cron/jobs.json").read_text())
            jobs = raw if isinstance(raw, list) else raw.get("jobs", [])
            for j in jobs:
                if isinstance(j, dict):
                    o = j.get("origin")
                    if isinstance(o, dict):
                        cid = o.get("chat_id", "")
                        if cid:
                            effective_chat_id = cid
                            break
        except Exception:
            pass
    if not effective_chat_id:
        return _error("NO_CHAT_ID", "chat_id not provided and TELEGRAM_CHAT_ID not set")

    # 写临时文件，复用 segment-send.py 的完整逻辑
    with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
        f.write(content)
        tmp_path = f.name

    try:
        script = str(Path.home() /
            ".hermes/skills/creative/xhs-daily-material-review/scripts/segment-send.py")
        env = os.environ.copy()
        env["TELEGRAM_BOT_TOKEN"] = TOKEN
        env["TELEGRAM_CHAT_ID"] = effective_chat_id

        result = subprocess.run(
            [sys.executable, script, tmp_path, effective_chat_id],
            capture_output=True, text=True, timeout=120, env=env
        )
        if result.returncode != 0:
            return _error("SEND_FAILED", result.stderr[:500])

        segments = result.stdout.count(" OK")
        return _ok({"segments": segments, "stdout": result.stdout[-200:]})
    except Exception as e:
        return _error("EXCEPTION", str(e))
    finally:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass

if __name__ == "__main__":
    mcp.run()



# ============ HTTP API 端点对齐工具（http://192.168.0.70:5000/） ============
# 这些工具与 webapp API 端点一一对应，方便外部 skill 调用

@mcp.tool(name="xhs_list_news")
def xhs_list_news(
    date_from: str = "",
    date_to: str = "",
    status: str = "active",
    sort_by: str = "created_at",
    sort_dir: str = "DESC",
    limit: int = 50,
    offset: int = 0,
    category: str = "",
    publish_xhs: str = "",
    fmt: str = "",
    score_min: str = "",
    fetch_by: str = "",
    preselected: str = "",
    search: str = "",
    fields: str = "",
    keys: str = "",
) -> dict:
    """对应 GET /api/news — 拉取素材列表。

    参数：
      date_from/date_to: YYYY-MM-DD，留空不限
      status: active|archived|published
      sort_by: created_at|title_score|content_score|pub_time|title
      sort_dir: DESC|ASC
      limit: 1-500
      offset: 分页偏移
      search: 关键词
      其他过滤：category/publish_xhs/fmt/score_min/fetch_by/preselected
    返回：{rows: [...], total, today, pending, published}
    """
    try:
        rows = query_news(
            date_from=date_from, date_to=date_to, category=category,
            status=status, search=search, publish_xhs=publish_xhs,
            fmt=fmt, score_min=score_min, fetch_by=fetch_by,
            preselected=preselected, sort_by=sort_by, sort_dir=sort_dir,
            limit=min(limit, 500), offset=offset,
        )
        total = len(query_news(
            date_from=date_from, date_to=date_to, category=category,
            status=status, search=search, publish_xhs=publish_xhs,
            fmt=fmt, score_min=score_min, fetch_by=fetch_by,
            preselected=preselected, sort_by=sort_by, sort_dir=sort_dir,
            limit=10000,
        ))
        if keys:
            key_set = set(k.strip() for k in keys.split(",") if k.strip())
            rows = [r for r in rows if r.get("key") in key_set]
            total = len(rows)
        if fields:
            keep = set(f.strip() for f in fields.split(",") if f.strip())
            rows = [{k: v for k, v in r.items() if k in keep} for r in rows]
        return _ok({"rows": rows, "total": total})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool(name="xhs_get_news")
def xhs_get_news(news_key: str) -> dict:
    """对应 GET /api/news/<key> — 读取单条素材详情（含 score_dims）。

    参数：
      news_key: SHA1 形式的素材 key
    返回：news 全字段 + score_dims 数组
    """
    try:
        article = get_by_key(news_key)
        if not article:
            return _error("NOT_FOUND", f"Article {news_key} not found")
        dims = get_score_dims(news_key)
        result = dict(article)
        result["score_dims"] = [dict(d) for d in dims]
        return _ok(result)
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool(name="xhs_update_news")
def xhs_update_news(news_key: str, fields: dict) -> dict:
    """对应 PUT /api/news/<key> — 更新素材字段（标题/分级/标签/状态等）。

    参数：
      news_key: 素材 key
      fields: 要更新的字段 dict
    常用字段：title, title_zh, content, summary, comment, tags, status,
              publish_xhs, category, score_dims, fetch_by
    返回：{ok: true}
    """
    try:
        update_news(news_key, fields)
        return _ok()
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool(name="xhs_score_dim")
def xhs_score_dim(news_key: str, dimension: str, human_value: float, override_note: str = "") -> dict:
    """对应 PUT /api/score-dim/<key>/<dim> — 人工覆盖评分维度值。

    参数：
      news_key: 素材 key
      dimension: 维度名（如 freshness/topic_fit/emotion/format_match）
      human_value: 0 | 0.5 | 1
      override_note: 说明（可选）
    返回：{ok: true, scores: [...]} 重算后的评分
    """
    if human_value not in (0, 0.5, 1):
        return _error("INVALID_VALUE", "human_value must be 0, 0.5, or 1")
    try:
        from scripts.sqlite_db import _connect
        with _connect() as db:
            existing = db.execute(
                "SELECT value FROM score_dims WHERE news_key=? AND dimension=?",
                (news_key, dimension),
            ).fetchone()
            if not existing:
                return _error("NOT_FOUND", f"Dimension {dimension} not found for {news_key}")
            db.execute(
                "UPDATE score_dims SET llm_value=value, human_value=?, human_override=1, "
                "override_note=?, value=? WHERE news_key=? AND dimension=?",
                (human_value, override_note, human_value, news_key, dimension),
            )
        scores = recalculate_scores(news_key)
        return _ok(scores)
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool(name="xhs_metrics_history")
def xhs_metrics_history(news_key: str) -> dict:
    """对应 GET /api/metrics-history/<key> — 读取发布后 72 个反馈快照。

    返回：{snapshots: [{collected_at, views, likes, saves, comments, impression, click_rate}], count}
    """
    try:
        from scripts.sqlite_db import _connect
        with _connect() as db:
            rows = db.execute(
                "SELECT collected_at, views, likes, saves, comments, impression, click_rate "
                "FROM metrics_history WHERE news_key=? ORDER BY collected_at ASC LIMIT 72",
                (news_key,),
            ).fetchall()
        return _ok({"snapshots": [dict(r) for r in rows], "count": len(rows)})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool(name="xhs_search_news")
def xhs_search_news(
    search: str,
    date_from: str = "",
    date_to: str = "",
    status: str = "active",
    sort_by: str = "created_at",
    sort_dir: str = "DESC",
    limit: int = 50,
    fields: str = "",
) -> dict:
    """对应 GET /api/news?search=... — 跨时间关联搜索素材。

    参数：
      search: 关键词
      date_from/date_to: YYYY-MM-DD 可选
      status: active|archived|published
      limit: 1-500
      fields: 逗号分隔字段列表，只返回指定字段（如 "key,title,title_score,pub_time"）。
              留空返回全字段（含 content/content_ja 全文，体积大，慎用）。
    返回：{rows: [...], total}
    """
    try:
        rows = query_news(
            date_from=date_from, date_to=date_to,
            status=status, search=search,
            sort_by=sort_by, sort_dir=sort_dir, limit=min(limit, 500),
        )
        total = len(query_news(
            date_from=date_from, date_to=date_to,
            status=status, search=search,
            sort_by=sort_by, sort_dir=sort_dir, limit=10000,
        ))
        if fields:
            keep = set(f.strip() for f in fields.split(",") if f.strip())
            rows = [{k: v for k, v in r.items() if k in keep} for r in rows]
        return _ok({"rows": rows, "total": total})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool(name="xhs_set_grade")
def xhs_set_grade(news_key: str, grade: str, reason: str = "") -> dict:
    """设置文章分级（A/B/C/D）— 专用写入工具，只能改 grade 字段。

    参数：
      news_key: 素材 key
      grade: 'A' | 'B' | 'C' | 'D' | '' (空字符串清除分级)
      reason: 分级理由（可选，记录到 grade_reason 字段）
    返回：{ok: true}
    """
    valid_grades = {"A", "B", "C", "D", ""}
    if grade not in valid_grades:
        return _error("INVALID_VALUE", f"grade must be one of {valid_grades}")
    try:
        update_news(news_key, {"grade": grade, "grade_reason": reason})
        return _ok({"grade": grade, "reason": reason})
    except Exception as e:
        return _error("DB_ERROR", str(e))


@mcp.tool(name="xhs_trigger_publish")
def xhs_trigger_publish() -> dict:
    """对应 POST /api/trigger-publish — 触发发布任务。

    实现走 HTTP（避免 webapp 内部状态机）。返回 {ok: true, response: {...}}
    若已有任务在跑：返回 {ok: true, response: {locked: true, msg: "..."}}
    """
    import os
    base = os.environ.get("XHS_WEBAPI_BASE", "http://192.168.0.70:5000")
    try:
        import requests
        r = requests.post(f"{base}/api/trigger-publish", timeout=10)
        return _ok({"response": r.json()})
    except Exception as e:
        return _error("HTTP_ERROR", str(e))


# 根据环境变量 XHS_DISABLE_WRITE_TOOLS=1 隐藏所有写工具
# fastmcp 推荐用 mcp.disable(names=...) 注册 Visibility transform，模块加载时同步调用
import os as _os
if _os.environ.get("XHS_DISABLE_WRITE_TOOLS") == "1":
    mcp.disable(names={
        "update_article_status",
        "activate_dimension_version",
        "override_dim_score",
        "batch_update_articles",
        "update_dim_weights",
        "xhs_update_news",
        "run_reflection",
    })



if __name__ == "__main__":
    mcp.run()
