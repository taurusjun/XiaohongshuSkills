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

# ── 主题定义（内联样式，WeChat 会剥离 <style> 块）─────────────
# 每个主题包含：primary 色、各元素内联 style 字符串
THEMES: dict[str, dict] = {
    "news": {
        "name": "新闻蓝",
        "primary": "#0F4C81",
        "desc": "经典新闻配色，权威感强",
        "h2": ("font-size:18px;font-weight:700;color:#fff;background:#0F4C81;"
               "padding:6px 16px;border-radius:4px;margin:28px 0 14px;display:inline-block"),
        "h3": ("font-size:16px;font-weight:700;color:#0F4C81;"
               "padding-left:10px;border-left:4px solid #0F4C81;margin:20px 0 10px"),
        "p":  "font-size:16px;line-height:1.9;color:#1a1a1a;margin:0 0 16px;letter-spacing:0.05em",
        "blockquote": ("font-size:15px;line-height:1.8;color:#555;"
                       "background:rgba(15,76,129,0.06);border-left:4px solid #0F4C81;"
                       "padding:12px 16px;border-radius:0 6px 6px 0;margin:16px 0"),
        "img": "max-width:100%;border-radius:6px;display:block;margin:14px auto",
        "caption": "font-size:12px;color:#999;text-align:center;margin:-10px 0 16px",
        "hr": "border:none;border-top:2px solid rgba(15,76,129,0.2);margin:24px 0",
    },
    "elegant": {
        "name": "优雅紫",
        "primary": "#92617E",
        "desc": "优雅文艺，适合深度报道",
        "h2": ("font-size:18px;font-weight:700;color:#fff;background:#92617E;"
               "padding:8px 20px;border-radius:8px;margin:28px 0 14px;"
               "box-shadow:0 4px 10px rgba(146,97,126,0.3);display:inline-block"),
        "h3": ("font-size:16px;font-weight:700;color:#92617E;"
               "padding-left:10px;border-left:4px solid #92617E;"
               "border-bottom:1px dashed rgba(146,97,126,0.3);margin:20px 0 10px;padding-bottom:4px"),
        "p":  "font-size:16px;line-height:1.95;color:#2d2d2d;margin:0 0 16px;letter-spacing:0.05em",
        "blockquote": ("font-style:italic;font-size:15px;line-height:1.8;color:#666;"
                       "border-left:4px solid #92617E;padding:12px 16px;"
                       "box-shadow:0 4px 12px rgba(0,0,0,0.06);border-radius:0 8px 8px 0;margin:16px 0"),
        "img": ("max-width:100%;border-radius:10px;display:block;margin:14px auto;"
                "box-shadow:0 4px 12px rgba(0,0,0,0.12)"),
        "caption": "font-size:12px;color:#aaa;text-align:center;margin:-10px 0 16px;font-style:italic",
        "hr": ("border:none;height:1px;margin:28px 0;"
               "background:linear-gradient(to right,rgba(0,0,0,0),rgba(146,97,126,0.4),rgba(0,0,0,0))"),
    },
    "fresh": {
        "name": "活力橘",
        "primary": "#FA5151",
        "desc": "活力感强，适合娱乐/偶像内容",
        "h2": ("font-size:18px;font-weight:700;color:#FA5151;"
               "padding:6px 0 6px 14px;border-left:5px solid #FA5151;"
               "background:linear-gradient(to right,rgba(250,81,81,0.08),transparent);"
               "margin:28px 0 14px;border-radius:0 6px 6px 0"),
        "h3": ("font-size:16px;font-weight:700;color:#333;"
               "padding-left:10px;border-left:3px solid #FA5151;margin:20px 0 10px"),
        "p":  "font-size:16px;line-height:1.9;color:#222;margin:0 0 16px;letter-spacing:0.04em",
        "blockquote": ("font-size:15px;line-height:1.8;color:#555;"
                       "background:#fff8f8;border-left:4px solid #FA5151;"
                       "padding:12px 16px;border-radius:0 8px 8px 0;margin:16px 0"),
        "img": ("max-width:100%;border-radius:10px;display:block;margin:14px auto;"
                "box-shadow:0 2px 10px rgba(0,0,0,0.08)"),
        "caption": "font-size:12px;color:#aaa;text-align:center;margin:-10px 0 16px",
        "hr": ("border:none;height:2px;margin:24px 0;"
               "background:linear-gradient(to right,#FA5151,#FFB347,rgba(0,0,0,0))"),
    },
    "minimal": {
        "name": "简洁黑",
        "primary": "#333333",
        "desc": "极简长文，严肃媒体风格",
        "h2": ("font-size:19px;font-weight:700;color:#111;"
               "padding-bottom:8px;border-bottom:2px solid #333;margin:32px 0 16px"),
        "h3": ("font-size:17px;font-weight:700;color:#333;"
               "margin:24px 0 12px"),
        "p":  ("font-size:16px;line-height:2.0;color:#222;margin:0 0 18px;letter-spacing:0.06em;"
               "font-family:Georgia,'Songti SC','Noto Serif SC',serif"),
        "blockquote": ("font-size:15px;line-height:1.8;color:#666;"
                       "border-left:3px solid rgba(0,0,0,0.3);padding:10px 16px;"
                       "background:rgba(0,0,0,0.03);margin:16px 0"),
        "img": "max-width:100%;display:block;margin:16px auto",
        "caption": "font-size:12px;color:#999;text-align:center;margin:-12px 0 18px",
        "hr": "border:none;border-top:1px solid rgba(0,0,0,0.15);margin:28px 0",
    },
}

# 默认主题
DEFAULT_THEME = "fresh"  # 活力橘，适合偶像内容


def _get_styles(theme_name: str) -> dict:
    return THEMES.get(theme_name, THEMES[DEFAULT_THEME])


def _theme_buttons(active: str) -> str:
    parts = []
    for k, v in THEMES.items():
        bg = f'background:{v["primary"]};color:#fff;border-color:transparent' if k == active else ""
        parts.append(f'<span class="theme-btn" style="{bg}">{v["name"]}</span>')
    return " ".join(parts)


def _render_html_preview(content: str, title: str, theme_name: str = DEFAULT_THEME) -> str:
    """用本地图片路径渲染 HTML 预览页面（不上传微信，用于本地浏览器预览）。"""
    img_url_map = {}
    for m in _IMG_RE.finditer(content):
        inner = m.group(1)
        if inner.startswith("/"):
            for p in inner.split("|"):
                if p.startswith("/") and p not in img_url_map:
                    img_url_map[p] = f"file://{p}"

    body = _render_html(content, img_url_map, theme_name)
    theme = _get_styles(theme_name)
    primary = theme["primary"]
    theme_label = theme["name"]

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
  /* Theme switcher bar */
  .theme-bar{{position:fixed;top:16px;right:16px;background:#fff;border-radius:10px;
    padding:10px 14px;box-shadow:0 4px 16px rgba(0,0,0,.12);font-size:13px}}
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
    <div class="article-meta">📖 阅读约需 5 分钟 &nbsp;·&nbsp; 微信预览</div>
    {body}
  </div>
</div>
<div class="theme-bar">
  <strong>切换主题预览</strong>
  {_theme_buttons(theme_name)}
  <div style="margin-top:8px;font-size:11px;color:#999">
    确认后运行 --key 发布<br>当前：<code>--theme {theme_name}</code>
  </div>
</div>
</body>
</html>"""


_BOLD_RE = re.compile(r'\*\*(.+?)\*\*')
_SECTION_BREAK_RE = re.compile(r'^(##|【(?:图片|推文)\d+)', re.MULTILINE)


def _split_intro_body_outro(content: str) -> tuple[str, str, str]:
    """将正文拆分为 导语（第一段）/ 主体 / 结语（最后一段）。
    按双换行分段，首段为导语，尾段为结语，中间为主体。
    首/尾段如果以 ## 或 【图片 开头则不作为导语/结语。
    """
    # 按空行分段
    paras = [p.strip() for p in re.split(r'\n{2,}', content) if p.strip()]
    if not paras:
        return "", content.strip(), ""

    def _is_body_para(s: str) -> bool:
        return s.startswith("##") or bool(_IMG_RE.match(s))

    # 导语：第一段（非标题/图片才算导语）
    if len(paras) >= 1 and not _is_body_para(paras[0]):
        intro = paras[0]
        rest = paras[1:]
    else:
        intro = ""
        rest = paras

    # 结语：最后一段（非标题/图片才算结语）
    if len(rest) >= 2 and not _is_body_para(rest[-1]):
        outro = rest[-1]
        body_paras = rest[:-1]
    else:
        outro = ""
        body_paras = rest

    body = "\n\n".join(body_paras)
    return intro, body, outro


def _render_inline(text: str, S: dict) -> str:
    """处理行内 Markdown: **bold** → colored strong, 其余原样。"""
    return _BOLD_RE.sub(
        lambda m: f'<strong style="color:{S.get("primary","#333")};font-weight:700">{m.group(1)}</strong>',
        text
    )


def _render_html(content: str, img_url_map: dict,
                 theme_name: str = DEFAULT_THEME) -> str:
    """将文章内容渲染为微信内联样式 HTML（含导语卡片、结语区、pull quote）。"""
    S = _get_styles(theme_name)
    primary = S.get("primary", "#333")
    intro, body, outro = _split_intro_body_outro(content)

    parts = []

    # ── 导语卡片（无"导语"标签，用视觉区分）─────────────────────
    if intro:
        intro_lines = [l.strip() for l in intro.split("\n") if l.strip()]
        intro_html = "".join(
            f'<p style="margin:0 0 8px;font-size:16px;line-height:1.9;color:#1a1a1a">'
            f'{_render_inline(l, S)}</p>'
            for l in intro_lines
        )
        parts.append(
            f'<section style="background:{_hex_tint(primary,0.08)};'
            f'border-left:4px solid {primary};'
            f'border-radius:0 10px 10px 0;'
            f'padding:16px 18px;margin:0 0 24px">'
            f'{intro_html}'
            f'</section>'
        )

    # ── 正文 ──────────────────────────────────────────────────────
    last = 0

    def _flush_text(text: str, is_outro: bool = False):
        for line in text.split("\n"):
            s = line.strip()
            if not s:
                continue
            if s.startswith("### "):
                parts.append(f'<h3 style="{S["h3"]}">{_render_inline(s[4:], S)}</h3>')
            elif s.startswith("## "):
                # h2 前加分隔空间
                parts.append(f'<div style="height:8px"></div>')
                parts.append(f'<h2 style="{S["h2"]}">{_render_inline(s[3:], S)}</h2>')
            elif s.startswith("> "):
                q = s[2:].strip()
                parts.append(
                    f'<p style="font-size:15px;line-height:1.8;color:#aaa;'
                    f'font-style:italic;padding:16px 0 4px;margin:0">'
                    f'{_render_inline(q, S)}</p>'
                )
            elif s.startswith(">>") and s.endswith("<<"):
                # 显式 pull quote: >>金句<<
                q = s[2:-2].strip()
                parts.append(
                    f'<blockquote style="{S.get("blockquote", S["p"])};'
                    f'font-size:18px;font-weight:600;color:{primary};'
                    f'text-align:center;padding:20px 16px;margin:20px 0">'
                    f'{q}</blockquote>'
                )
            else:
                p_style = S["p"] if not is_outro else (
                    S["p"] + f";color:#666;font-style:italic"
                )
                parts.append(f'<p style="{p_style}">{_render_inline(s, S)}</p>')

    for m in _IMG_RE.finditer(body):
        _flush_text(body[last:m.start()])
        inner = m.group(1)
        if inner.startswith("/"):
            paths = [p for p in inner.split("|") if p.startswith("/")]
            if len(paths) > 1:
                w = 96 // len(paths)
                cells = "".join(
                    f'<img src="{img_url_map.get(p,"")}" style="width:{w}%;border-radius:6px;margin:2px">'
                    for p in paths if img_url_map.get(p)
                )
                if cells:
                    parts.append(f'<div style="display:flex;gap:4px;margin:16px 0">{cells}</div>')
            elif paths:
                url = img_url_map.get(paths[0], "")
                if url:
                    cap = _IMG_RE.sub("", m.group(0)).strip()
                    parts.append(f'<img src="{url}" style="{S["img"]}">')
                    if cap and not cap.startswith("/"):
                        parts.append(f'<p style="{S["caption"]}">{cap}</p>')
        last = m.end()

    _flush_text(body[last:])

    # ── 结语区（装饰分隔线 + 浅底色，无"结语"标签）──────────────
    if outro:
        hr = S.get("hr", f'border:none;border-top:1px solid {primary};opacity:.3;margin:28px 0 20px')
        outro_lines = [l.strip() for l in outro.split("\n") if l.strip()]
        outro_html = "".join(
            f'<p style="margin:0 0 8px;font-size:15px;line-height:1.9;'
            f'color:#555;font-style:italic">{_render_inline(l, S)}</p>'
            for l in outro_lines
        )
        parts.append(
            f'<hr style="{hr}">'
            f'<section style="background:{_hex_tint(primary,0.04)};'
            f'border-radius:8px;padding:16px 18px;margin-top:8px">'
            f'{outro_html}'
            f'</section>'
        )

    body_html = "\n".join(parts)
    return (f'<section style="max-width:680px;margin:0 auto;'
            f'font-family:-apple-system,&quot;PingFang SC&quot;,&quot;Microsoft YaHei&quot;,sans-serif">'
            f'\n{body_html}\n</section>')


def _hex_tint(hex_color: str, alpha: float) -> str:
    """将 #RRGGBB 与白色预混合，返回实色 hex（微信不支持 rgba）。"""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c*2 for c in h)
    r, g, b = int(h[0:2],16), int(h[2:4],16), int(h[4:6],16)
    r2 = round(255 + (r - 255) * alpha)
    g2 = round(255 + (g - 255) * alpha)
    b2 = round(255 + (b - 255) * alpha)
    return f"#{r2:02x}{g2:02x}{b2:02x}"


def publish_article(article_key: str, publish: bool = False,
                    theme_name: str = DEFAULT_THEME):
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
    theme = _get_styles(theme_name)
    print(f"  主题: {theme['name']} ({theme['desc']})")
    html_content = _render_html(content, img_url_map, theme_name)
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
    _theme_help = " / ".join(f"{k}={v['name']}" for k, v in THEMES.items())
    p.add_argument("--theme", default=DEFAULT_THEME,
                   choices=list(THEMES.keys()),
                   help=f"排版主题 ({_theme_help})，默认 {DEFAULT_THEME}")
    p.add_argument("--preview", action="store_true", help="本地 HTML 预览（不发布，用浏览器打开）")
    p.add_argument("--publish", action="store_true", help="直接发布（默认只创建草稿）")
    p.add_argument("--delete-drafts", action="store_true", help="清空草稿箱")
    p.add_argument("--list-themes", action="store_true", help="列出所有主题")
    args = p.parse_args()

    os.chdir(Path(__file__).parent.parent)

    if args.list_themes:
        print("可用主题：")
        for k, v in THEMES.items():
            mark = " ← 默认" if k == DEFAULT_THEME else ""
            print(f"  {k:10} {v['name']}  — {v['desc']}{mark}")
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
        print(f"预览文件: {tmp.name}  主题: {THEMES[args.theme]['name']}")
        webbrowser.open(f"file://{tmp.name}")
        print(f"✅ 已在浏览器打开，确认后运行：")
        print(f"   python scripts/wechat_publisher.py --key {args.key} --theme {args.theme}")
    elif args.key:
        publish_article(args.key, publish=args.publish, theme_name=args.theme)
    else:
        p.print_help()
