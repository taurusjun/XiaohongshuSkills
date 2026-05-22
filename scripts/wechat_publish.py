#!/usr/bin/env python3
"""wechat_publish.py — 将 Markdown 长文转为公众号草稿（XHS 可一键导入）"""
import sys, os, re, json, time, requests
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

WECHAT_APPID = os.environ.get("WECHAT_APPID", "")
WECHAT_SECRET = os.environ.get("WECHAT_SECRET", "")
WECHAT_API = "https://api.weixin.qq.com/cgi-bin"

_token_cache = {"token": "", "expires_at": 0}


def get_access_token() -> str:
    now = time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"] - 300:
        return _token_cache["token"]
    resp = requests.get(f"{WECHAT_API}/token", params={
        "grant_type": "client_credential",
        "appid": WECHAT_APPID, "secret": WECHAT_SECRET,
    }, timeout=10).json()
    _token_cache["token"] = resp.get("access_token", "")
    _token_cache["expires_at"] = now + resp.get("expires_in", 7200)
    return _token_cache["token"]


def upload_image(image_path: str) -> str:
    """上传永久图片素材，返回 URL"""
    token = get_access_token()
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"{WECHAT_API}/material/add_material?access_token={token}&type=image",
            files={"media": (os.path.basename(image_path), f, "image/jpeg")},
            timeout=30,
        ).json()
    return resp.get("url", "")


def upload_thumb(image_path: str) -> str:
    """上传封面图（thumb），返回 media_id"""
    if not os.path.exists(image_path):
        return ""
    token = get_access_token()
    with open(image_path, "rb") as f:
        resp = requests.post(
            f"{WECHAT_API}/material/add_material?access_token={token}&type=thumb",
            files={"media": (os.path.basename(image_path), f, "image/jpeg")},
            timeout=30,
        ).json()
    return resp.get("media_id", "")


def create_draft(title: str, html_content: str, thumb_media_id: str = "") -> dict:
    """创建公众号草稿"""
    token = get_access_token()
    articles = [{
        "title": title,
        "content": html_content,
        "content_source_url": "",
        "need_open_comment": 0,
        "only_fans_can_comment": 0,
        "thumb_media_id": thumb_media_id,
    }]
    resp = requests.post(
        f"{WECHAT_API}/draft/add?access_token={token}",
        data=json.dumps({"articles": articles}, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        timeout=30,
    ).json()
    return resp


def md_to_wechat_html(md_text: str) -> str:
    """极简 Markdown → 公众号兼容 HTML"""
    lines = md_text.split("\n")
    html = []
    in_paragraph = False

    for line in lines:
        line = line.rstrip()
        # Headers
        if line.startswith("## "):
            if in_paragraph: html.append("</p>"); in_paragraph = False
            html.append(f'<h2 style="font-size:18px;color:#333;margin:20px 0 12px;padding-left:8px;border-left:3px solid #ff6b35;">{line[3:]}</h2>')
            continue
        if line.startswith("# "):
            if in_paragraph: html.append("</p>"); in_paragraph = False
            html.append(f'<h1 style="font-size:20px;color:#222;margin:20px 0 14px;text-align:center;">{line[2:]}</h1>')
            continue
        # Images
        m = re.match(r"!\[.*\]\((.+)\)", line)
        if m:
            if in_paragraph: html.append("</p>"); in_paragraph = False
            img_path = m.group(1).strip()
            if os.path.exists(img_path):
                url = upload_image(img_path)
                if url:
                    html.append(f'<p style="text-align:center;margin:16px 0"><img src="{url}" style="max-width:100%;border-radius:4px"></p>')
                else:
                    html.append(f'<p style="text-align:center;color:#999;font-size:12px">[图片: {os.path.basename(img_path)}]</p>')
            continue
        # Empty line
        if not line:
            if in_paragraph: html.append("</p>"); in_paragraph = False
            continue
        # Regular paragraph
        if not in_paragraph:
            html.append('<p style="font-size:15px;color:#333;line-height:1.8;margin:0 0 12px">')
            in_paragraph = True
        else:
            html.append("<br>")
        html.append(line)

    if in_paragraph:
        html.append("</p>")

    body = "\n".join(html)
    return f"""<section style="padding:10px 0">{body}</section>"""


def publish_article(key: str) -> dict:
    """从 DB 读取文章，转为公众号草稿"""
    from scripts.sqlite_db import get_by_key
    art = dict(get_by_key(key))
    if not art:
        return {"error": "article not found"}

    title = art["title"]
    content = art.get("content", "") or ""
    if not content:
        return {"error": "no content"}

    # Process image markers: 【图片N：path|path2】
    def replace_img(m):
        paths = m.group(1).split("|")
        result = []
        for i, p in enumerate(paths):
            p = p.strip()
            if os.path.exists(p):
                result.append(f"![图片{i+1}]({p})")
        return "\n".join(result)

    content = re.sub(r"【图片\d+：([^】]+)】", replace_img, content)

    # Build Markdown
    md = f"# {title}\n\n{content}"

    # Convert
    html = md_to_wechat_html(md)

    # Upload cover from article's first image
    cache_dir = os.path.expanduser(f"~/.cache/xhs_images/{key}")
    cover_path = os.path.join(cache_dir, "cover.jpg")
    if not os.path.exists(cover_path):
        jpgs = sorted(Path(cache_dir).glob("*.jpg"))
        if jpgs:
            cover_path = str(jpgs[0])
    thumb_id = upload_thumb(cover_path) if os.path.exists(cover_path) else ""

    # WeChat API 草稿标题限制严格，截断后可在后台重命名
    safe_title = title if len(title.encode('utf-8')) <= 20 else title[:10]

    result = create_draft(safe_title, html, thumb_media_id=thumb_id)

    # Save preview locally
    preview_path = f"/tmp/wechat_draft_{art['key'][:12]}.html"
    with open(preview_path, "w") as f:
        f.write(html)

    return {
        "title": safe_title,
        "original_title": art["title"],
        "draft_result": result,
        "preview": preview_path,
        "media_id": result.get("media_id", ""),
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--key", required=True, help="news key")
    p.add_argument("--preview-only", action="store_true", help="只生成 HTML 预览，不推草稿")
    args = p.parse_args()

    if args.preview_only:
        from scripts.sqlite_db import get_by_key
        art = dict(get_by_key(args.key))
        content = art.get("content", "")
        content = re.sub(r"【图片\d+：([^】]+)】",
                         lambda m: f"![图]({m.group(1).split('|')[0].strip()})", content)
        md = f"# {art['title']}\n\n{content}"
        html = md_to_wechat_html(md)
        preview = f"/tmp/wechat_preview_{args.key[:12]}.html"
        with open(preview, "w") as f:
            f.write(html)
        print(f"Preview: {preview}")
    else:
        result = publish_article(args.key)
        print(json.dumps(result, indent=2, ensure_ascii=False))
