#!/usr/bin/env python3
"""
wechat_publisher.py — 将 story 文章发布为微信公众号草稿

流程：
  1. 获取 access_token
  2. 上传封面图 + 正文图片 → 微信 media_id
  3. 将文章内容（含 ## 小标题、【图片N：/path】标记）渲染为微信 HTML
  4. 调用草稿接口创建草稿，返回 media_id

用法:
  python scripts/wechat_publisher.py --key <article_key>
  python scripts/wechat_publisher.py --key <article_key> --publish  # 直接发布（需白名单）
"""

import sys
import os
import re
import json
import time
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import requests

# ── 配置 ──────────────────────────────────────────────────────
_CONF_PATH = Path(__file__).parent.parent / "config" / "wechat_conf.json"
_TOKEN_CACHE = Path(__file__).parent.parent / "config" / ".wechat_token_cache.json"

WX_API = "https://api.weixin.qq.com/cgi-bin"


def _load_conf() -> dict:
    if not _CONF_PATH.exists():
        raise FileNotFoundError(f"微信配置文件不存在: {_CONF_PATH}")
    return json.loads(_CONF_PATH.read_text())


def _get_access_token(conf: dict) -> str:
    """获取 access_token，带本地缓存（有效期 7200s）。"""
    appid = conf["appid"]
    secret = conf["appsecret"]

    # 读缓存
    if _TOKEN_CACHE.exists():
        cache = json.loads(_TOKEN_CACHE.read_text())
        if cache.get("appid") == appid and cache.get("expires_at", 0) > time.time() + 60:
            return cache["access_token"]

    url = f"{WX_API}/token?grant_type=client_credential&appid={appid}&secret={secret}"
    resp = requests.get(url, timeout=10).json()
    if "access_token" not in resp:
        raise RuntimeError(f"获取 access_token 失败: {resp}")

    token = resp["access_token"]
    _TOKEN_CACHE.write_text(json.dumps({
        "appid": appid,
        "access_token": token,
        "expires_at": time.time() + resp.get("expires_in", 7200),
    }))
    return token


def _upload_image(token: str, img_path: str) -> str:
    """上传图片到微信永久素材库，返回 media_id。"""
    url = f"{WX_API}/material/add_material?access_token={token}&type=image"
    with open(img_path, "rb") as f:
        ext = Path(img_path).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "gif": "image/gif"}.get(ext, "image/jpeg")
        resp = requests.post(url, files={"media": (Path(img_path).name, f, mime)}, timeout=30).json()

    if "media_id" not in resp:
        raise RuntimeError(f"上传图片失败 {img_path}: {resp}")
    print(f"    ✅ 图片上传: {Path(img_path).name} → media_id={resp['media_id'][:12]}...")
    return resp["media_id"]


def _upload_image_for_content(token: str, img_path: str) -> str:
    """上传用于正文的图片（新增素材接口），返回 URL。"""
    url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}"
    with open(img_path, "rb") as f:
        ext = Path(img_path).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
        resp = requests.post(url, files={"media": (Path(img_path).name, f, mime)}, timeout=30).json()

    if "url" not in resp:
        raise RuntimeError(f"上传正文图片失败 {img_path}: {resp}")
    print(f"    🖼️ 正文图片: {Path(img_path).name} → {resp['url'][:50]}...")
    return resp["url"]


# ── Markdown → 微信 HTML ──────────────────────────────────────

_IMG_RE = re.compile(r'【(?:图片|推文)\d+：([^】]*)】')

_STYLE = {
    "h2": 'font-size:18px;font-weight:700;color:#1a1a1a;margin:24px 0 10px;border-left:4px solid #ff2442;padding-left:10px',
    "h3": 'font-size:16px;font-weight:600;color:#333;margin:18px 0 8px',
    "p":  'font-size:15px;line-height:1.9;color:#333;margin:0 0 14px',
    "img": 'max-width:100%;border-radius:8px;display:block;margin:12px auto',
    "caption": 'font-size:12px;color:#999;text-align:center;margin:-8px 0 16px',
}


def _render_html(content: str, img_url_map: dict) -> str:
    """将文章内容渲染为微信 HTML。
    img_url_map: {"/local/path.jpg": "https://mmbiz.qpic.cn/..."}
    """
    parts = []
    last = 0

    def _flush_text(text: str):
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            if s.startswith("### "):
                parts.append(f'<h3 style="{_STYLE["h3"]}">{s[4:]}</h3>')
            elif s.startswith("## "):
                parts.append(f'<h2 style="{_STYLE["h2"]}">{s[3:]}</h2>')
            else:
                parts.append(f'<p style="{_STYLE["p"]}">{s}</p>')

    for m in _IMG_RE.finditer(content):
        _flush_text(content[last:m.start()])

        inner = m.group(1)
        # inner 可能是 /path1|/path2 或描述文字
        if inner.startswith("/"):
            paths = [p for p in inner.split("|") if p.startswith("/")]
            # 多图用 flex 横排
            if len(paths) > 1:
                cells = ""
                for p in paths:
                    url = img_url_map.get(p, "")
                    if url:
                        cells += f'<img src="{url}" style="width:{96//len(paths)}%;border-radius:6px;margin:2px">'
                if cells:
                    parts.append(f'<div style="display:flex;gap:4px;margin:12px 0">{cells}</div>')
            elif paths:
                url = img_url_map.get(paths[0], "")
                if url:
                    # 取 caption 描述（非路径部分）
                    full_cap = m.group(0)
                    cap_text = _IMG_RE.sub("", full_cap).strip() or Path(paths[0]).name
                    parts.append(f'<img src="{url}" style="{_STYLE["img"]}">')
                    if cap_text and not cap_text.startswith("/"):
                        parts.append(f'<p style="{_STYLE["caption"]}">{cap_text}</p>')
        else:
            # 无图，只保留描述作注释
            pass

        last = m.end()

    _flush_text(content[last:])

    body = "\n".join(parts)
    return f"""<section style="max-width:680px;margin:0 auto;font-family:-apple-system,sans-serif">
{body}
</section>"""


def publish_article(article_key: str, publish: bool = False):
    """完整流程：读取文章 → 上传图片 → 创建草稿。"""
    from scripts.sqlite_db import _connect

    os.environ.setdefault("SQLITE_PATH", "data/news_dev.db")

    with _connect() as db:
        r = db.execute(
            "SELECT title, content, image_url, original_image_url, gallery_images FROM news WHERE key=?",
            (article_key,)
        ).fetchone()

    if not r:
        print(f"文章 {article_key} 不存在")
        return

    title = r["title"]
    content = r["content"]
    gallery_raw = r["gallery_images"]
    gallery = json.loads(gallery_raw) if isinstance(gallery_raw, str) and gallery_raw else []

    print(f"文章: {title}")
    print(f"图片: {len(gallery)} 张")

    conf = _load_conf()
    token = _get_access_token(conf)
    print(f"access_token: {token[:12]}...")

    # ── 上传封面图 ────────────────────────────────────────────
    cover_path = next(
        (p for p in gallery if "article_" in Path(p).name or "cover" in Path(p).name),
        gallery[0] if gallery else None
    )
    thumb_media_id = None
    if cover_path and os.path.exists(cover_path):
        print(f"\n[1] 上传封面图: {Path(cover_path).name}")
        thumb_media_id = _upload_image(token, cover_path)
    else:
        print("\n[1] 无封面图，跳过")

    # ── 上传正文图片 ──────────────────────────────────────────
    print(f"\n[2] 上传正文图片...")
    img_url_map = {}  # {local_path: wx_url}

    # 从 content 提取所有用到的本地路径
    used_paths = set()
    for m in _IMG_RE.finditer(content):
        inner = m.group(1)
        if inner.startswith("/"):
            for p in inner.split("|"):
                if p.startswith("/"):
                    used_paths.add(p)

    for p in sorted(used_paths):
        if os.path.exists(p):
            try:
                wx_url = _upload_image_for_content(token, p)
                img_url_map[p] = wx_url
                time.sleep(0.3)  # 防频率限制
            except Exception as e:
                print(f"    ⚠️ {Path(p).name} 上传失败: {e}")

    print(f"  已上传 {len(img_url_map)} / {len(used_paths)} 张图片")

    # ── 渲染 HTML ─────────────────────────────────────────────
    print(f"\n[3] 渲染文章 HTML...")
    html_content = _render_html(content, img_url_map)
    print(f"  HTML 长度: {len(html_content)} 字符")

    # ── 创建草稿 ──────────────────────────────────────────────
    print(f"\n[4] 创建微信草稿...")
    # 微信草稿标题上限约 10 个汉字，超出截断
    if len(title) > 10:
        title = title[:9] + "…"
        print(f"  标题截断为: {title}")
    article = {
        "title": title,
        "content": html_content,
        "need_open_comment": 1,
        "only_fans_can_comment": 0,
    }
    if thumb_media_id:
        article["thumb_media_id"] = thumb_media_id

    url = f"{WX_API}/draft/add?access_token={token}"
    payload = {"articles": [article]}
    # ensure_ascii=False 保留中文，避免微信后台显示 \uXXXX 乱码
    resp = requests.post(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                         headers={"Content-Type": "application/json; charset=utf-8"},
                         timeout=30).json()

    if resp.get("errcode", 0) != 0:
        print(f"  ❌ 创建草稿失败: {resp}")
        return None

    media_id = resp.get("media_id", "")
    print(f"  ✅ 草稿创建成功！media_id = {media_id}")
    print(f"  👉 在微信公众平台草稿箱查看: https://mp.weixin.qq.com")
    return media_id


def delete_all_drafts():
    """清空草稿箱（用于清理测试产生的多余草稿）。"""
    conf = _load_conf()
    token = _get_access_token(conf)
    deleted = 0
    while True:
        r = requests.get(f"{WX_API}/draft/batchget?access_token={token}",
                         params={"offset": 0, "count": 20, "no_content": 1}, timeout=15).json()
        items = r.get("item", [])
        if not items:
            break
        for item in items:
            mid = item.get("media_id", "")
            if mid:
                dr = requests.post(f"{WX_API}/draft/delete?access_token={token}",
                                   data=json.dumps({"media_id": mid}),
                                   headers={"Content-Type": "application/json"}, timeout=10).json()
                print(f"  删除草稿 {mid[:16]}... → {dr}")
                deleted += 1
    print(f"共删除 {deleted} 篇草稿")


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="发布文章到微信公众号草稿箱")
    p.add_argument("--key", help="文章 key（DB 中的 key 字段）")
    p.add_argument("--publish", action="store_true", help="直接发布（默认只创建草稿）")
    p.add_argument("--delete-drafts", action="store_true", help="清空草稿箱")
    args = p.parse_args()

    os.chdir(Path(__file__).parent.parent)

    if args.delete_drafts:
        delete_all_drafts()
    elif args.key:
        publish_article(args.key, publish=args.publish)
    else:
        p.print_help()
