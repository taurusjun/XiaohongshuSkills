#!/usr/bin/env python3
"""SQLite 数据库模块 — 替代 Notion 的读写操作"""

import sqlite3, os, json
from datetime import datetime

DB_PATH = os.environ.get("SQLITE_PATH",
    os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "news.db"))

def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
                gallery_url   TEXT,
                video_path  TEXT,
                video_caption TEXT,
                content_ja  TEXT DEFAULT '',
                pub_time    TEXT,
                title_score REAL DEFAULT 0,
                content_score REAL DEFAULT 0,
                publish_xhs INTEGER DEFAULT 0,
                publish_time TEXT,
                status      TEXT DEFAULT 'active',
                created_at  TEXT DEFAULT (datetime('now','localtime')),
                updated_at  TEXT DEFAULT (datetime('now','localtime')),
                fetch_by    TEXT DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_key ON news(key);
            CREATE INDEX IF NOT EXISTS idx_pub_time ON news(pub_time);
            CREATE INDEX IF NOT EXISTS idx_status ON news(status);
            CREATE INDEX IF NOT EXISTS idx_publish_xhs ON news(publish_xhs);

            CREATE TABLE IF NOT EXISTS score_dims (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                news_key    TEXT NOT NULL,
                dimension   TEXT NOT NULL,
                value       INTEGER DEFAULT 0,
                reason      TEXT DEFAULT '',
                UNIQUE(news_key, dimension),
                FOREIGN KEY (news_key) REFERENCES news(key)
            );
            CREATE INDEX IF NOT EXISTS idx_score_dims_key ON score_dims(news_key);
        """)
        # Compat: add fetch_by / publish_images to existing DBs
        try: db.execute("ALTER TABLE news ADD COLUMN fetch_by TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE news ADD COLUMN publish_images TEXT DEFAULT ''")
        except: pass
        try: db.execute("ALTER TABLE news ADD COLUMN content_ja TEXT DEFAULT ''")
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
                    summary, tags, image_url, original_image_url, gallery_images, gallery_video,
                    video_path, video_caption, gallery_url, content_ja, pub_time, title_score, content_score,
                    publish_xhs, publish_time, fetch_by, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,datetime('now','localtime'))
                ON CONFLICT(key) DO UPDATE SET
                    title=excluded.title, title_ja=excluded.title_ja, link=excluded.link,
                    source=excluded.source, category=excluded.category, content=excluded.content,
                    comment=excluded.comment, summary=excluded.summary, tags=excluded.tags,
                    image_url=excluded.image_url, original_image_url=excluded.original_image_url,
                    gallery_images=excluded.gallery_images, gallery_video=excluded.gallery_video,
                    video_path=excluded.video_path, video_caption=excluded.video_caption,
                    gallery_url=excluded.gallery_url, content_ja=excluded.content_ja,
                    pub_time=excluded.pub_time, title_score=excluded.title_score,
                    content_score=excluded.content_score, fetch_by=excluded.fetch_by,
                    updated_at=datetime('now','localtime')
            """, (news.get('key',''), news.get('title',''), news.get('title_ja',''),
                  news.get('link',''), news.get('source',''), news.get('category',''),
                  news.get('content',''), news.get('comment',''), news.get('summary',''),
                  tag_str, news.get('image_url',''), news.get('original_image_url',''),
                  gallery_str, news.get('gallery_video',''),
                  news.get('video_path',''), news.get('video_caption',''), news.get('gallery_url',''),
                  news.get('content_ja',''),
                  news.get('pub_time',''), news.get('title_score',0), news.get('content_score',0),
                  news.get('publish_xhs',0), news.get('publish_time',''), news.get('fetch_by','')))
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
               'video_path','video_caption','gallery_images','publish_images','gallery_video','gallery_url','content_ja',
               'publish_xhs','publish_time','status','title_score','content_score','fetch_by'}
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

def mark_published(key: str, publish_time: str = "") -> bool:
    if not publish_time:
        publish_time = datetime.now().strftime('%Y-%m-%d %H:%M')
    with _connect() as db:
        db.execute("UPDATE news SET publish_xhs=1, publish_time=?, updated_at=datetime('now','localtime') WHERE key=?",
                   (publish_time, key))
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
    '生活照': ('图片','不计分'), '搞怪照': ('图片','不计分'), '宣传照': ('图片','不计分'),
    '写真': ('图片','不计分'), '中年男照': ('图片','不计分'),
}

def upsert_score_dims(news_key: str, scores: dict):
    """scores: {dimension: {"value": 0|1, "reason": "..."}}"""
    with _connect() as db:
        for dim, data in scores.items():
            v = data.get('value', 0) if isinstance(data, dict) else int(data)
            r = data.get('reason', '') if isinstance(data, dict) else ''
            db.execute(
                "INSERT INTO score_dims (news_key, dimension, value, reason) VALUES (?,?,?,?) ON CONFLICT(news_key, dimension) DO UPDATE SET value=excluded.value, reason=excluded.reason",
                (news_key, dim, v, r)
            )

def get_score_dims(news_key: str) -> list[dict]:
    with _connect() as db:
        rows = db.execute("SELECT dimension, value, reason FROM score_dims WHERE news_key=?", (news_key,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        cat, calc = _DIM_DEFS.get(d['dimension'], ('其他', '不计分'))
        d['category'] = cat
        d['calc'] = calc
        result.append(d)
    return result
