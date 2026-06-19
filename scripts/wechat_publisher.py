#!/usr/bin/env python3
"""
wechat_publisher.py — 将 story 文章发布为微信公众号草稿

流程：
  1. 获取 access_token
  2. 上传封面图 + 正文图片 → 微信 media_id / CDN URL
  3. 用 format_engine （85 主题）将内容渲染为微信内联 HTML
  4. 调用草稿接口创建草稿，返回 media_id

用法:
  python scripts/wechat_publisher.py --key <article_key>
  python scripts/wechat_publisher.py --key <article_key> --theme newspaper
  python scripts/wechat_publisher.py --key <article_key> --preview
  python scripts/wechat_publisher.py --list-themes
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

# ── format_engine（85 主题排版引擎）────────────────────────────
_SCRIPTS_DIR = Path(__file__).parent
sys.path.insert(0, str(_SCRIPTS_DIR))

from format_engine import (
    load_theme,
    format_for_output,
    xhs_img_to_markdown,
    list_available_themes,
    THEMES_DIR,
    inject_inline_styles,
    convert_lists_to_sections,
    extract_links_as_footnotes,
    fix_cjk_spacing,
    fix_cjk_bold_punctuation,
    convert_image_captions,
)

# 旧版主题名 → format_engine 主题名映射（保持向后兼容）
_THEME_ALIASES = {
    "news": "newspaper",
    "elegant": "magazine",
    "fresh": "sports",
    "minimal": "ink",
}

DEFAULT_THEME = "sports"  # 活力风格，适合娱乐/偶像内容


def _resolve_theme(theme_name: str) -> str:
    """解析主题名，支持别名和直接 ID。"""
    theme_name = _THEME_ALIASES.get(theme_name, theme_name)
    try:
        load_theme(theme_name)
        return theme_name
    except (SystemExit, Exception):
        # 尝试模糊匹配
        t = list_available_themes()
        for item in t:
            if item["id"] == theme_name:
                return theme_name
            if item["name"] == theme_name:
                return item["id"]
        # fallback 到默认
        print(f"  主题 '{theme_name}' 不存在，使用默认 '{DEFAULT_THEME}'", file=sys.stderr)
        return DEFAULT_THEME


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
    """上传图片到微信永久素材库（封面图），返回 media_id。"""
    url = f"{WX_API}/material/add_material?access_token={token}&type=image"
    with open(img_path, "rb") as f:
        ext = Path(img_path).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "gif": "image/gif"}.get(ext, "image/jpeg")
        resp = requests.post(url, files={"media": (Path(img_path).name, f, mime)}, timeout=30).json()

    if "media_id" not in resp:
        raise RuntimeError(f"上传封面图失败 {img_path}: {resp}")
    print(f"    ✅ 封面上传: {Path(img_path).name} → media_id={resp['media_id'][:12]}...")
    return resp["media_id"]


def _upload_image_for_content(token: str, img_path: str) -> str:
    """上传正文图片（新增素材接口），返回 URL。"""
    url = f"https://api.weixin.qq.com/cgi-bin/media/uploadimg?access_token={token}"
    with open(img_path, "rb") as f:
        ext = Path(img_path).suffix.lower().lstrip(".")
        mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png"}.get(ext, "image/jpeg")
        resp = requests.post(url, files={"media": (Path(img_path).name, f, mime)}, timeout=30).json()

    if "url" not in resp:
        raise RuntimeError(f"上传正文图片失败 {img_path}: {resp}")
    print(f"    🖼️ 正文图片: {Path(img_path).name} → {resp['url'][:50]}...")
    return resp["url"]


def _hex_tint(hex_color: str, alpha: float) -> str:
    """将 #RRGGBB 与白色预混合，返回实色 hex（微信不支持 rgba）。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c*2 for c in h)
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    r2 = round(255 + (r - 255) * alpha)
    g2 = round(255 + (g - 255) * alpha)
    b2 = round(255 + (b - 255) * alpha)
    return f"#{r2:02x}{g2:02x}{b2:02x}"


def _theme_card_html(theme_name: str) -> str:
    """生成主题选择卡片（HTML 注释），供预览页参考。"""
    try:
        theme = load_theme(theme_name)
    except Exception:
        return ""
    colors = theme.get("colors", {})
    accent = colors.get("accent", "#333")
    return (
        f'<section style="background:{_hex_tint(accent, 0.05)};'
        f'border-radius:8px;padding:8px 12px;margin-bottom:16px;'
        f'font-size:13px;color:#666;border-left:3px solid {accent}">'
        f'📐 主题：{theme.get("name", theme_name)}&nbsp;&nbsp;'
        f'<code style="font-size:11px;background:rgba(0,0,0,0.05);padding:1px 6px;border-radius:3px">'
        f'--theme {theme_name}</code></section>'
    )


# ── Markdown → 微信 HTML（format_engine 桥接）──────────────────

_IMG_RE = re.compile(r'【(?:图片|推文)\d+：([^】]*)】')


def _render_html_preview(content: str, title: str, theme_name: str = DEFAULT_THEME) -> str:
    """用本地图片路径渲染 HTML 预览页面（不上传微信，用于本地浏览器预览）。"""
    img_url_map = {}
    for m in _IMG_RE.finditer(content):
        inner = m.group(1)
        if inner.startswith("/"):
            for p in inner.split("|"):
                if p.startswith("/") and p not in img_url_map:
                    img_url_map[p] = f"file://{p}"

    # Prepend title as H1 so format_engine doesn't fall back to input_path stem
    content_with_title = f'# {title}\n\n{content}' if title and not content.lstrip().startswith('# ') else content
    body = _render_html(content_with_title, img_url_map, theme_name)
    theme = load_theme(theme_name) if _theme_exists(theme_name) else {}
    colors = theme.get("colors", {}) if theme else {}
    primary = colors.get("accent", "#333")
    theme_label = theme.get("name", theme_name) if theme else theme_name

    return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<style>
  body{{background:#f0f0f0;margin:0;padding:20px 16px;
    font-family:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif}}
  .phone-frame{{max-width:390px;margin:0 auto;background:#fff;border-radius:16px;
    overflow:hidden;box-shadow:0 8px 32px rgba(0,0,0,.15)}}
  .phone-header{{background:#f7f7f7;padding:12px 16px;display:flex;align-items:center;
    border-bottom:1px solid #e8e8e8}}
  .phone-header .dot{{width:8px;height:8px;border-radius:50%;margin-right:4px}}
  .phone-header .account{{font-size:13px;font-weight:600;color:#1a1a1a;margin-left:8px}}
  .article-body{{padding:20px 16px 80px}}
  .article-title{{font-size:20px;font-weight:700;color:#111;line-height:1.4;
    margin:0 0 8px}}
  .article-meta{{font-size:12px;color:#999;margin-bottom:20px;padding-bottom:16px;
    border-bottom:1px solid #f0f0f0}}
  img{{max-width:100%!important}}
  .theme-bar{{position:fixed;top:16px;right:16px;background:#fff;border-radius:10px;
    padding:10px 14px;box-shadow:0 4px 16px rgba(0,0,0,.12);font-size:13px;max-width:220px}}
  .theme-bar strong{{display:block;margin-bottom:6px;color:#333}}
  .theme-btn{{display:inline-block;padding:3px 10px;border-radius:12px;margin:2px;
    border:1px solid #ddd;cursor:pointer;font-size:12px;background:#f8f8f8;
    text-decoration:none;color:#444}}
  .theme-btn.active{{color:#fff;border-color:transparent}}
</style>
</head>
<body>
<div class="phone-frame">
  <div class="phone-header">
    <div class="dot" style="background:{primary}"></div>
    <div class="dot" style="background:#ccc"></div>
    <span class="account">日本娱乐报道</span>
    <span style="margin-left:auto;font-size:11px;color:#999">主题：{theme_label}</span>
  </div>
  <div class="article-body">
    <h1 class="article-title">{title}</h1>
    <div class="article-meta">📖 微信预览</div>
    {body}
  </div>
</div>
</body>
</html>"""


def _theme_exists(name: str) -> bool:
    """检查主题是否存在。"""
    return ((THEMES_DIR / f"{name}.json").exists()
            or name in _THEME_ALIASES)


def _render_html(content: str, img_url_map: dict,
                 theme_name: str = DEFAULT_THEME) -> str:
    """用 format_engine 渲染 Markdown → 微信内联 HTML。

    流程：
      1. 【图片N：/path】→ Markdown ![](url)
      2. 送入 format_for_output → 微信兼容内联 HTML
    """
    # 图片标记转换
    md_content = xhs_img_to_markdown(content, img_url_map)

    # 用 format_engine 渲染
    # 构造一个临时文件路径，让引擎能提取标题
    resolved = _resolve_theme(theme_name)
    theme = load_theme(resolved)

    result = format_for_output(
        md_content,
        input_path=Path("/tmp/wechat_article.md"),  # 占位路径
        theme=theme,
        output_dir=Path("/tmp/wechat-format"),
        vault_root=Path.home(),
        output_format="wechat",
    )

    html = result["html"]
    footnote_html = result.get("footnote_html", "")

    if footnote_html:
        html += "\n" + footnote_html

    return html


def publish_article(article_key: str, publish: bool = False,
                    theme_name: str = DEFAULT_THEME):
    """完整流程：读取文章 → 上传图片 → 用 format_engine 渲染 → 创建草稿。"""
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

    resolved_theme = _resolve_theme(theme_name)
    theme_data = load_theme(resolved_theme)
    print(f"文章: {title}")
    print(f"主题: {theme_data.get('name', resolved_theme)} ({resolved_theme})")
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

    # ── format_engine 渲染 ────────────────────────────────────
    print(f"\n[3] 渲染文章 HTML（format_engine · {theme_data.get('name', resolved_theme)}）...")
    html_content = _render_html(content, img_url_map, resolved_theme)
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
    p = argparse.ArgumentParser(description="发布文章到微信公众号草稿箱（format_engine · 85 主题）")
    p.add_argument("--key", help="文章 key（DB 中的 key 字段）")
    p.add_argument("--theme", default=DEFAULT_THEME,
                   help=f"排版主题 ID（默认 {DEFAULT_THEME}）。可用: --list-themes")
    p.add_argument("--preview", action="store_true", help="本地 HTML 预览（不发布，用浏览器打开）")
    p.add_argument("--publish", action="store_true", help="直接发布（默认只创建草稿）")
    p.add_argument("--delete-drafts", action="store_true", help="清空草稿箱")
    p.add_argument("--list-themes", action="store_true", help="列出所有可用主题")
    args = p.parse_args()

    os.chdir(Path(__file__).parent.parent)

    if args.list_themes:
        themes = list_available_themes()
        print(f"可用主题（共 {len(themes)} 个）：")
        for t in themes:
            print(f"  {t['id']:25s} {t['name']}")
        print(f"\n默认主题: {DEFAULT_THEME}")
        print("旧版别名兼容: news→newspaper, elegant→magazine, fresh→sports, minimal→ink")
    elif args.delete_drafts:
        delete_all_drafts()
    elif args.key and args.preview:
        import tempfile, webbrowser
        os.environ.setdefault("SQLITE_PATH", "data/news_dev.db")
        from scripts.sqlite_db import _connect
        with _connect() as db:
            r = db.execute("SELECT title, content FROM news WHERE key=?", (args.key,)).fetchone()
        if not r:
            print(f"文章 {args.key} 不存在"); sys.exit(1)
        html = _render_html_preview(r["content"], r["title"], args.theme)
        tmp = tempfile.NamedTemporaryFile(suffix=".html", delete=False, mode="w", encoding="utf-8")
        tmp.write(html); tmp.close()
        resolved = _resolve_theme(args.theme)
        theme_data = load_theme(resolved)
        print(f"预览文件: {tmp.name}  主题: {theme_data.get('name', resolved)} ({resolved})")
        webbrowser.open(f"file://{tmp.name}")
        print(f"✅ 已在浏览器打开，确认后运行：")
        print(f"   python scripts/wechat_publisher.py --key {args.key} --theme {resolved}")
    elif args.key:
        publish_article(args.key, publish=args.publish, theme_name=args.theme)
    else:
        p.print_help()
