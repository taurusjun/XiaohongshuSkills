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
                # Placeholder: run reflection logic here
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


if __name__ == "__main__":
    mcp.run()
