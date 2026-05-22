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

    date_str = datetime.now().strftime("%Y%m%d")
    progress = get_state(f"runner_progress_{date_str}", default={"phase": 0})

    # Phase 1: 感知
    if progress.get("phase", 0) < 1:
        logger.info("=== Phase 1: 感知 ===")
        focus_topics = get_config("focus_topics", default=[])
        try:
            from scripts.xhs_trend_scanner import scan_topic_trends, update_trend_signals
            trends = scan_topic_trends(focus_topics[:5] or ["写真集"])
            if trends:
                update_trend_signals(trends)
                logger.info(f"  趋势扫描完成: {len(trends)} topics")
        except Exception as e:
            logger.warning(f"  趋势扫描失败: {e}")

        # 账号快照（用 get_content_data 获取近期数据）
        try:
            from scripts.sqlite_db import insert_account_snapshot
            from scripts.cdp_publish import XiaohongshuPublisher
            pub = XiaohongshuPublisher()
            pub.connect()
            stats = pub.get_content_data(page_num=1, page_size=50)
            rows = stats.get("rows", [])
            week_views = sum(r.get("观看", 0) or 0 for r in rows if isinstance(r.get("观看"), int))
            week_saves = sum(r.get("收藏", 0) or 0 for r in rows if isinstance(r.get("收藏"), int))
            week_likes = sum(r.get("点赞", 0) or 0 for r in rows if isinstance(r.get("点赞"), int))
            top_note = max(rows, key=lambda r: r.get("观看", 0) or 0, default={}).get("_id", "")
            insert_account_snapshot(
                snapshot_date=date_str,
                week_views=week_views,
                week_saves=week_saves,
                week_likes=week_likes,
                top_note_key=top_note,
            )
            logger.info(f"  账号快照: views={week_views} saves={week_saves}")
        except Exception as e:
            logger.warning(f"  账号快照失败: {e}")

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

    plan_data = get_state(f"runner_progress_{date_str}", default={})
    plan = plan_data.get("plan", {})
    topics = [t["topic"] for t in plan.get("topics", [])]

    if dry_run or live_preview:
        print(json.dumps(plan_data, ensure_ascii=False, indent=2))
        return

    # Phase 3: 执行（并行处理，对齐 yahoo_news_auto_sqlite.py 的 ThreadPoolExecutor）
    if progress.get("phase", 0) < 3 and topics:
        logger.info("=== Phase 3: 执行 ===")
        total_fetched = 0
        try:
            import threading
            from concurrent.futures import ThreadPoolExecutor, as_completed
            from scripts.yahoo_news_auto import fetch_news_via_cdp, KEYWORD_TAG_MAP
            from scripts.yahoo_common import process_news_item
            from scripts.sqlite_db import (insert_news, upsert_score_dims,
                                            update_news, load_today_keys,
                                            increment_topic_discard)

            from scripts.yahoo_common import extract_key_from_url
            existing_keys = load_today_keys()
            max_results = get_config("daily_quota", default=2)
            max_workers = get_config("fetch_parallel", default=3)

            # Step 1: 串行抓取（CDP 不支持并发），跨话题合并去重
            # yahoo_keyword_map: focus_topics（中文）→ Yahoo Japan 搜索词（日语）
            yahoo_kw_map = get_config("yahoo_keyword_map", default={})
            tasks = []
            seen_keys = set()  # 跨话题去重，同 yahoo_news_auto_sqlite.py 的逻辑
            for topic in topics[:plan.get("quota_total", 3)]:
                extra_tags = KEYWORD_TAG_MAP.get(topic, [topic])
                yahoo_kw = yahoo_kw_map.get(topic, topic)  # 转换为日语搜索词
                if yahoo_kw != topic:
                    logger.info(f"  topic '{topic}' → Yahoo搜索词 '{yahoo_kw}'")
                try:
                    articles = fetch_news_via_cdp(
                        yahoo_kw, max_results=max_results,
                        china_filter=False, existing_keys=existing_keys,
                    )
                    for art in articles:
                        key = extract_key_from_url(art.get("link", ""))
                        if not key or key in seen_keys:
                            continue
                        seen_keys.add(key)
                        tasks.append({"art": art, "topic": topic, "extra_tags": extra_tags})
                except Exception as e:
                    logger.warning(f"  fetch failed for '{topic}': {e}")

            logger.info(f"  抓取完成: {len(tasks)} 篇待处理，并行度={max_workers}")

            # Step 2: 并行处理（翻译 + 生成 + 评分 各自独立，LLM 调用可并发）
            lock = threading.Lock()
            done_count = [0]

            def _process_one(task):
                art = task["art"]
                topic = task["topic"]
                extra_tags = task["extra_tags"]
                art = process_news_item(art, extra_tags=extra_tags, keyword=topic)
                with lock:
                    done_count[0] += 1
                    status = "⏭️" if art.get("_skip") else ("🗑️" if art.get("_discard") else "✅")
                    logger.info(f"  [{done_count[0]}/{len(tasks)}] {status} {art.get('title_zh','')[:35]}")
                return art, topic

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(_process_one, t): t for t in tasks}
                for f in as_completed(futures):
                    try:
                        art, topic = f.result()
                        if art.get("_skip"):
                            continue
                        # 空标题或空 key → 跳过，避免插入无效记录
                        if not art.get("title_zh", "").strip() or not art.get("key", "").strip():
                            logger.warning(f"  ⚠️ 跳过无效文章（空标题或空key）: {art.get('title_ja','')[:40]}")
                            continue
                        if art.get("_discard"):
                            increment_topic_discard(topic, reason=art.get("_discard_reason", ""))
                            continue
                        insert_news(art)
                        quality = art.get("_quality", {})
                        if quality.get("scores"):
                            upsert_score_dims(art["key"], quality["scores"],
                                             dim_version=quality.get("_dim_version", ""))
                            update_news(art["key"], {
                                "title_score": quality.get("title_score", 0),
                                "content_score": quality.get("content_score", 0),
                            })
                        total_fetched += 1
                    except Exception as e:
                        logger.warning(f"  process failed: {e}")

            logger.info(f"  执行完成: {total_fetched} articles saved")
        except Exception as e:
            logger.warning(f"  执行阶段失败: {e}")

        set_state(f"runner_progress_{date_str}",
                  {"phase": 3, "fetched": total_fetched}, date=date_str)

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
    _update_topic_performance_for_mature_articles()

    logger.info("Agent run complete.")


def _update_topic_performance_for_mature_articles():
    """检查发布满 7 天且有 72h 数据的文章，更新 topic_performance。
    使用单一连接处理所有行，避免大批量时文件句柄耗尽。
    """
    from scripts.sqlite_db import _connect
    from scripts.sqlite_db import get_config
    import json as _json

    eng_w = get_config("engagement_weights", default={"saves": 0.6, "comments": 0.4})
    w_s = eng_w.get("saves", 0.6)
    w_c = eng_w.get("comments", 0.4)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    with _connect() as db:
        rows = db.execute(
            "SELECT key, tags, xhs_saves, xhs_comments, xhs_views FROM news "
            "WHERE substr(replace(pub_time,'.','-'),1,10) <= date('now','localtime','-7 days') "
            "AND topic_perf_updated_at IS NULL "
            "AND xhs_collected_at LIKE '%72h%' "
            "AND status='active'"
        ).fetchall()

        for r in rows:
            saves = r["xhs_saves"] or 0
            comments = r["xhs_comments"] or 0
            views = r["xhs_views"] or 0
            eng = w_s * saves + w_c * comments
            tags = (r["tags"] or "").split(",") if r["tags"] else []

            for raw_tag in tags:
                tag = raw_tag.strip()
                if not tag:
                    continue
                existing = db.execute(
                    "SELECT post_count, avg_saves, avg_comments, avg_views, engagement_score "
                    "FROM topic_performance WHERE topic=?", (tag,)
                ).fetchone()
                if not existing:
                    db.execute(
                        "INSERT INTO topic_performance "
                        "(topic, avg_saves, avg_comments, avg_views, engagement_score, post_count, "
                        " discard_count, vertical, window_days, last_updated) "
                        "VALUES (?,?,?,?,?,1,0,'idol',90,datetime('now','localtime'))",
                        (tag, saves, comments, views, eng),
                    )
                else:
                    n = existing["post_count"]
                    new_n = n + 1
                    db.execute(
                        "UPDATE topic_performance SET "
                        "avg_saves=?, avg_comments=?, avg_views=?, engagement_score=?, "
                        "post_count=?, last_updated=datetime('now','localtime') WHERE topic=?",
                        ((existing["avg_saves"] * n + saves) / new_n,
                         (existing["avg_comments"] * n + comments) / new_n,
                         (existing["avg_views"] * n + views) / new_n,
                         (existing["engagement_score"] * n + eng) / new_n,
                         new_n, tag),
                    )

            db.execute(
                "UPDATE news SET topic_perf_updated_at=? WHERE key=?",
                (now_str, r["key"]),
            )

    if rows:
        logger.info(f"  topic_performance 更新: {len(rows)} articles")


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
