#!/usr/bin/env python3
import sys, sqlite3, os
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("SQLITE_PATH", "data/news_dev.db")

from scripts.yahoo_common import generate_story_article
DB = os.environ["SQLITE_PATH"]

conn = sqlite3.connect(DB)
rows = conn.execute("""
    SELECT key, title, title_ja, content_ja
    FROM news WHERE format = 'story' ORDER BY id DESC
""").fetchall()
conn.close()

print(f"共 {len(rows)} 篇 story 文章\n")

for key, old_title, title_ja, content_ja in rows:
    print(f"[{key[:8]}] {old_title}")
    if not content_ja:
        print("  ⚠️  无 content_ja，跳过\n"); continue

    story = generate_story_article(title_ja or '', old_title, content_ja)
    if not story:
        print("  ⚠️  生成失败，跳过\n"); continue

    new_title    = story.get('title', old_title)
    new_content  = f"{story['intro']}\n\n{story['body']}\n\n{story['outro']}"
    new_comment  = story.get('outro', '')
    new_summary  = story.get('intro', '')[:100]
    story_type   = story.get('story_type', '')

    conn = sqlite3.connect(DB)
    conn.execute("""
        UPDATE news SET title=?, content=?, comment=?, summary=?, story_type=?, updated_at=datetime('now')
        WHERE key=?
    """, (new_title, new_content, new_comment, new_summary, story_type, key))
    conn.commit()
    conn.close()
    print(f"  [{story_type}] ✅ {new_title}\n")

print("全部完成")
