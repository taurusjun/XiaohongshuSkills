#!/usr/bin/env python3
"""SQLite 数据库模块 — 替代 Notion 的读写操作"""

import sqlite3, os, json
from datetime import datetime

from config.yahoo_conf import DB_PATH

def _connect() -> sqlite3.Connection:
    # file::memory:?cache=shared 让所有连接共享同一个内存库
    path = "file::memory:?cache=shared" if DB_PATH == ":memory:" else DB_PATH
    conn = sqlite3.connect(path, uri=True if DB_PATH == ":memory:" else False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn

def init_db():
    with _connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS news (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                key         TEXT UNIQUE NOT NULL,
                title       TEXT NOT NULL,
                title_ja    TEXT,
                link        TEXT NOT NULL,
                source      TEXT,
                category    TEXT,
                content     TEXT,
                comment     TEXT,
                summary     TEXT,
                tags        TEXT,
                image_url   TEXT,
                original_image_url TEXT,
                gallery_images TEXT,
                publish_images TEXT DEFAULT '',
                gallery_video  TEXT,
                publish_video TEXT DEFAULT '',
                gallery_url   TEXT,
                video_path  TEXT,
                video_caption TEXT,
                content_ja  TEXT DEFAULT '',
                pub_time    TEXT,
                title_score REAL DEFAULT 0,
                content_score REAL DEFAULT 0,
                publish_xhs INTEGER DEFAULT 0,
                publish_time TEXT,
                xhs_pub_time TEXT DEFAULT '',
                xhs_views          INTEGER DEFAULT 0,
                xhs_likes          INTEGER DEFAULT 0,
                xhs_saves          INTEGER DEFAULT 0,
                xhs_comments       INTEGER DEFAULT 0,
                xhs_collected_at   TEXT DEFAULT '',
                xhs_shares         INTEGER DEFAULT 0,
                xhs_fans_gained    INTEGER DEFAULT 0,
                xhs_impression     INTEGER DEFAULT 0,
                xhs_click_rate     REAL DEFAULT 0,
                xhs_watch_time     INTEGER DEFAULT 0,
                xhs_danmaku        INTEGER DEFAULT 0,
                topic_perf_updated_at TEXT DEFAULT NULL,
                status      TEXT DEFAULT 'active',
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                updated_at  TEXT DEFAULT (datetime('now','localtime')),
                fetch_by    TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_key ON news(key);
            CREATE INDEX IF NOT EXISTS idx_pub_time ON news(pub_time);
            CREATE INDEX IF NOT EXISTS idx_status ON news(status);
            CREATE INDEX IF NOT EXISTS idx_publish_xhs ON news(publish_xhs);

            CREATE TABLE IF NOT EXISTS topic_performance (
                topic                   TEXT PRIMARY KEY,
                avg_saves               REAL DEFAULT 0,
                avg_comments            REAL DEFAULT 0,
                avg_views               REAL DEFAULT 0,
                engagement_score        REAL DEFAULT 0,
                post_count              INTEGER DEFAULT 0,
                discard_count           INTEGER DEFAULT 0,
                last_discard_reason     TEXT DEFAULT '',
                topic_baseline_saves    REAL DEFAULT 0,
                topic_baseline_comments REAL DEFAULT 0,
                trend_signal            TEXT DEFAULT '',
                trend_updated_at        TEXT DEFAULT '',
                window_days             INTEGER DEFAULT 90,
                vertical                TEXT DEFAULT 'idol',
                competition_count       INTEGER DEFAULT 0,
                last_updated            TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS account_snapshots (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                snapshot_date    TEXT NOT NULL UNIQUE,
                followers        INTEGER,
                week_views       INTEGER DEFAULT 0,
                week_saves       INTEGER DEFAULT 0,
                week_likes       INTEGER DEFAULT 0,
                top_note_key     TEXT DEFAULT '',
                data_completeness TEXT DEFAULT 'full',
                created_at       TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS agent_config (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS agent_state (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                date       TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS score_dims (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                news_key    TEXT NOT NULL,
                dimension   TEXT NOT NULL,
                value       INTEGER DEFAULT 0,
                reason      TEXT DEFAULT '',
                human_override INTEGER DEFAULT 0,
                human_value    REAL,
                override_note  TEXT DEFAULT '',
                llm_value      REAL,
                dim_version    TEXT DEFAULT '',
                UNIQUE(news_key, dimension)
            );
            CREATE INDEX IF NOT EXISTS idx_score_dims_key ON score_dims(news_key);

            CREATE TABLE IF NOT EXISTS scoring_dimension_versions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                version        TEXT NOT NULL,
                dimensions_json TEXT NOT NULL,
                created_at     TEXT NOT NULL,
                created_by     TEXT DEFAULT 'system',
                change_note    TEXT DEFAULT '',
                is_active      INTEGER DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_sdv_active ON scoring_dimension_versions(is_active);

            CREATE TABLE IF NOT EXISTS metrics_history (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                news_key     TEXT NOT NULL,
                collected_at TEXT NOT NULL,
                views        INTEGER DEFAULT 0,
                likes        INTEGER DEFAULT 0,
                saves        INTEGER DEFAULT 0,
                comments     INTEGER DEFAULT 0,
                shares       INTEGER DEFAULT 0,
                fans_gained  INTEGER DEFAULT 0,
                impression   INTEGER DEFAULT 0,
                click_rate   REAL DEFAULT 0,
                watch_time   INTEGER DEFAULT 0,
                danmaku      INTEGER DEFAULT 0,
                UNIQUE(news_key, collected_at)
            );
            CREATE INDEX IF NOT EXISTS idx_mh_key ON metrics_history(news_key);
            CREATE INDEX IF NOT EXISTS idx_mh_time ON metrics_history(collected_at);
        """)
        # Compat: add columns to existing DBs
        _news_compat = [
            ("fetch_by", "TEXT DEFAULT ''"),
            ("publish_images", "TEXT DEFAULT ''"),
            ("publish_video", "TEXT DEFAULT ''"),
            ("content_ja", "TEXT DEFAULT ''"),
            ("xhs_pub_time", "TEXT DEFAULT ''"),
            ("xhs_views", "INTEGER DEFAULT 0"),
            ("xhs_likes", "INTEGER DEFAULT 0"),
            ("xhs_saves", "INTEGER DEFAULT 0"),
            ("xhs_comments", "INTEGER DEFAULT 0"),
            ("xhs_collected_at", "TEXT DEFAULT ''"),
            ("topic_perf_updated_at", "TEXT DEFAULT NULL"),
            ("xhs_shares", "INTEGER DEFAULT 0"),
            ("xhs_fans_gained", "INTEGER DEFAULT 0"),
            ("xhs_impression", "INTEGER DEFAULT 0"),
            ("xhs_click_rate", "REAL DEFAULT 0"),
            ("xhs_watch_time", "INTEGER DEFAULT 0"),
            ("xhs_danmaku", "INTEGER DEFAULT 0"),
        ]
        for col, col_type in _news_compat:
            try: db.execute(f"ALTER TABLE news ADD COLUMN {col} {col_type}")
            except: pass
        for col, col_type in [("shares", "INTEGER DEFAULT 0"),
                               ("fans_gained", "INTEGER DEFAULT 0"),
                               ("impression", "INTEGER DEFAULT 0"),
                               ("click_rate", "REAL DEFAULT 0"),
                               ("watch_time", "INTEGER DEFAULT 0"),
                               ("danmaku", "INTEGER DEFAULT 0")]:
            try: db.execute(f"ALTER TABLE metrics_history ADD COLUMN {col} {col_type}")
            except: pass
        for col, col_type in [("human_override", "INTEGER DEFAULT 0"),
                               ("human_value", "REAL"),
                               ("override_note", "TEXT DEFAULT ''"),
                               ("llm_value", "REAL"),
                               ("dim_version", "TEXT DEFAULT ''")]:
            try: db.execute(f"ALTER TABLE score_dims ADD COLUMN {col} {col_type}")
            except: pass

# ── 新闻 CRUD ──

def insert_news(news: dict) -> bool:
    tags = news.get('tags', [])
    tag_str = ','.join(tags) if isinstance(tags, list) else str(tags or '')
    gallery = news.get('gallery_images', [])
    gallery_str = json.dumps(gallery) if isinstance(gallery, list) else str(gallery or '')
    with _connect() as db:
        try:
            db.execute("""
                INSERT INTO news (key, title, title_ja, link, source, category, content, comment,
                    summary, tags, image_url, original_image_url, gallery_images, publish_images,
                    gallery_video, publish_video, video_path, video_caption, gallery_url, content_ja,
                    pub_time, title_score, content_score, publish_xhs, publish_time, xhs_pub_time, fetch_by, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
                ON CONFLICT(key) DO UPDATE SET
                    title=excluded.title, title_ja=excluded.title_ja, link=excluded.link,
                    source=excluded.source, category=excluded.category, content=excluded.content,
                    comment=excluded.comment, summary=excluded.summary, tags=excluded.tags,
                    image_url=excluded.image_url, original_image_url=excluded.original_image_url,
                    gallery_images=excluded.gallery_images, publish_images=excluded.publish_images,
                    gallery_video=excluded.gallery_video, publish_video=excluded.publish_video,
                    video_path=excluded.video_path, video_caption=excluded.video_caption,
                    gallery_url=excluded.gallery_url, content_ja=excluded.content_ja,
                    pub_time=excluded.pub_time, title_score=excluded.title_score,
                    content_score=excluded.content_score, publish_xhs=excluded.publish_xhs,
                    publish_time=excluded.publish_time, xhs_pub_time=excluded.xhs_pub_time,
                    fetch_by=excluded.fetch_by,
                    updated_at=datetime('now','localtime')
            """, (news.get('key',''), news.get('title',''), news.get('title_ja',''),
                  news.get('link',''), news.get('source',''), news.get('category',''),
                  news.get('content',''), news.get('comment',''), news.get('summary',''),
                  tag_str, news.get('image_url',''), news.get('original_image_url',''),
                  gallery_str, news.get('publish_images',''),
                  news.get('gallery_video',''), news.get('publish_video',''),
                  news.get('video_path',''), news.get('video_caption',''), news.get('gallery_url',''),
                  news.get('content_ja',''),
                  news.get('pub_time',''), news.get('title_score',0), news.get('content_score',0),
                  news.get('publish_xhs',0), news.get('publish_time',''), news.get('xhs_pub_time',''), news.get('fetch_by','')))
            return True
        except Exception as e:
            print(f"  ⚠️ SQLite 写入失败: {e}")
            return False

def load_today_keys(date_str: str = "") -> set[str]:
    if not date_str:
        date_str = datetime.now().strftime('%Y.%m.%d')
    with _connect() as db:
        rows = db.execute("SELECT key FROM news WHERE created_at LIKE ? AND status='active'", (f"{date_str}%",)).fetchall()
    return {r['key'] for r in rows}

def get_by_key(key: str) -> dict | None:
    with _connect() as db:
        row = db.execute("SELECT * FROM news WHERE key=?", (key,)).fetchone()
        if row:
            d = dict(row)
            d['tags'] = d['tags'].split(',') if d.get('tags') else []
            return d
    return None

def query_news(date_from: str = "", date_to: str = "", category: str = "",
               status: str = "active", search: str = "", publish_xhs: str = "",
               limit: int = 200, sort_by: str = "created_at", sort_dir: str = "DESC") -> list[dict]:
    valid_sort = {'pub_time','created_at','title_score','content_score','title'}
    if sort_by not in valid_sort:
        sort_by = 'created_at'
    sort_dir = 'DESC' if sort_dir.upper() == 'DESC' else 'ASC'
    sql = f"SELECT * FROM news WHERE status=? "
    params = [status]
    if date_from:
        sql += "AND created_at >= ? "; params.append(date_from)
    if date_to:
        sql += "AND created_at <= ? || ' 23:59:59' "; params.append(date_to)
    if category:
        sql += "AND category = ? "; params.append(category)
    if publish_xhs == 'published':
        sql += "AND publish_xhs=1 AND publish_time IS NOT NULL AND publish_time!='' "
    elif publish_xhs == 'pending':
        sql += "AND publish_xhs=1 AND (publish_time IS NULL OR publish_time='') "
    elif publish_xhs == 'unpublished':
        sql += "AND publish_xhs=0 "
    if search:
        sql += "AND (title LIKE ? OR content LIKE ? OR comment LIKE ?) "
        params.extend([f"%{search}%"]*3)
    sql += f"ORDER BY {sort_by} {sort_dir} LIMIT ?"
    params.append(limit)
    with _connect() as db:
        rows = db.execute(sql, params).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d['tags'] = d.get('tags','').split(',') if d.get('tags') else []
        result.append(d)
    return result

def update_news(key: str, fields: dict) -> bool:
    allowed = {'title','content','comment','summary','category','tags','image_url',
               'video_path','video_caption','gallery_images','publish_images','gallery_video','publish_video','gallery_url','content_ja',
               'publish_xhs','publish_time','xhs_pub_time','status','title_score','content_score','fetch_by',
               'xhs_views','xhs_likes','xhs_saves','xhs_comments','xhs_collected_at',
               'xhs_shares','xhs_fans_gained','xhs_impression','xhs_click_rate','xhs_watch_time','xhs_danmaku',
               'topic_perf_updated_at'}
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return False
    if 'tags' in updates and isinstance(updates['tags'], list):
        updates['tags'] = ','.join(updates['tags'])
    if 'gallery_images' in updates and isinstance(updates['gallery_images'], list):
        updates['gallery_images'] = json.dumps(updates['gallery_images'])
    if 'publish_images' in updates and isinstance(updates['publish_images'], list):
        updates['publish_images'] = json.dumps(updates['publish_images'])
    set_clause = ', '.join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [key]
    with _connect() as db:
        db.execute(f"UPDATE news SET {set_clause}, updated_at=datetime('now','localtime') WHERE key=?", vals)
    return True

def mark_published(key: str, publish_time: str = "", xhs_pub_time: str = "") -> bool:
    if not publish_time:
        publish_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    if not xhs_pub_time:
        xhs_pub_time = publish_time
    with _connect() as db:
        db.execute("UPDATE news SET publish_xhs=1, publish_time=?, xhs_pub_time=?, updated_at=datetime('now','localtime') WHERE key=?",
                   (publish_time, xhs_pub_time, key))
    return True

def get_pending_publish(limit: int = 20) -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM news WHERE publish_xhs=1 AND (publish_time IS NULL OR publish_time='') AND status='active' ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]

def archive_old(days: int = 30):
    with _connect() as db:
        db.execute("UPDATE news SET status='archived', updated_at=datetime('now','localtime') WHERE status='active' AND created_at < datetime('now','localtime', ?)", (f'-{days} days',))

def stats() -> dict:
    with _connect() as db:
        total = db.execute("SELECT COUNT(*) as n FROM news WHERE status='active'").fetchone()['n']
        today = db.execute("SELECT COUNT(*) as n FROM news WHERE created_at LIKE ? AND status='active'",
                           (datetime.now().strftime('%Y-%m-%d')+'%',)).fetchone()['n']
        pending = db.execute("SELECT COUNT(*) as n FROM news WHERE publish_xhs=1 AND (publish_time IS NULL OR publish_time='') AND status='active'").fetchone()['n']
        published = db.execute("SELECT COUNT(*) as n FROM news WHERE publish_time IS NOT NULL AND publish_time!='' AND status='active'").fetchone()['n']
    return {"total": total, "today": today, "pending": pending, "published": published}

# ── 记忆层 CRUD (Module A) ──

_dim_weights_cache = {"weights": {}, "cached_updated_at": ""}


def get_top_topics(n: int = 10, window_days: int = 90) -> list[dict]:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM topic_performance WHERE last_updated >= datetime('now','localtime',?) ORDER BY engagement_score DESC LIMIT ?",
            (f'-{window_days} days', n),
        ).fetchall()
    return [dict(r) for r in rows]


def get_recent_performance(days: int = 7) -> dict:
    with _connect() as db:
        rows = db.execute(
            "SELECT * FROM account_snapshots WHERE snapshot_date >= date('now','localtime',?) AND followers IS NOT NULL ORDER BY snapshot_date DESC LIMIT ?",
            (f'-{days} days', days),
        ).fetchall()
    if not rows:
        return {"avg_week_saves": 0.0, "avg_week_views": 0.0, "data_completeness": "no_data"}
    saves = [r["week_saves"] for r in rows if r["week_saves"]]
    views = [r["week_views"] for r in rows if r["week_views"]]
    return {
        "avg_week_saves": sum(saves) / len(saves) if saves else 0.0,
        "avg_week_views": sum(views) / len(views) if views else 0.0,
        "data_completeness": rows[-1]["data_completeness"] if rows else "full",
    }


def upsert_topic_performance(topic: str, saves: float = 0, comments: float = 0,
                              views: float = 0, **kwargs):
    eng_w = get_config("engagement_weights", default={"saves": 0.6, "comments": 0.4})
    eng_score = eng_w.get("saves", 0.6) * saves + eng_w.get("comments", 0.4) * comments
    import json as _json
    with _connect() as db:
        existing = db.execute("SELECT post_count FROM topic_performance WHERE topic=?", (topic,)).fetchone()
        post_count = (existing["post_count"] + 1) if existing else 1
        db.execute(
            """INSERT OR REPLACE INTO topic_performance
               (topic, avg_saves, avg_comments, avg_views, engagement_score, post_count,
                discard_count, last_discard_reason, topic_baseline_saves, topic_baseline_comments,
                trend_signal, trend_updated_at, vertical, competition_count,
                window_days, last_updated)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                       COALESCE((SELECT window_days FROM topic_performance WHERE topic=?), 90),
                       datetime('now','localtime'))""",
            (topic, saves, comments, views, eng_score, post_count,
             kwargs.get("discard_count", 0), kwargs.get("last_discard_reason", ""),
             kwargs.get("topic_baseline_saves", 0.0), kwargs.get("topic_baseline_comments", 0.0),
             _json.dumps(kwargs.get("trend_signal", {}), ensure_ascii=False) if isinstance(kwargs.get("trend_signal"), dict) else str(kwargs.get("trend_signal", "")),
             kwargs.get("trend_updated_at", ""),
             kwargs.get("vertical", "idol"),
             kwargs.get("competition_count", 0),
             topic),
        )


def get_config(key: str, default=None):
    import json as _json
    with _connect() as db:
        row = db.execute("SELECT value FROM agent_config WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return _json.loads(row["value"])
    except Exception:
        return row["value"]


def set_config(key: str, value) -> None:
    global _dim_weights_cache
    import json as _json
    v = _json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    with _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO agent_config (key, value, updated_at) VALUES (?,?,datetime('now','localtime'))",
            (key, v),
        )
    if key == "dim_weights":
        _dim_weights_cache = {"weights": {}, "cached_updated_at": ""}


def get_state(key: str, default=None):
    import json as _json
    with _connect() as db:
        row = db.execute("SELECT value FROM agent_state WHERE key=?", (key,)).fetchone()
    if not row:
        return default
    try:
        return _json.loads(row["value"])
    except Exception:
        return row["value"]


def set_state(key: str, value, date: str = "") -> None:
    import json as _json
    from datetime import datetime as _dt
    v = _json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    d = date or _dt.now().strftime("%Y%m%d")
    with _connect() as db:
        db.execute(
            "INSERT OR REPLACE INTO agent_state (key, value, date, updated_at) VALUES (?,?,?,datetime('now','localtime'))",
            (key, v, d),
        )


def cleanup_old_states(days: int = 7):
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.now() - _td(days=days)).strftime("%Y%m%d")
    with _connect() as db:
        db.execute("DELETE FROM agent_state WHERE date!='' AND date < ?", (cutoff,))


def record_metrics(news_key: str, collected_at: str,
                   views: int = 0, likes: int = 0,
                   saves: int = 0, comments: int = 0,
                   shares: int = 0, fans_gained: int = 0,
                   impression: int = 0, click_rate: float = 0,
                   watch_time: int = 0, danmaku: int = 0):
    """写入 metrics_history + 更新 news 最新值"""
    with _connect() as db:
        db.execute(
            """INSERT OR REPLACE INTO metrics_history
               (news_key, collected_at, views, likes, saves, comments,
                shares, fans_gained, impression, click_rate, watch_time, danmaku)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (news_key, collected_at, views, likes, saves, comments,
             shares, fans_gained, impression, click_rate, watch_time, danmaku),
        )
        db.execute(
            """UPDATE news SET xhs_views=?, xhs_likes=?, xhs_saves=?, xhs_comments=?,
               xhs_shares=?, xhs_fans_gained=?, xhs_impression=?, xhs_click_rate=?,
               xhs_watch_time=?, xhs_danmaku=?, updated_at=datetime('now','localtime') WHERE key=?""",
            (views, likes, saves, comments, shares, fans_gained, impression, click_rate, watch_time, danmaku, news_key),
        )


def cleanup_old_metrics(days: int = 90):
    """删除 N 天前的指标历史记录"""
    from datetime import datetime as _dt, timedelta as _td
    cutoff = (_dt.now() - _td(days=days)).strftime("%Y-%m-%d %H:%M")
    with _connect() as db:
        db.execute("DELETE FROM metrics_history WHERE collected_at < ?", (cutoff,))


def load_dim_weights() -> dict:
    global _dim_weights_cache
    import json as _json
    with _connect() as db:
        row = db.execute(
            "SELECT value, updated_at FROM agent_config WHERE key='dim_weights'"
        ).fetchone()
    if not row:
        return {}
    if row["updated_at"] != _dim_weights_cache["cached_updated_at"]:
        _dim_weights_cache["weights"] = _json.loads(row["value"])
        _dim_weights_cache["cached_updated_at"] = row["updated_at"]
    return _dim_weights_cache["weights"]


# ── 评分 ──

# 维度定义：dimension → (category, calc)
_DIM_DEFS = {
    '剧情感': ('标题','加分'), '冲突感': ('标题','加分'), '猎奇感': ('标题','加分'),
    '用户共鸣': ('标题','加分'), '名人': ('标题','加分'), '热点': ('标题','加分'),
    '简单通知': ('标题','减分'), '震惊体': ('标题','减分'), '概括全部': ('标题','减分'),
    '原创度': ('内容','加分'), '趣味性': ('内容','加分'), '有用信息': ('内容','加分'),
    '对立信息': ('内容','加分'), '视频': ('内容','加分'),
    '离题': ('内容','减分'), '啰嗦重复': ('内容','减分'), '主动讨赏': ('内容','减分'),
    '负面情绪': ('内容','减分'),
    '收藏驱动': ('内容','加分'),
    '评论引导性': ('内容','加分'),
    '受众规模': ('内容','加分'),
    '生活照': ('图片','不计分'), '搞怪照': ('图片','不计分'), '宣传照': ('图片','不计分'),
    '写真': ('图片','不计分'), '中年男照': ('图片','不计分'),
}

# ── 维度版本管理 ──

_dim_cache = {"dims": [], "cached_created_at": ""}


def init_dimension_versions():
    """从 scoring_dimensions.json 初始化版本表（仅首次）"""
    with _connect() as db:
        existing = db.execute("SELECT COUNT(*) as n FROM scoring_dimension_versions").fetchone()
        if existing["n"] > 0:
            return
    from pathlib import Path
    import json as _json
    json_path = Path(__file__).parent.parent / "config" / "scoring_dimensions.json"
    try:
        cfg = _json.loads(json_path.read_text(encoding="utf-8"))
        dims_json = _json.dumps(cfg["dimensions"], ensure_ascii=False)
        with _connect() as db:
            db.execute(
                "INSERT INTO scoring_dimension_versions (version, dimensions_json, created_at, created_by, is_active) VALUES (?,?,datetime('now','localtime'),'system',1)",
                (cfg["version"], dims_json),
            )
    except Exception as e:
        print(f"  ⚠️ 初始化维度版本失败: {e}")


def load_active_dimensions() -> list[dict]:
    """加载当前生效的维度定义，带进程级缓存"""
    global _dim_cache
    import json as _json
    with _connect() as db:
        row = db.execute(
            "SELECT version, dimensions_json, created_at FROM scoring_dimension_versions WHERE is_active=1"
        ).fetchone()
        if not row:
            init_dimension_versions()
            row = db.execute(
                "SELECT version, dimensions_json, created_at FROM scoring_dimension_versions WHERE is_active=1"
            ).fetchone()
        if not row:
            return []
        if row["created_at"] != _dim_cache["cached_created_at"]:
            _dim_cache["dims"] = _json.loads(row["dimensions_json"])
            _dim_cache["cached_created_at"] = row["created_at"]
    return _dim_cache["dims"]


def commit_dimension_version(dims: list[dict], change_note: str, created_by: str = "human"):
    """提交新版本，自动递增 minor 版本号，清空缓存"""
    global _dim_cache
    import json as _json
    with _connect() as db:
        current = db.execute(
            "SELECT version FROM scoring_dimension_versions WHERE is_active=1"
        ).fetchone()
        if current:
            parts = current["version"].split(".")
            parts[1] = str(int(parts[1]) + 1)
            new_version = ".".join(parts)
        else:
            new_version = "1.0.0"
        dims_json = _json.dumps(dims, ensure_ascii=False)
        db.execute("UPDATE scoring_dimension_versions SET is_active=0")
        db.execute(
            "INSERT INTO scoring_dimension_versions (version, dimensions_json, created_at, created_by, change_note, is_active) VALUES (?,?,datetime('now','localtime'),?,?,1)",
            (new_version, dims_json, created_by, change_note),
        )
    _dim_cache = {"dims": [], "cached_created_at": ""}


def rollback_dimension_version(version: str) -> bool:
    """回滚到指定版本"""
    global _dim_cache
    with _connect() as db:
        exists = db.execute(
            "SELECT id FROM scoring_dimension_versions WHERE version=?", (version,)
        ).fetchone()
        if not exists:
            return False
        db.execute("UPDATE scoring_dimension_versions SET is_active=0")
        db.execute("UPDATE scoring_dimension_versions SET is_active=1 WHERE version=?", (version,))
    _dim_cache = {"dims": [], "cached_created_at": ""}
    return True


def upsert_score_dims(news_key: str, scores: dict, dim_version: str = ""):
    """scores: {dimension: {"value": 0|1, "reason": "..."}}"""
    with _connect() as db:
        for dim, data in scores.items():
            v = data.get('value', 0) if isinstance(data, dict) else int(data)
            r = data.get('reason', '') if isinstance(data, dict) else ''
            db.execute(
                "INSERT INTO score_dims (news_key, dimension, value, reason, dim_version) VALUES (?,?,?,?,?) ON CONFLICT(news_key, dimension) DO UPDATE SET value=excluded.value, reason=excluded.reason, dim_version=excluded.dim_version",
                (news_key, dim, v, r, dim_version)
            )

def recalculate_scores(news_key: str) -> dict:
    """根据 score_dims 重算 title_score/content_score，优先使用人工纠正值"""
    dims = get_score_dims(news_key)
    if not dims:
        return {"title_score": 0, "content_score": 0}
    by_name = {}
    for d in dims:
        val = d["human_value"] if d.get("human_override") else d["value"]
        cat, calc = _DIM_DEFS.get(d["dimension"], ("其他", "不计分"))
        by_name[d["dimension"]] = {"value": float(val or 0), "category": cat, "calc": calc}
    title_score = sum(by_name[d]["value"] for d in by_name if by_name[d]["category"] == "标题" and by_name[d]["calc"] == "加分")
    title_score -= sum(by_name[d]["value"] for d in by_name if by_name[d]["category"] == "标题" and by_name[d]["calc"] == "减分")
    content_score = sum(by_name[d]["value"] for d in by_name if by_name[d]["category"] == "内容" and by_name[d]["calc"] == "加分")
    content_score -= sum(by_name[d]["value"] for d in by_name if by_name[d]["category"] == "内容" and by_name[d]["calc"] == "减分")
    title_score = max(0.0, min(5.0, title_score))
    content_score = max(0.0, min(5.0, content_score))
    update_news(news_key, {"title_score": title_score, "content_score": content_score})
    return {"title_score": title_score, "content_score": content_score}


def get_score_dims(news_key: str) -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT dimension, value, reason, human_override, human_value, override_note, llm_value, dim_version FROM score_dims WHERE news_key=?", (news_key,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        cat, calc = _DIM_DEFS.get(d['dimension'], ('其他', '不计分'))
        d['category'] = cat
        d['calc'] = calc
        result.append(d)
    return result
