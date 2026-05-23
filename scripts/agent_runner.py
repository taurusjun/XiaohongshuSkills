#!/usr/bin/env python3
"""agent_runner.py — 智能体主循环（每日 07:00 触发）"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os
import json
import logging
from datetime import datetime, timedelta
from dataclasses import asdict

logging.basicConfig(level=logging.INFO, format="%(asctime)s [agent] %(message)s")
logger = logging.getLogger("agent_runner")


def _alert(phase: str, e: Exception, context: str = "") -> None:
    """统一异常通知：打印 ERROR 日志 + 飞书消息（fire-and-forget）。"""
    msg = f"❌ agent_runner Phase {phase} 异常\n原因：{e}"
    if context:
        msg += f"\n上下文：{context}"
    logger.error(f"  ❌ Phase {phase} 失败: {e}")
    try:
        from scripts.feishu_bot import send_text, FEISHU_OPERATOR_OPEN_ID
        if FEISHU_OPERATOR_OPEN_ID:
            send_text(FEISHU_OPERATOR_OPEN_ID, msg)
    except Exception as fe:
        logger.warning(f"  飞书告警发送失败: {fe}")


def run(dry_run: bool = False, live_preview: bool = False):
    """智能体主循环：感知→规划→执行→通知"""
    if dry_run:
        import tempfile
        os.environ["SQLITE_PATH"] = tempfile.mktemp(suffix=".db")
        # 强制重载 yahoo_conf 以获取新 DB_PATH
        import config.yahoo_conf, importlib
        importlib.reload(config.yahoo_conf)
        from scripts.sqlite_db import init_db
        # 更新已缓存的 DB_PATH
        import scripts.sqlite_db as sdb
        sdb.DB_PATH = config.yahoo_conf.DB_PATH
        init_db()
        _seed_test_config()

    from scripts.sqlite_db import (init_db, get_config, set_state, get_state,
                                    get_top_topics, get_recent_performance)
    init_db()
    _sync_strategy_config()  # 每次启动检查 agent_strategy.json 是否比 DB 新，若是则同步

    date_str = datetime.now().strftime("%Y%m%d")
    progress = get_state(f"runner_progress_{date_str}", default={"phase": 0})

    # Phase 1: 感知
    if progress.get("phase", 0) < 1:
        logger.info("=== Phase 1: 感知 ===")
        focus_topics = get_config("focus_topics", default=[])

        # 趋势扫描：任何话题返回0条均抛异常，由外层 except 记录为 ERROR 而非 warning
        try:
            from scripts.xhs_trend_scanner import scan_topic_trends, update_trend_signals
            trends = scan_topic_trends(focus_topics[:5] or ["写真集"])
            if not trends:
                raise RuntimeError("趋势扫描返回空列表（所有话题均无结果），请确认 Chrome 已登录小红书")
            update_trend_signals(trends)
            logger.info(f"  趋势扫描完成: {len(trends)} topics")
        except Exception as e:
            from scripts.cdp_publish import XHSRateLimitError
            ctx = ("⚠️ 触发安全验证，需要人工在 Chrome 中完成验证后，明天将自动恢复"
                   if isinstance(e, XHSRateLimitError)
                   else "请确认 Chrome 已打开并登录小红书")
            _alert("1-趋势扫描", e, ctx)

        # 账号快照：返回0行也应明确报错（说明 CDP 未能读取创作者数据）
        try:
            from scripts.sqlite_db import insert_account_snapshot
            from scripts.cdp_publish import XiaohongshuPublisher
            pub = XiaohongshuPublisher()
            pub.connect()
            stats = pub.get_content_data(page_num=1, page_size=50)
            rows = stats.get("rows", [])
            if not rows:
                raise RuntimeError("get_content_data 返回0行，创作者数据读取失败（请确认已登录创作者后台）")
            week_views = sum(r.get("观看", 0) or 0 for r in rows if isinstance(r.get("观看"), int))
            week_saves = sum(r.get("收藏", 0) or 0 for r in rows if isinstance(r.get("收藏"), int))
            week_likes = sum(r.get("点赞", 0) or 0 for r in rows if isinstance(r.get("点赞"), int))
            top_note = max(rows, key=lambda r: r.get("观看", 0) or 0, default={}).get("_id", "")

            # 从自己的主页抓粉丝数（用 span.count+span.shows 精确提取）
            followers = None
            my_user_id = get_config("my_user_id", default="")
            if my_user_id:
                try:
                    pub._navigate(f"https://www.xiaohongshu.com/user/profile/{my_user_id}")
                    import time as _t; _t.sleep(2.5)
                    raw = pub._evaluate("""
                        (() => {
                            const result = {};
                            document.querySelectorAll('.user-interactions span.count, .count').forEach(el => {
                                const label = el.nextElementSibling?.innerText?.trim() || '';
                                const val = el.innerText?.trim();
                                if (label && val) result[label] = val;
                            });
                            return result;
                        })()
                    """)
                    if isinstance(raw, dict):
                        fans_str = raw.get("粉丝", "")
                        if fans_str:
                            fans_str = str(fans_str).replace(",", "")
                            followers = int(float(fans_str.replace("万", "")) * 10000) if "万" in fans_str else int(fans_str)
                    if followers is None:
                        raise ValueError(f"DOM 中未找到粉丝数（raw={raw}），页面结构可能已变化")
                    logger.info(f"  粉丝数: {followers}  关注: {raw.get('关注')}  获赞收藏: {raw.get('获赞与收藏')}")
                except Exception as pe:
                    _alert("1-粉丝数获取", pe, f"user_id={my_user_id}，快照仍正常写入，followers=NULL")

            insert_account_snapshot(
                snapshot_date=date_str,
                week_views=week_views,
                week_saves=week_saves,
                week_likes=week_likes,
                top_note_key=top_note,
                followers=followers,
            )
            logger.info(f"  账号快照: views={week_views} saves={week_saves} followers={followers} (from {len(rows)} 篇)")
        except Exception as e:
            _alert("1-账号快照", e, "请确认已登录小红书创作者后台")

        set_state(f"runner_progress_{date_str}", {"phase": 1}, date=date_str)

    # Phase 2: 规划
    if progress.get("phase", 0) < 2:
        logger.info("=== Phase 2: 规划 ===")
        from scripts.agent_planner import plan_today
        plan = plan_today(date_str)
        logger.info(f"  计划: quota={plan.quota_total}, mode={plan.mode}, topics={len(plan.topics)}")
        set_state(f"runner_progress_{date_str}", {"phase": 2, "plan": asdict(plan)}, date=date_str)

        # 告知运营者今日计划（非阻塞，失败不影响后续流程）
        try:
            from scripts.feishu_bot import send_text, FEISHU_OPERATOR_OPEN_ID
            if FEISHU_OPERATOR_OPEN_ID:
                topic_lines = "\n".join(
                    f"  • {t['topic']} × {t['quota']} 篇  [{t['source']}{'🔥' if t.get('is_fresh') else ''}]"
                    for t in plan.topics
                )
                send_text(FEISHU_OPERATOR_OPEN_ID,
                    f"📋 {date_str} 今日运营计划\n"
                    f"模式：{plan.mode}  配额：{plan.quota_total} 篇\n"
                    f"{topic_lines}\n"
                    f"发布时间：{' / '.join(plan.post_times)}"
                )
        except Exception as e:
            logger.warning(f"  飞书计划通知失败（不影响执行）: {e}")

        # Shadow: LLM 版规划（不影响执行，仅用于对比验证）
        try:
            from scripts.agent_planner_llm import shadow_plan
            shadow_plan(date_str, plan)
        except Exception as e:
            logger.warning(f"  Shadow 规划失败（不影响执行）: {e}")

    plan_data = get_state(f"runner_progress_{date_str}", default={})
    plan = plan_data.get("plan", {})
    topics = [t["topic"] for t in plan.get("topics", [])]
    # topic → target_format 映射（来自 content_format_rotation）
    topic_format_map = {t["topic"]: t.get("target_format", "news") for t in plan.get("topics", [])}

    if dry_run or live_preview:
        print(json.dumps(plan_data, ensure_ascii=False, indent=2))
        return

    # Phase 3: 执行 — 调 Web UI /api/trigger-fetch（走统一任务系统，页面可见锁/日志）
    if progress.get("phase", 0) < 3 and topics:
        logger.info("=== Phase 3: 执行 ===")
        try:
            import requests as _req, json as _json, time as _time
            yahoo_kw_map = get_config("yahoo_keyword_map", default={})
            daily_quota = get_config("daily_quota", default=2)
            keywords = []
            for topic in topics:
                kw_cfg = yahoo_kw_map.get(topic, {"keyword": topic, "max": daily_quota})
                keywords.append({"keyword": kw_cfg.get("keyword", topic), "max": kw_cfg.get("max", daily_quota)})
            resp = _req.post("http://127.0.0.1:5000/api/trigger-fetch",
                           json={"mode": "keywords", "keywords": keywords}, timeout=10)
            data = resp.json()
            if data.get("locked"):
                raise RuntimeError(f"抓取被锁定: {data.get('msg')}")
            tid = data.get("task_id", "?")
            logger.info(f"  已触发抓取任务 task_id={tid}")
            # 等待任务完成（最长 10 分钟）
            for _ in range(120):
                _time.sleep(5)
                status = _req.get(f"http://127.0.0.1:5000/api/task/{tid}", timeout=5).json()
                st = status.get("status", "?")
                if st == "done":
                    logger.info(f"  抓取任务完成")
                    break
                elif st != "running":
                    raise RuntimeError(f"抓取任务异常终止: {st}")
        except Exception as e:
            _alert("3-执行", e)
        set_state(f"runner_progress_{date_str}", {"phase": 3}, date=date_str)

    # Phase 4: 通知（飞书纯通知 + Web UI 链接，不依赖回调）
    if progress.get("phase", 0) < 4:
        logger.info("=== Phase 4: 通知 ===")
        try:
            from scripts.feishu_bot import send_daily_summary
            from scripts.sqlite_db import _connect
            with _connect() as db:
                candidates = db.execute(
                    "SELECT key, title, title_score, content_score FROM news "
                    "WHERE publish_xhs=0 AND status='active' AND title_score > 0 "
                    "AND DATE(created_at)=DATE('now','localtime') "
                    "ORDER BY title_score + content_score DESC LIMIT 10"
                ).fetchall()
            send_daily_summary(
                candidates=[dict(c) for c in candidates],
                date_str=date_str,
            )
        except Exception as e:
            logger.warning(f"  飞书通知失败: {e}")
        set_state(f"runner_progress_{date_str}", {"phase": 4}, date=date_str)

    # Phase 5: topic_performance 更新
    try:
        _update_topic_performance_for_mature_articles()
    except Exception as e:
        _alert("5-topic_performance更新", e)

    logger.info("Agent run complete.")


def _sync_strategy_config():
    """将 config/agent_strategy.json 中的配置同步到 DB。
    每次 agent_runner 启动时执行：运营者修改 JSON 文件后无需手动同步。
    """
    from scripts.sqlite_db import set_config
    cfg_path = Path(__file__).resolve().parent.parent / "config" / "agent_strategy.json"
    if not cfg_path.exists():
        return
    try:
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        skip_keys = {"_comment_yahoo_keyword_map"}  # 注释字段不写入
        synced = []
        for k, v in cfg.items():
            if not k.startswith("_") and k not in skip_keys:
                set_config(k, v)
                synced.append(k)
        logger.info(f"  agent_strategy.json → DB 同步完成: {len(synced)} 项")
    except Exception as e:
        logger.warning(f"  agent_strategy.json 同步失败（不影响运行）: {e}")


def _update_topic_performance_for_mature_articles():
    """检查发布满 7 天的文章，调用 upsert_topic_performance 更新话题表现"""
    from scripts.sqlite_db import _connect, upsert_topic_performance, update_news

    with _connect() as db:
        rows = db.execute(
            "SELECT key, tags, xhs_saves, xhs_comments, xhs_views FROM news "
            "WHERE substr(replace(pub_time,'.','-'),1,10) <= date('now','localtime','-7 days') "
            "AND topic_perf_updated_at IS NULL "
            "AND xhs_collected_at LIKE '%72h%' "
            "AND status='active'"
        ).fetchall()

    for r in rows:
        tags = (r["tags"] or "").split(",") if r["tags"] else []
        for tag in tags:
            tag = tag.strip()
            if tag:
                upsert_topic_performance(tag, saves=r["xhs_saves"] or 0,
                                         comments=r["xhs_comments"] or 0,
                                         views=r["xhs_views"] or 0)
        update_news(r["key"], {"topic_perf_updated_at": datetime.now().strftime("%Y-%m-%d %H:%M")})

def _seed_test_config():
    from scripts.sqlite_db import set_config
    from pathlib import Path
    try:
        cfg = json.loads((Path("config/agent_strategy.json")).read_text())
        for k, v in cfg.items():
            set_config(k, v)
    except Exception:
        pass


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="XHS 智能体主循环")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--live-preview", action="store_true")
    args = p.parse_args()
    run(dry_run=args.dry_run, live_preview=args.live_preview)
