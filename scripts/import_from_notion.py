#!/usr/bin/env python3
"""从 Notion 导入已发布到小红书的文章到 SQLite"""
import os, sys, json, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from yahoo_common import (
    NOTION_API_KEY, NOTION_DATABASE_ID, extract_key_from_url,
    _direct_session,
)
from sqlite_db import insert_news, update_news, upsert_score_dims, get_by_key, init_db

HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}


def _text(prop: dict) -> str:
    """Extract text from a Notion property"""
    if not prop:
        return ""
    t = prop.get("type", "")
    if t == "rich_text":
        return "".join(r.get("plain_text", "") for r in prop.get("rich_text", []))
    if t == "title":
        return "".join(r.get("plain_text", "") for r in prop.get("title", []))
    if t == "url":
        return prop.get("url", "") or ""
    if t == "select":
        return (prop.get("select") or {}).get("name", "")
    if t == "number":
        return str(prop.get("number", ""))
    if t == "multi_select":
        return ",".join(o.get("name", "") for o in prop.get("multi_select", []))
    if t == "checkbox":
        return str(prop.get("checkbox", False))
    return ""


def _extract_blocks(page_id: str) -> dict:
    """Read child blocks of a Notion page and extract content fields"""
    result = {"content": "", "comment": "", "title_ja": "", "summary": ""}
    blocks = []
    has_more = True
    cursor = None
    while has_more:
        url = f"https://api.notion.com/v1/blocks/{page_id}/children?page_size=100"
        if cursor:
            url += f"&start_cursor={cursor}"
        resp = _direct_session.get(url, headers=HEADERS)
        if resp.status_code != 200:
            break
        data = resp.json()
        blocks.extend(data.get("results", []))
        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")

    contents = []
    comments = []
    mode = None  # None, 'content', 'comment', 'original', 'summary'

    for b in blocks:
        t = b.get("type", "")
        texts = []
        if t in ("paragraph", "heading_3", "callout"):
            rt = b.get(t, {}).get("rich_text", [])
            texts = [r.get("plain_text", "") for r in rt]
        elif t == "bulleted_list_item":
            rt = b.get("bulleted_list_item", {}).get("rich_text", [])
            texts = ["• " + r.get("plain_text", "") for r in rt]

        line = "".join(texts).strip()
        if not line:
            continue

        if "新闻要点" in line or "News Points" in line:
            mode = "content"
            continue
        if "我的解读" in line or "My Interpretation" in line:
            mode = "comment"
            continue
        if "原文" in line or "Source" in line:
            mode = "original"
            continue
        if line.startswith("💡"):
            result["summary"] = line.lstrip("💡").strip()
            continue

        if mode == "content":
            contents.append(line)
        elif mode == "comment":
            comments.append(line)
        elif mode == "original":
            if not result["title_ja"]:
                result["title_ja"] = line

    result["content"] = "\n".join(contents)
    result["comment"] = "\n".join(comments)
    return result


def import_published():
    """导入 Notion 中 发布XHS=checked 且 发布XHS时间 有值的文章"""
    init_db()

    imported = 0
    skipped = 0
    has_more = True
    cursor = None

    while has_more:
        query = {"page_size": 100}
        if cursor:
            query["start_cursor"] = cursor

        resp = _direct_session.post(
            f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
            headers=HEADERS, json=query,
        )
        if resp.status_code != 200:
            print(f"Notion API error: {resp.status_code}")
            break

        data = resp.json()
        for page in data.get("results", []):
            props = page.get("properties", {})
            page_id = page.get("id", "")

            # Check publish_xhs
            publish_checkbox = props.get("发布XHS", {})
            if not publish_checkbox.get("checkbox", False):
                skipped += 1
                continue

            # 发布XHS时间 is a date property
            xhs_date_prop = props.get("发布XHS时间", {})
            xhs_date = xhs_date_prop.get("date") if xhs_date_prop.get("type") == "date" else None
            if xhs_date:
                publish_time_prop = (xhs_date.get("start") or "")[:16].replace("T", " ")
            else:
                publish_time_prop = pub_time

            # Extract fields
            key = _text(props.get("key", {}))
            title = _text(props.get("Name", {}))
            link = props.get("原文链接", {}).get("url", "")
            category = _text(props.get("分类", {}))
            tags_str = _text(props.get("标签", {}))
            source = _text(props.get("来源", {}))
            pub_time = _text(props.get("发布时间", {}))
            title_score = props.get("标题评分", {}).get("number") or 0
            content_score = props.get("内容评分", {}).get("number") or 0
            image_url = props.get("封面图", {}).get("url", "")
            orig_img = props.get("原图链接", {}).get("url", "")

            if not key or not title:
                # Try extract key from link
                key = extract_key_from_url(link) if link else ""
            if not key:
                skipped += 1
                continue

            # Check if already in DB
            if get_by_key(key):
                skipped += 1
                continue

            # Get content from blocks
            blocks_data = _extract_blocks(page_id)

            tags = [t.strip() for t in tags_str.split(",") if t.strip()]

            article = {
                "key": key,
                "title": title,
                "title_ja": blocks_data.get("title_ja", ""),
                "link": link,
                "source": source,
                "category": category,
                "content": blocks_data.get("content", ""),
                "comment": blocks_data.get("comment", ""),
                "summary": blocks_data.get("summary", ""),
                "tags": tags,
                "image_url": image_url,
                "original_image_url": orig_img,
                "pub_time": pub_time,
                "title_score": title_score,
                "content_score": content_score,
                "publish_xhs": 1,
                "publish_time": publish_time_prop,
                "xhs_pub_time": publish_time_prop,
            }
            insert_news(article)
            imported += 1
            print(f"  ✅ {key[:16]}... {title[:40]}")

        has_more = data.get("has_more", False)
        cursor = data.get("next_cursor")
        time.sleep(0.3)

    print(f"\nDone: imported={imported}, skipped={skipped}")
    return imported


if __name__ == "__main__":
    import_published()
