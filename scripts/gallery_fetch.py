#!/usr/bin/env python3
"""
从 Notion 日本新闻条目中，检测 Yahoo 文章包含的图集链接，
下载图片到本地缓存 ~/.cache/xhs_images/<key>/

支持站点：mdpr.jp / modelpress.net / nikkansports.com / chunichi.co.jp /
          hochi.news / sponichi.co.jp / oricon.co.jp / natalie.mu /
          billboard-japan.com / crank-in.net / limo.media / mezamashi.media /
          smart-flash.jp / mantan-web.jp / inside-games.jp

用法:
    python scripts/gallery_fetch.py              # 扫描最近 20 条
    python scripts/gallery_fetch.py --limit 5
    python scripts/gallery_fetch.py --notion-id <page_id>
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from bs4 import BeautifulSoup

import sys as _sys
_scripts_dir = os.path.dirname(os.path.abspath(__file__))
if _scripts_dir not in _sys.path:
    _sys.path.insert(0, _scripts_dir)
from unified_media_downloader import download_youtube, download_instagram, download_twitter

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
except ImportError:
    pass

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

CACHE_DIR = Path.home() / ".cache" / "xhs_images"
MAX_IMAGES = 20
BURN_SUBTITLES = True  # 可通过 --no-subtitles 关闭
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}
CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))

# 已知图集站点及对应图片容器 CSS selector（空串表示用通用逻辑）
GALLERY_SITES: dict[str, str] = {
    "mdpr.jp":              ".photo-detail, .slide, article",
    "modelpress.net":       ".photo, article",
    "nikkansports.com":     ".article-photo, .photo-slider, article",
    "chunichi.co.jp":       ".article-img, .photo, article",
    "hochi.news":           ".article-image, article",
    "sponichi.co.jp":       ".photo-area, article",
    "oricon.co.jp":         "div.main_photo",
    "natalie.mu":           ".chronicle-article-photo, article",
    "billboard-japan.com":  ".article-photo, article",
    "crank-in.net":         ".photo-link-img",
    "limo.media":           "article, .article-body",
    "mezamashi.media":      "article, .gallery-body",
    "smart-flash.jp":       ".imageArea, article",
    "mantan-web.jp":        ".photo-area, article",
    "inside-games.jp":      "article, body",
    "bookbang.jp":          "article",
    "thetv.jp":             ".newsimage",
    "efight.jp":            ".attachment img, article img",
    "maidonanews.jp":       ".photo, article",
    "encount.press":        ".article-image, article",
    "nishispo.nishinippon.co.jp": "article, .contents",
    "thefirsttimes.jp":          "article, .m-gallery-list",
    "kstyle.com":           "article, body",
    "yorozoonews.jp":       "article, .article-body",
    "nikkan-spa.jp":        "article, .post-content",
    "animeanime.jp":        "article, body",
    "mainichikirei.jp":     "article, .article-body",
    "deview.co.jp":         "article, #main_image",
    "qjweb.jp":             "article, .gallery-main",
    "pinzuba.news":         "article, main",
    "friday.kodansha.co.jp": "article, main",
    "shueisha.online":       ".article-photo",
    "entamenext.com":        ".articleGalleryImg",
    "musicvoice.jp":         "article, .entry-content",
    "daily.co.jp":           "article.detailContent",
    "vivi.tv":               "article, .gallery",
    "times.abema.tv":        ".article-body",
    "realsound.jp":          "figure img, .post-content img",
    "lasisa.net":            "main img, .entry-content img",
    "jisin.jp":              ".post-content img, .slider-show img",
    "lp.p.pia.jp":           ".photoGallaryArea__largeImage, img[data-src]",
    "news-postseven.com":    ".c-PhotoImage img, article img",
}

# 这些站点的链接即使不含图集关键词也应被识别（如 /article/XXXXXX 形式）
GALLERY_NO_HINT_SITES = {"limo.media", "mezamashi.media", "smart-flash.jp",
                         "chunichi.co.jp", "mantan-web.jp", "inside-games.jp",
                         "efight.jp", "thetv.jp", "maidonanews.jp", "encount.press",
                         "nishispo.nishinippon.co.jp", "thefirsttimes.jp", "kstyle.com",
                         "realsound.jp", "lasisa.net",
                         "yorozoonews.jp", "nikkan-spa.jp", "animeanime.jp",
                         "mainichikirei.jp", "deview.co.jp", "qjweb.jp", "pinzuba.news",
                         "friday.kodansha.co.jp", "shueisha.online", "entamenext.com",
                         "musicvoice.jp", "daily.co.jp", "vivi.tv", "times.abema.tv"}

# URL に含まれる「図集っぽい」キーワード（なければ外部リンク全体を対象）
GALLERY_URL_HINTS = ["photo", "picture", "gallery", "image", "img", "pic", "slide", "gazo"]


# ============ Notion ============

def get_notion_pages(limit: int = 20) -> list:
    """获取勾选了「图集下载」且发布时间为今天的条目"""
    from datetime import date
    today_str = date.today().strftime("%Y.%m.%d")
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {
                "and": [
                    {"property": "图集下载", "checkbox": {"equals": True}},
                    {"property": "发布时间", "rich_text": {"equals": today_str}},
                ]
            },
            "page_size": limit,
            "sorts": [{"property": "发布时间", "direction": "descending"}],
        },
        timeout=10,
    )
    if resp.status_code == 200:
        return resp.json().get("results", [])
    print(f"❌ Notion 查询失败: {resp.status_code}")
    return []


def get_notion_page(page_id: str) -> dict:
    resp = requests.get(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        timeout=10,
    )
    return resp.json() if resp.status_code == 200 else {}


def parse_page_meta(page: dict) -> dict:
    props = page.get("properties", {})

    def get_title():
        return "".join(r.get("plain_text", "") for r in props.get("Name", {}).get("title", []))

    def get_text(name):
        return "".join(r.get("plain_text", "") for r in props.get(name, {}).get("rich_text", []))

    return {
        "id": page["id"].replace("-", ""),
        "title": get_title(),
        "key": get_text("key"),
        "link": props.get("原文链接", {}).get("url", ""),
        "gallery_url": props.get("图集链接", {}).get("url", ""),
    }


# ============ Gallery Detection ============

def _domain_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.lstrip("www.")


def _is_valid_gallery_url(url: str) -> bool:
    """URL 必须有实际路径（排除首页、纯域名链接）"""
    from urllib.parse import urlparse
    p = urlparse(url)
    path = p.path.rstrip("/")
    parts = [p for p in path.split("/") if p]
    # kstyle.com 通过 query param 标识文章（如 ?articleNo=2278474），路径只有 /article.ksn
    if len(parts) >= 2:
        return True
    if "kstyle.com" in p.netloc and "articleNo" in p.query:
        return True
    if "nikkan-spa.jp" in p.netloc and "attachment_id" in p.query:
        return True
    if "deview.co.jp" in p.netloc and "am_article_id" in p.query:
        return True
    return False


def is_gallery_url(gallery_url: str) -> bool:
    """判断URL是否为有效的图集URL（用于验证）"""
    if not gallery_url:
        return False
    domain = _domain_of(gallery_url)
    # 检查是否在支持的站点列表中
    if not any(site in domain for site in GALLERY_SITES):
        return False
    # 检查URL路径是否有效
    return _is_valid_gallery_url(gallery_url)


def _extract_instagram_shortcode(html_text: str) -> str:
    """从页面 HTML 提取 Instagram 帖子 shortcode（支持 /p/ 和 /reel/）"""
    import re
    m = re.search(r'instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)/', html_text)
    if m:
        return m.group(1)
    return ""


def detect_gallery_link(article_url: str) -> str:
    """从 Yahoo 文章页找已知图集站点的外链，URL 必须含图集关键词且有实际路径"""
    if not article_url:
        return ""
    try:
        import time as _time
        for attempt in range(3):
            try:
                resp = requests.get(article_url, headers=HEADERS, timeout=15)
                break
            except Exception:
                if attempt < 2:
                    _time.sleep(2)
                else:
                    raise
        soup = BeautifulSoup(resp.text, "html.parser")

        # Instagram embed（blockquote / iframe）— 优先检测
        shortcode = _extract_instagram_shortcode(resp.text)
        if shortcode:
            ig_url = f"https://www.instagram.com/p/{shortcode}/"
            print(f"  🔗 找到 Instagram 嵌入: {ig_url}")
            return ig_url

        # YouTube embed — 次优先
        yt_id = _extract_youtube_video_id(resp.text)
        if yt_id:
            yt_url = f"https://www.youtube.com/watch?v={yt_id}"
            print(f"  🎬 找到 YouTube 嵌入: {yt_url}")
            return yt_url

        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            domain = _domain_of(href)
            matched = next((k for k in GALLERY_SITES if k in domain), None)
            if not matched:
                continue
            # 必须有实际路径深度
            if not _is_valid_gallery_url(href):
                continue
            # 排除导航/广告/功能链接（只排除顶级路径，不误杀文章子路径）
            from urllib.parse import urlparse
            path = urlparse(href).path.lower()
            # 只排除路径深度 ≤ 2 的纯导航页（如 /news/、/feature/）
            # 路径有 3 层以上（如 /news/0000802151/attachment/）属于文章子页，保留
            path_parts = [p for p in path.split("/") if p]
            exclude_top = ["/feature", "/movie", "/video", "/special",
                           "/tieup", "/campaign", "/program", "/column", "/interview"]
            if len(path_parts) <= 1 and any(path.startswith(ex) for ex in exclude_top + ["/news"]):
                continue
            # 无需关键词的站点直接返回
            if any(site in domain for site in GALLERY_NO_HINT_SITES):
                print(f"  🔗 找到图集外链 ({domain}): {href[:70]}")
                return href
            # 其他站点需要含图集关键词
            href_lower = href.lower()
            if any(hint in href_lower for hint in GALLERY_URL_HINTS):
                print(f"  🔗 找到图集外链 ({domain}): {href[:70]}")
                return href
    except Exception as e:
        print(f"  ⚠️ 检测图集链接失败: {e}")
    return ""


def _scrape_oricon(gallery_url: str) -> list[str]:
    """oricon.co.jp 分页图集：通过「次の写真」链接逐页遍历，抓 #main_photo img"""
    import re
    from urllib.parse import urljoin
    headers = {**HEADERS, "Referer": "https://www.oricon.co.jp/"}

    images: list[str] = []
    seen: set[str] = set()
    url = gallery_url

    for _ in range(MAX_IMAGES):
        try:
            r = requests.get(url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")

            img = s.select_one("#main_photo img")
            if img:
                src = img.get("src", "")
                if src:
                    if src.startswith("/"):
                        src = urljoin("https://www.oricon.co.jp", src)
                    if src not in seen:
                        seen.add(src)
                        images.append(src)

            # 「次の写真」リンクから次ページへ
            next_a = None
            for a in s.select("a[href*='/photo/']"):
                if "次の写真" in a.get_text():
                    next_a = a
                    break
            if not next_a:
                break
            next_url = urljoin("https://www.oricon.co.jp", next_a.get("href", ""))
            if next_url == url:
                break
            url = next_url
            time.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️ oricon 抓取失败: {e}")
            break

    return images


def _scrape_crank_in(gallery_url: str) -> list[str]:
    """crank-in.net 每张图独立分页，遍历所有页抓主图。"""
    import re
    from urllib.parse import urlparse, urlunparse
    headers = {**HEADERS, "Referer": "https://www.crank-in.net/"}

    # 先去掉 query string，再截掉末尾页码
    p = urlparse(gallery_url)
    clean_url = urlunparse(p._replace(query="", fragment="")).rstrip("/")
    base = re.sub(r'/\d+$', '', clean_url)

    # 先取第一页获取总页数
    resp = requests.get(f"{base}/1", headers=headers, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    num_el = soup.select_one(".photo-link-num")
    total = 1
    if num_el:
        m = re.search(r'/(\d+)', num_el.get_text())
        if m:
            total = int(m.group(1))

    total = min(total, MAX_IMAGES)
    images = []

    for page in range(1, total + 1):
        try:
            if page != 1:
                r = requests.get(f"{base}/{page}", headers=headers, timeout=15)
                s = BeautifulSoup(r.text, "html.parser")
            else:
                s = soup

            img = s.select_one(".photo-link-img img")
            if img:
                src = img.get("src", "")
                if src and not src.startswith("data:"):
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        pp = urlparse(gallery_url)
                        src = f"{pp.scheme}://{pp.netloc}{src}"
                    src = re.sub(r'_\d+\.jpg', '_1200.jpg', src)
                    images.append(src)
        except Exception as e:
            print(f"  ⚠️ crank-in page {page} 失败: {e}")
        time.sleep(0.3)

    return images


def _scrape_limo(gallery_url: str) -> list[str]:
    """limo.media 图集：遍历分页抓取正文中的图片"""
    import re
    from urllib.parse import urlparse

    # 清理 URL，提取基础路径
    base_url = re.sub(r'\?page=\d+', '', gallery_url)

    headers = {**HEADERS, "Referer": "https://limo.media/"}
    images = []

    try:
        # 先请求第一页，获取分页数量
        r = requests.get(base_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")

        # 找分页链接
        page_nums = set()
        for a in s.find_all("a", href=True):
            m = re.search(r'page=(\d+)', a["href"])
            if m:
                page_nums.add(int(m.group(1)))
        max_page = max(page_nums) if page_nums else 1
        max_page = min(max_page, 5)  # 最多 5 页

        # 遍历所有分页
        for page in range(1, max_page + 1):
            if page == 1:
                page_url = base_url
            else:
                page_url = f"{base_url}?page={page}"

            r = requests.get(page_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")

            # 找大图（870wm 尺寸）
            for img in s.find_all("img"):
                src = img.get("data-src") or img.get("src", "")
                if "ismcdn.jp" in src and "/mwimgs/" in src and any(ext in src.lower() for ext in [".jpg", ".jpeg"]):
                    # 只取大图（宽度 >= 500）
                    m = re.search(r'/(\d+)m?/img_', src)
                    if m and int(m.group(1)) < 500:
                        continue
                    # 转大图：保留子目录，只替换尺寸部分
                    # /mwimgs/3/d/870m/ -> /mwimgs/3/d/1200w/
                    src = re.sub(r'/mwimgs/(\w+)/(\w+)/\d+\w*/', r'/mwimgs/\1/\2/1200w/', src)
                    if src not in images:
                        images.append(src)
                    if len(images) >= MAX_IMAGES:
                        return images
            time.sleep(0.3)
    except Exception as e:
        print(f"  ⚠️ limo 抓取失败: {e}")

    return images


def _scrape_mezamashi(gallery_url: str) -> list[str]:
    """mezamashi.media 图集：从 gallery 页面提取所有图片"""
    import re
    from urllib.parse import urlparse, urlunparse

    # 去掉 query 参数
    p = urlparse(gallery_url)
    clean_url = urlunparse(p._replace(query="", fragment=""))

    headers = {**HEADERS, "Referer": "https://mezamashi.media/"}
    images = []

    try:
        r = requests.get(clean_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")

        # 找所有 data-src 包含 ismcdn.jp/img 的图片
        for img in s.find_all("img"):
            src = img.get("data-src") or img.get("src", "")
            if "ismcdn.jp" in src and "img_" in src:
                # 转大图：保留子目录，只替换尺寸部分
                # /mwimgs/8/0/708/ -> /mwimgs/8/0/1200w/
                src = re.sub(r'/mwimgs/(\w+)/(\w+)/\d+\w*/', r'/mwimgs/\1/\2/1200w/', src)
                if src not in images:
                    images.append(src)
            if len(images) >= MAX_IMAGES:
                break
    except Exception as e:
        print(f"  ⚠️ mezamashi 抓取失败: {e}")

    return images


def _scrape_smart_flash(gallery_url: str) -> list[str]:
    """smart-flash.jp 图集：限定 .newsBlock，分页是 JS 驱动，所有图已在 HTML 中"""
    import re
    from urllib.parse import urljoin

    headers = {**HEADERS, "Referer": "https://smart-flash.jp/"}
    images: list[str] = []
    seen: set[str] = set()

    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")
        body = s.select_one(".newsBlock") or s

        for img in body.select("img"):
            src = img.get("data-src") or img.get("src", "")
            if not src or "data.smart-flash.jp" not in src:
                continue
            if src.startswith("/"):
                src = urljoin("https://smart-flash.jp", src)
            # 去缩略图尺寸后缀
            src = re.sub(r'-\d+x\d+(\.\w+)$', r'\1', src)
            if src not in seen:
                seen.add(src)
                images.append(src)
            if len(images) >= MAX_IMAGES:
                break
    except Exception as e:
        print(f"  ⚠️ smart-flash 抓取失败: {e}")

    return images


def _mantan_to_jpeg(src: str) -> str:
    """将 mantan CDN URL 的参数替换为 w=1200,f=jpg，获取高质量 JPEG 大图。
    输入: https://storage.mantan-web.jp/w=977,h=1466,f=webp:auto/images/...
    输出: https://storage.mantan-web.jp/w=1200,f=jpg/images/...
    """
    import re
    return re.sub(
        r'storage\.mantan-web\.jp/[^/]+/images/',
        'storage.mantan-web.jp/w=1200,f=jpg/images/',
        src,
    )


def _scrape_mantan(gallery_url: str) -> list[str]:
    """mantan-web.jp 图集：支持两种页面结构。

    旧版：photo__photolist-item 导航列表一次性获取全部图片（_size 路径）。
    新版：每页仅含一张当前图，通过遍历 photopage/001..999 页面抓取。
    URL 格式：storage.mantan-web.jp/w=W,f=F/images/... 或 storage.mantan-web.jp/.../_size...
    统一转换为 w=1200,f=jpg 大图 JPEG。
    """
    import re
    headers = {**HEADERS, "Referer": "https://gravure.mantan-web.jp/"}

    # 找 photopage 基础路径：去掉末尾 photopage/XXX.html
    base_url = re.sub(r'/photopage/\d+\.html.*$', '', gallery_url)
    if not base_url.endswith('/'):
        base_url += '/'

    images: list[str] = []

    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")

        # 新格式：直接取当前页的大图
        for img in s.find_all("img"):
            src = img.get("src", "")
            if "storage.mantan-web.jp" in src:
                src = _mantan_to_jpeg(src)
                if not any(k in src for k in ("logo", "icon", "banner")):
                    if images:
                        # 已有一张则跳过（当前页主图）
                        continue
                    images.append(src)

        # 旧格式：photolist 导航
        for a in s.find_all("a", class_="photo__photolist-item"):
            img = a.find("img")
            if not img:
                continue
            src = img.get("src", "")
            if "storage.mantan-web.jp" in src:
                src = _mantan_to_jpeg(src)
                if src not in images:
                    images.append(src)
            if len(images) >= MAX_IMAGES:
                return images

        # 新版无 photolist：遍历分页 2..MAX_IMAGES
        if len(images) == 1:
            m = re.search(r'/photopage/(\d+)\.html', gallery_url)
            start = int(m.group(1)) if m else 1
            end = min(20, start + MAX_IMAGES)
            for p in range(start + 1, end + 1):
                page_url = f"{base_url}photopage/{p:03d}.html"
                try:
                    rp = requests.get(page_url, headers=headers, timeout=15)
                    sp = BeautifulSoup(rp.text, "html.parser")
                    for img in sp.find_all("img"):
                        src = img.get("src", "")
                        if "storage.mantan-web.jp" in src:
                            src = _mantan_to_jpeg(src)
                            if not any(k in src for k in ("logo", "icon", "banner")):
                                if src not in images:
                                    images.append(src)
                                break
                except Exception as e:
                    print(f"  ⚠️ mantan page {p:03d} 失败: {e}")
                time.sleep(0.3)
                if len(images) >= MAX_IMAGES:
                    break
    except Exception as e:
        print(f"  ⚠️ mantan-web 抓取失败: {e}")

    return images


def _scrape_thetv(gallery_url: str) -> list[str]:
    """thetv.jp 图集：/news/detail/{article_id}/{image_id}/landing/
    从 news_feed 提取主图 + 遍历分页链接获取所有图片。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://thetv.jp/"}
    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"

    article_m = re.search(r'/(?:news/)?detail/(\d+)/', p.path)
    if not article_m:
        return []
    article_id = article_m.group(1)

    images: list[str] = []
    seen_ids: set[int] = set()

    def _add_image(img_id: int, src: str):
        if img_id in seen_ids:
            return
        seen_ids.add(img_id)
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = f"{base}{src}"
        src = re.sub(r'\?w=\d+(&h=\d+(&f=\d+)?)?', '', src)
        src = f"{src}?w=2560"
        images.append(src)

    # --- 第一页：提取 news_feed 主图 + 收集分页 ---
    resp = requests.get(gallery_url, headers=headers, timeout=15)
    if not resp.text:
        return []
    soup = BeautifulSoup(resp.text, "html.parser")

    # 1a. news_feed 中的主图（figure > a > img）
    feed = soup.find("div", class_="news_feed")
    if feed:
        for fig in feed.find_all("figure"):
            for img in fig.find_all("img"):
                src = img.get("src") or img.get("data-src") or ""
                if "/i/nw/" in src and article_id in src:
                    mid = re.search(r'/i/nw/\d+/(\d+)\.', src)
                    if mid:
                        _add_image(int(mid.group(1)), src)

    # 1b. 收集所有分页链接
    detail_re = re.compile(rf'/(?:news/)?detail/{article_id}/(\d+)/')
    page_urls: list[tuple[int, str]] = []
    seen_pages: set[int] = set()
    for a_tag in soup.find_all("a", href=True):
        m = detail_re.search(a_tag["href"])
        if not m:
            continue
        img_num = int(m.group(1))
        if img_num in seen_pages:
            continue
        seen_pages.add(img_num)
        href = a_tag["href"]
        full = href if href.startswith("http") else f"{base}{href}"
        full = re.sub(r'/landing/?$', '/', full)
        page_urls.append((img_num, full))

    # --- 逐页抓取 ---
    for img_num, page_url in page_urls[:MAX_IMAGES]:
        if len(images) >= MAX_IMAGES:
            break
        try:
            r = requests.get(page_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")
            target_pat = f"/i/nw/{article_id}/{img_num}"
            for img in s.find_all("img"):
                src = img.get("data-src") or img.get("src") or ""
                if target_pat in src and ".jpg" in src:
                    _add_image(img_num, src)
                    break
        except Exception as e:
            print(f"  ⚠️ thetv page {page_url} 失败: {e}")

    return images[:MAX_IMAGES]


def _scrape_efight(gallery_url: str) -> list[str]:
    """efight.jp 图集：从文章页提取所有 /attachment/* 链接，逐页抓取原图。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://efight.jp/"}
    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"

    attachments: set[str] = set()
    # 扫描当前页 + 可能的 /2 /3 分页
    article_slug = re.sub(r'/\d+/?$', '', gallery_url.rstrip('/'))
    for pn in range(1, 6):
        p_url = f"{article_slug}/{pn}" if pn > 1 else article_slug
        try:
            r = requests.get(p_url, headers=headers, timeout=15)
            sp = BeautifulSoup(r.text, "html.parser")
            for a_tag in sp.find_all("a", href=True):
                href = a_tag["href"]
                if "/attachment/" in href:
                    full = href if href.startswith("http") else f"{base}{href}"
                    attachments.add(full)
        except Exception:
            pass
        time.sleep(0.3)

    if not attachments:
        return []

    images: list[str] = []
    for att_url in list(attachments)[:MAX_IMAGES]:
        try:
            r = requests.get(att_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")
            imgs = s.select("div.attachment img") or s.select("article img, .entry img")
            for img in imgs:
                src = (img.get("data-src") or img.get("src") or "")
                if "wp-content/uploads/" in src and any(e in src.lower() for e in [".jpg", ".jpeg", ".png"]):
                    src = re.sub(r'-\d+x\d+(\.\w+)$', r'\1', src)
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        src = f"{base}{src}"
                    if src not in images:
                        images.append(src)
                    break
        except Exception as e:
            print(f"  ⚠️ efight {att_url} 失败: {e}")
        time.sleep(0.3)

    return images


def _scrape_maidonanews(gallery_url: str) -> list[str]:
    """maidonanews.jp 图集：支持两种页面结构"""
    import re
    from urllib.parse import urljoin, urlparse, parse_qs
    headers = {**HEADERS, "Referer": "https://maidonanews.jp/"}

    images: list[str] = []
    seen: set[str] = set()

    def _extract_640px(src: str) -> str:
        if src.startswith("//"): src = "https:" + src
        elif src.startswith("/"): src = urljoin("https://maidonanews.jp", src)
        src = re.sub(r'\?.*$', '', src)
        return re.sub(r'/picture/(\w+)/(\w+)_\d+(px|square)\.(jpg|png)$',
                      r'/picture/\1/\2_640px.\4', src)

    # 先取第一页
    r = requests.get(gallery_url, headers=headers, timeout=15)
    s = BeautifulSoup(r.text, "html.parser")

    # 结构A：figure.module-article-photo + Next/Prev 分页
    fig = s.select_one("figure.module-article-photo")
    if fig:
        url = gallery_url
        for _ in range(MAX_IMAGES):
            try:
                if url != gallery_url:
                    r = requests.get(url, headers=headers, timeout=15)
                    s = BeautifulSoup(r.text, "html.parser")
                img = s.select_one("figure.module-article-photo img")
                if img:
                    src = (img.get("data-src") or img.get("src") or "")
                    if src:
                        src = _extract_640px(src)
                        if src not in seen:
                            seen.add(src)
                            images.append(src)
                next_btn = s.select_one(".module-article-photo-button--next a")
                if not next_btn: break
                next_url = urljoin("https://maidonanews.jp", next_btn.get("href", ""))
                if next_url == url: break
                url = next_url
                time.sleep(0.3)
            except Exception as e:
                print(f"  ⚠️ maidonanews 结构A 失败: {e}")
                break
        return images

    # 结构B：main.layout-main 内图片 + ?p= 分页
    main = s.select_one("main.layout-main, #main, .layout-main")
    if not main:
        return images

    # 收集本页 main 内的图片（过滤缩略图后缀 120px/200px/square）
    for img in main.select("img"):
        src = img.get("data-src") or img.get("src", "")
        if not src or "potaufeu.asahi.com" not in src or "/picture/" not in src:
            continue
        # 跳过缩略图
        if re.search(r'_\d+(px|square)\.', src) and "_640px" not in src:
            continue
        src = _extract_640px(src)
        if src not in seen:
            seen.add(src)
            images.append(src)
        if len(images) >= MAX_IMAGES:
            break

    return images


def _scrape_lasisa(gallery_url: str) -> list[str]:
    """lasisa.net 图集：从 main 取 wp-content uploads，过滤侧栏缩略图"""
    import re
    headers = {**HEADERS, "Referer": "https://lasisa.net/"}

    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")
        main = s.select_one("main") or s

        images: list[str] = []
        seen: set[str] = set()
        for img in main.select("img"):
            src = img.get("data-src") or img.get("src", "")
            if "wp-content/uploads" not in src:
                continue
            # 过滤侧栏缩略图（带尺寸后缀的）
            if re.search(r'-\d+x\d+\.(jpg|png|webp)$', src):
                continue
            # 过滤非内容图
            skip_names = ["favicon", "lasisa_ipnone"]
            if any(s in src.lower() for s in skip_names):
                continue
            if src not in seen:
                seen.add(src)
                images.append(src)
            if len(images) >= MAX_IMAGES:
                break
        return images
    except Exception as e:
        print(f"  ⚠️ lasisa 抓取失败: {e}")
        return []


def _scrape_realsound(gallery_url: str) -> list[str]:
    """realsound.jp 图集：跟随「次のページ」链抓取 figure img，过滤侧栏缩略图"""
    import re
    from urllib.parse import urljoin
    headers = {**HEADERS, "Referer": "https://realsound.jp/"}

    images: list[str] = []
    seen: set[str] = set()
    url = gallery_url

    for _ in range(MAX_IMAGES):
        try:
            r = requests.get(url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")

            img = s.select_one("figure img")
            if img:
                src = img.get("src") or img.get("data-src") or ""
                if src:
                    src = urljoin("https://realsound.jp", src)
                    # 过滤侧栏缩略图（URL 含尺寸如 -329x468）
                    if not re.search(r'-\d+x\d+\.(jpg|png)$', src) and src not in seen:
                        seen.add(src)
                        images.append(src)

            # 跟随「次のページ」
            next_a = None
            for a in s.select("a"):
                if "次のページ" in a.get_text():
                    next_a = a
                    break
            if not next_a:
                break
            next_url = urljoin("https://realsound.jp", next_a.get("href", ""))
            if next_url == url:
                break
            url = next_url
            time.sleep(0.3)
        except Exception as e:
            print(f"  ⚠️ realsound 抓取失败: {e}")
            break

    return images


def _scrape_encount(gallery_url: str) -> list[str]:
    """encount.press 图集：包含Twitter embed，只取 article body 内图片和 x.com 推文图片。"""
    import re
    headers = {**HEADERS, "Referer": "https://encount.press/"}

    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")

        # 限定 article body 容器
        body = s.find(class_="single__content__txt")
        if not body:
            body = s

        images: list[str] = []
        seen: set[str] = set()

        # 1. 提取 article body 内的 wp-content/uploads 图片，过滤 banner/recruit
        banner_kw = ["banner", "recruit", "_SP_", "600x200"]
        for img in body.find_all("img"):
            src = (img.get("data-src") or img.get("src") or "")
            if ("wp-content/uploads/" in src and
                src.endswith(('.jpg', '.jpeg', '.png', '.webp')) and
                "hatena_white.png" not in src and
                "logo.svg" not in src and
                "icon_" not in src):

                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("/"):
                    src = "https://encount.press" + src

                src = src.split('?')[0]

                if any(kw in src for kw in banner_kw):
                    continue

                if src not in seen:
                    seen.add(src)
                    images.append(src)
                    if len(images) >= MAX_IMAGES:
                        break

        # 2. 提取 article body 内的 Twitter 推文图片
        # 2a. pbs.twimg.com media 直抓（CDP 渲染后可能出现）
        body_html = str(body)
        for m in re.finditer(r'pbs\.twimg\.com/media/([A-Za-z0-9]+)', body_html):
            img_key = m.group(1)
            img_url = f"https://pbs.twimg.com/media/{img_key}?format=jpg&name=large"
            if img_url not in seen:
                seen.add(img_url)
                images.append(img_url)
                print(f"    🖼️ 抓取Twitter图片: {img_key}")
                if len(images) >= MAX_IMAGES:
                    break

        # 2b. blockquote.twitter-tweet 内的 tweet_id（静态 HTML）
        for bq in body.find_all("blockquote", class_="twitter-tweet"):
            for a in bq.find_all("a", href=True):
                m = re.search(r'(?:twitter\.com|x\.com)/\w+/status/(\d+)', a["href"])
                if m:
                    tweet_id = m.group(1)
                    print(f"    🔗 发现Twitter embed: {tweet_id}")
                    twitter_images = _get_twitter_images_for_encount(tweet_id)
                    for img_url in twitter_images:
                        if img_url not in seen:
                            seen.add(img_url)
                            images.append(img_url)
                            if len(images) >= MAX_IMAGES:
                                break

        return images
    except Exception as e:
        print(f"  ⚠️ encount.press 抓取失败: {e}")
        return []


def _scrape_abema_tv(gallery_url: str) -> list[str]:
    """times.abema.tv 分页文章：CDP 阻断 widgets.js 后提取 blockquote 中的 tweet_id，
    通过 fxtwitter API 获取推文图片。"""
    import re
    import json as _json
    from urllib.parse import urlparse, urlunparse

    try:
        import websocket
    except ImportError:
        print("  ⚠️ 需要安装: pip install websocket-client")
        return []

    headers = {**HEADERS, "Referer": "https://times.abema.tv/"}

    # 先获取分页信息
    resp = requests.get(gallery_url, headers=headers, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    max_page = 1
    for a in soup.find_all("a", href=True):
        m = re.search(r'[?&]page=(\d+)', a["href"])
        if m:
            max_page = max(max_page, int(m.group(1)))
    p = urlparse(gallery_url)
    base_url = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))

    try:
        tabs = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5).json()
        page_tab = next((t for t in tabs if t.get("type") == "page"), None)
        if not page_tab:
            print("  ⚠️ 找不到可用的 Chrome tab")
            return []
    except Exception as e:
        print(f"  ⚠️ 无法连接 Chrome CDP: {e}")
        return []

    if max_page > 1:
        print(f"  📄 发现 {max_page} 页分页")

    tweet_ids_seen: set[str] = set()
    tweet_ids: list[str] = []

    ws_url = page_tab["webSocketDebuggerUrl"]

    for pg in range(1, max_page + 1):
        pg_url = gallery_url if pg == 1 else f"{base_url}?page={pg}"
        try:
            ws = websocket.create_connection(ws_url, timeout=15)
            ws.settimeout(15)

            # 阻断 widgets.js，保留 blockquote.twitter-tweet 原始 HTML
            ws.send(_json.dumps({"id": 0, "method": "Network.enable"}))
            ws.recv()
            ws.send(_json.dumps({"id": 1, "method": "Network.setBlockedURLs",
                     "params": {"urls": ["platform.twitter.com/widgets.js"]}}))
            ws.recv()

            ws.send(_json.dumps({"id": 2, "method": "Page.enable"}))
            ws.recv()
            ws.send(_json.dumps({"id": 3, "method": "Page.navigate", "params": {"url": pg_url}}))

            import time as _time
            start = _time.time()
            while _time.time() - start < 15:
                msg = _json.loads(ws.recv())
                if msg.get("method") == "Page.loadEventFired":
                    break
            ws.close()

            _time.sleep(1)
            tabs2 = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5).json()
            pg_tab = next((t for t in tabs2 if t.get("type") == "page"), None)
            if not pg_tab:
                continue
            ws_url = pg_tab["webSocketDebuggerUrl"]

            ws2 = websocket.create_connection(ws_url, timeout=15)
            ws2.settimeout(15)
            ws2.send(_json.dumps({"id": 9, "method": "Runtime.evaluate",
                     "params": {"expression": 'document.querySelector(".article-body").innerHTML'}}))
            html = ""
            while True:
                msg = _json.loads(ws2.recv())
                if msg.get("id") == 9:
                    html = msg.get("result", {}).get("result", {}).get("value", "")
                    break
            ws2.close()

            for bq in re.findall(
                r'<blockquote[^>]*twitter-tweet[^>]*>(.*?)</blockquote>', html, re.DOTALL):
                for m in re.finditer(r'(?:twitter\.com|x\.com)/\w+/status/(\d+)', bq):
                    tid = m.group(1)
                    if tid not in tweet_ids_seen:
                        tweet_ids_seen.add(tid)
                        tweet_ids.append(tid)
        except Exception as e:
            print(f"  ⚠️ abema.tv CDP page {pg} 失败: {e}")

    images: list[str] = []
    if tweet_ids:
        print(f"  🔗 发现 {len(tweet_ids)} 个 x.com 推文嵌入")
        tw_headers = {"User-Agent": HEADERS["User-Agent"]}
        for tid in tweet_ids:
            imgs = _get_twitter_images_from_embed(tid, tw_headers)
            images.extend(imgs)
            if len(images) >= MAX_IMAGES:
                break

    return images


def _get_twitter_images_for_encount(tweet_id: str) -> list[str]:
    """为encount.press获取推文图片（通过 fxtwitter API）"""
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    return _get_twitter_images_from_embed(tweet_id, headers)


def _get_twitter_images_from_embed(tweet_id: str, headers: dict) -> list[str]:
    """从Twitter embed获取图片URL（优先 fxtwitter API，回退 syndication）"""
    images: list[str] = []

    # 方法1：fxtwitter API（无需认证，返回完整 media 信息）
    try:
        r = requests.get(
            f"https://api.fxtwitter.com/twitter/status/{tweet_id}",
            headers=headers, timeout=10,
        )
        if r.status_code == 200:
            data = r.json()
            media_all = data.get("tweet", {}).get("media", {}).get("all", [])
            for m in media_all:
                mtype = m.get("type", "")
                if mtype == "photo":
                    url = m.get("direct_url") or m.get("url", "")
                    if url and "pbs.twimg.com" in url:
                        images.append(url)
                elif mtype in ("video", "animated_gif"):
                    # 推文视频用 yt-dlp 下载（直链 403），传推文链接
                    author = data.get("tweet", {}).get("author", {}).get("screen_name", "user")
                    images.append(f"https://x.com/{author}/status/{tweet_id}")
            if images:
                print(f"    ✅ fxtwitter 获取 {len(images)} 张图片/视频")
                return images
            print(f"    ⚠️ 推文无媒体内容，跳过")
    except Exception as e:
        print(f"    ⚠️ fxtwitter API 失败: {e}")

    # 方法2：syndication API（部分推文可能不可用）
    try:
        api_url = f"https://cdn.syndication.twimg.com/widgets/tweet?url=https%3A%2F%2Ftwitter.com%2Fuser%2Fstatus%2F{tweet_id}"
        r = requests.get(api_url, headers=headers, timeout=10)
        if r.status_code == 200:
            data = r.json()
            if "extended_entities" in data and "media" in data["extended_entities"]:
                for media in data["extended_entities"]["media"]:
                    if "media_url_https" in media:
                        images.append(media["media_url_https"] + ":large")
            elif "entities" in data and "media" in data["entities"]:
                for media in data["entities"]["media"]:
                    if "media_url_https" in media:
                        images.append(media["media_url_https"] + ":large")
            if images:
                print(f"    ✅ syndication 获取 {len(images)} 张图片")
                return images
    except Exception as e:
        print(f"    ⚠️ syndication API 失败: {e}")

    print(f"    ❌ 无法获取推文图片: {tweet_id}")
    return []


def _scrape_chunichi(gallery_url: str) -> list[str]:
    """chunichi.co.jp 文章页：只提取正文图（/article/size1/），排除推荐缩略图和 UI 素材"""
    from urllib.parse import urlparse, urlunparse

    # 去掉 fragment（#1 锚点）
    p = urlparse(gallery_url)
    clean_url = urlunparse(p._replace(fragment=""))

    headers = {**HEADERS, "Referer": "https://www.chunichi.co.jp/"}
    images = []

    try:
        r = requests.get(clean_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")

        seen = set()
        for img in s.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if not src:
                continue
            if "static.chunichi.co.jp" not in src:
                continue
            # 协议补全
            if src.startswith("//"):
                src = "https:" + src
            # 只取正文大图：路径必须含 /article/size1/
            # size3 是推荐文章缩略图，/images/ 目录是 logo/banner/icon
            if "/article/size1/" not in src:
                continue
            if src in seen:
                continue
            seen.add(src)
            images.append(src)
            if len(images) >= MAX_IMAGES:
                break
    except Exception as e:
        print(f"  ⚠️ chunichi 抓取失败: {e}")

    return images


def _scrape_inside_games(gallery_url: str) -> list[str]:
    """inside-games.jp 图集：从缩略图导航条提取所有图片 ID，转换为大图 URL。

    URL 格式：/article/img/YYYY/MM/DD/<article_id>/<img_id>.html
    缩略图路径：/imgs/p/<hash>/<img_id>.jpg  → 大图路径：/imgs/zoom/<img_id>.jpg

    精准过滤策略：导航缩略图的 <a href> 都指向同一 article_id 下的图片页，
    推荐/特集等无关缩略图指向不同 article_id，可借此剔除噪声。
    """
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://www.inside-games.jp/"}
    images = []

    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")

        p = urlparse(gallery_url)
        base_origin = f"{p.scheme}://{p.netloc}"

        # 从 URL 中提取当前 article_id
        # 路径形如 /article/img/2026/04/25/180484/1699001.html
        m_article = re.search(r"/article/img/\d+/\d+/\d+/(\d+)/", p.path)
        article_id = m_article.group(1) if m_article else None

        seen_ids: list[str] = []
        seen_set: set[str] = set()

        if article_id:
            # 精准模式：只取 <a href> 指向同一 article_id 的缩略图
            # 导航条：<a href="/article/img/.../180484/1699003.html"><img src="/imgs/p/hash/1699003.jpg">
            article_img_pattern = re.compile(
                rf"/article/img/\d+/\d+/\d+/{re.escape(article_id)}/(\d+)\.html"
            )
            for a in s.find_all("a", href=True):
                href = a["href"]
                m = article_img_pattern.search(href)
                if not m:
                    continue
                img_id = m.group(1)
                if img_id not in seen_set:
                    seen_set.add(img_id)
                    seen_ids.append(img_id)
        else:
            # fallback：从 /imgs/p/<hash>/<img_id>.jpg 提取（可能含噪声）
            for img in s.find_all("img"):
                src = img.get("src", "")
                m = re.search(r"/imgs/p/[^/]+/(\d+)\.jpg", src)
                if m:
                    img_id = m.group(1)
                    if img_id not in seen_set:
                        seen_set.add(img_id)
                        seen_ids.append(img_id)

        if seen_ids:
            for img_id in seen_ids[:MAX_IMAGES]:
                images.append(f"{base_origin}/imgs/zoom/{img_id}.jpg")
        else:
            # last-resort fallback：取当前页的大图
            for img in s.find_all("img"):
                src = img.get("src", "")
                if "/imgs/zoom/" in src:
                    if src.startswith("/"):
                        src = base_origin + src
                    if src not in images:
                        images.append(src)
                    if len(images) >= MAX_IMAGES:
                        break

    except Exception as e:
        print(f"  ⚠️ inside-games 抓取失败: {e}")

    return images


def _to_large_url(src: str, domain: str) -> str:
    """将缩略图 URL 转换为大图 URL（站点专用规则）"""
    import re
    if "oricon.co.jp" in domain:
        return src.replace("_p_s_", "_p_o_")
    if "crank-in.net" in domain:
        return re.sub(r'_\d+\.jpg', '_1200.jpg', src)
    if "mdpr.jp" in domain:
        # 去掉 width/crop/upscale 参数，保留 quality=80
        src = re.sub(r'\?.*$', '', src)
        return f"{src}?width=1520&auto=webp&quality=80"
    if "maidonanews.jp" in domain:
        # 替换图片尺寸后缀: 120px/200px → 640px
        return re.sub(r'_(\d+)px\.jpg$', '_640px.jpg', src)
    return src


def _scrape_nikkansports(gallery_url: str) -> list[str]:
    """nikkansports.com 图集：序号导航模式，逐页抓取所有图片。
    
    URL 格式：photonews_nsInc_{news_id}-{seq}.html
    图片格式：/entertainment/news/img/{news_id}-w1300_{seq}.jpg
    """
    import re
    from urllib.parse import urlparse
    headers = {**HEADERS, "Referer": "https://www.nikkansports.com/"}

    # 从URL中提取基础路径、新闻ID、序号
    # URL 形如 /entertainment/photonews/... 或 /entertainment/column/sakamichi/photonews/...
    m = re.search(r'photonews_nsInc_(\d+)-(\d+)\.html', gallery_url)
    if not m:
        return []

    p = urlparse(gallery_url)
    news_id = m.group(1)
    start_seq = int(m.group(2))

    # 提取 photonews 前缀：/.../column/sakamichi/ → news/img/ prefix
    prefix = re.sub(r'photonews_nsInc_.*$', '', p.path)
    if not prefix.endswith("/"):
        prefix = prefix.rsplit("/", 1)[0] + "/"
    img_prefix = prefix.replace("photonews/", "news/img/")

    base_url = f"{p.scheme}://{p.netloc}{prefix}"
    images: list[str] = []

    # 访问起始页收集所有 seq 编号（从导航链接提取，比 HEAD probe 可靠）
    try:
        resp = requests.get(gallery_url, headers=headers, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        valid_seqs = {start_seq}
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if news_id not in href:
                continue
            m2 = re.search(rf'photonews_nsInc_{re.escape(news_id)}-(\d+)\.html', href)
            if m2:
                valid_seqs.add(int(m2.group(1)))

        for seq in sorted(valid_seqs)[:MAX_IMAGES]:
            img_url = f"{p.scheme}://{p.netloc}{img_prefix}{news_id}-w1300_{seq}.jpg"
            images.append(img_url)
    except Exception as e:
        print(f"  ⚠️ nikkansports 获取分页失败: {e}")
        # fallback: 只返回当前页图片
        images.append(f"{p.scheme}://{p.netloc}{img_prefix}{news_id}-w1300_{start_seq}.jpg")

    return images


def _scrape_mdpr(gallery_url: str) -> list[str]:
    """mdpr.jp 图集：从页面提取所有 article 图片 URL，统一转换为 width=1520 大图。"""
    import re
    headers = {**HEADERS, "Referer": "https://mdpr.jp/"}

    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")

        images: list[str] = []
        seen: set[str] = set()

        for img in s.find_all("img"):
            src = (img.get("data-src") or img.get("src") or "")
            if "img-mdpr.freetls.fastly.net/article/" not in src:
                continue
            # 去掉查询参数，统一加 width=1520
            base = re.sub(r'\?.*$', '', src)
            if base in seen:
                continue
            seen.add(base)
            large = f"{base}?width=1520&auto=webp&quality=80"
            images.append(large)
            if len(images) >= MAX_IMAGES:
                break

        return images
    except Exception as e:
        print(f"  ⚠️ mdpr.jp 抓取失败: {e}")
        return []


def _cdp_navigate(url: str, wait_seconds: float = 3.0) -> bool:
    """用 CDP 导航到 url，等待 loadEventFired 后断开连接。
    返回是否成功导航（Chrome CDP 是否可用）。
    """
    try:
        import websocket
    except ImportError:
        return False

    try:
        resp = requests.get("http://127.0.0.1:9222/json", timeout=5)
        if resp.status_code != 200:
            return False
        tabs = resp.json()
    except Exception:
        return False

    ws_url = next(
        (t.get("webSocketDebuggerUrl", "") for t in tabs if t.get("type") == "page"),
        ""
    )
    if not ws_url:
        return False

    try:
        ws = websocket.create_connection(ws_url, timeout=15)
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.navigate", "params": {"url": url}}))
        start = time.time()
        while time.time() - start < 20:
            try:
                msg = json.loads(ws.recv())
                if msg.get("method") == "Page.loadEventFired":
                    break
            except Exception:
                break
        ws.close()
        time.sleep(wait_seconds)
        return True
    except Exception as e:
        print(f"  ⚠️ CDP 导航失败: {e}")
        return False


def _cdp_read_instagram_iframe(post_url_fragment: str) -> list[str]:
    """从 CDP target 列表中找匹配 post_url_fragment 的 Instagram embed iframe，
    直接连接并读取其 outerHTML，提取 cdninstagram.com 最大图片 URL。
    post_url_fragment 如 '/p/DXgG89Nk0BA/' 或 '/reel/DXlzkAJCZJh/'。
    """
    try:
        import websocket
    except ImportError:
        return []

    try:
        resp = requests.get("http://127.0.0.1:9222/json", timeout=5)
        tabs = resp.json()
    except Exception:
        return []

    # 找匹配的 Instagram iframe target
    ig_tab = next(
        (t for t in tabs
         if "instagram.com" in t.get("url", "")
         and "embed" in t.get("url", "")
         and post_url_fragment in t.get("url", "")),
        None
    )
    if not ig_tab:
        return []

    ws_url = ig_tab.get("webSocketDebuggerUrl", "")
    if not ws_url:
        return []

    try:
        ws = websocket.create_connection(ws_url, timeout=15)
        ws.send(json.dumps({
            "id": 1, "method": "Runtime.evaluate",
            "params": {"expression": "document.documentElement.outerHTML"},
        }))
        html = ""
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                html = msg.get("result", {}).get("result", {}).get("value", "")
                break
        ws.close()
        return _extract_instagram_images(html)
    except Exception as e:
        print(f"  ⚠️ 读 Instagram iframe 失败: {e}")
        return []


def _extract_instagram_images(html: str) -> list[str]:
    """从已渲染的 HTML 中提取 scontent-*.cdninstagram.com 最大尺寸图片 URL。
    优先取 srcset 中最大 w 的项（1080w），去重后返回。
    """
    import re
    soup = BeautifulSoup(html, "html.parser")
    images: list[str] = []
    seen: set[str] = set()

    for img in soup.find_all("img"):
        # 只取 Instagram CDN 图片
        src = img.get("src", "")
        if "cdninstagram.com" not in src:
            continue

        # 从 srcset 取最大 w
        best_url, best_w = src, 0
        srcset = img.get("srcset", "")
        if srcset:
            for part in srcset.split(","):
                tokens = part.strip().split()
                if not tokens:
                    continue
                u = tokens[0]
                w = 0
                if len(tokens) >= 2:
                    try:
                        w = int(tokens[1].rstrip("w"))
                    except ValueError:
                        pass
                if w > best_w:
                    best_w, best_url = w, u

        # 去掉会过期的 oh=/oe= 参数可能导致下载失败，保留原 URL
        if best_url not in seen:
            seen.add(best_url)
            images.append(best_url)

    return images


def _scrape_thefirsttimes_sns(gallery_url: str) -> list[str]:
    """通过 CDP 逐页处理 thefirsttimes.jp /attachment-sns/ 页面：
    1. 导航到每个分页（loadEventFired 后断开）
    2. 等待 Instagram embed iframe 渲染
    3. 直接连接 Instagram iframe 的 CDP target 读取图片
    """
    import re
    from urllib.parse import urlparse

    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"
    article_id_m = re.search(r'/news/(\d+)/', p.path)
    if not article_id_m:
        return []
    article_id = article_id_m.group(1)

    # 获取总页数
    headers = {**HEADERS, "Referer": "https://www.thefirsttimes.jp/"}
    base_sns = f"{base}/news/{article_id}/attachment-sns"
    page_infos: list[tuple[str, str]] = []  # (page_url, instagram_post_id)
    try:
        r = requests.get(f"{base_sns}/1/", headers=headers, timeout=15)
        soup = BeautifulSoup(r.text, "html.parser")
        max_page = 1
        for a in soup.find_all("a", href=True):
            m = re.search(r'/attachment-sns/(\d+)/?$', a["href"])
            if m:
                max_page = max(max_page, int(m.group(1)))
    except Exception as e:
        print(f"  ⚠️ 获取 SNS 页数失败: {e}")
        max_page = 1

    # 收集每页的 Instagram post URL
    for i in range(1, min(max_page, MAX_IMAGES) + 1):
        page_url = f"{base_sns}/{i}/"
        try:
            r = requests.get(page_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")
            bq = s.find("blockquote", class_="instagram-media")
            if bq:
                permalink = bq.get("data-instgrm-permalink", "")
                # 提取 /p/SHORTCODE/ 或 /reel/SHORTCODE/
                m2 = re.search(r'/(p|reel)/([A-Za-z0-9_-]+)/', permalink)
                if m2:
                    post_frag = f"/{m2.group(1)}/{m2.group(2)}/"
                    page_infos.append((page_url, post_frag))
        except Exception:
            pass

    if not page_infos:
        return []

    print(f"  📄 共 {len(page_infos)} 页 SNS embed，通过 CDP 逐页读取 Instagram iframe...")

    images: list[str] = []
    seen: set[str] = set()

    for i, (page_url, post_frag) in enumerate(page_infos, 1):
        print(f"    [{i}/{len(page_infos)}] 导航到: {page_url}")
        # 导航 + 等待 embed 加载
        _cdp_navigate(page_url, wait_seconds=6.0)
        # 读 Instagram iframe target
        page_imgs = _cdp_read_instagram_iframe(post_frag)
        print(f"    找到 {len(page_imgs)} 张图片")
        for u in page_imgs:
            if u not in seen:
                seen.add(u)
                images.append(u)
        if len(images) >= MAX_IMAGES:
            break

    return images[:MAX_IMAGES]


def _scrape_thefirsttimes(gallery_url: str) -> list[str]:
    """thefirsttimes.jp 图集分两种类型：

    /attachment/{slug}/     → 图片版，直接抓 wp-content/uploads 大图
    /attachment-sns/{n}/   → Instagram embed，通过 CDP 渲染后提取图片
                             若无 Chrome/CDP，回溯文章页找图片版兜底
    """
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://www.thefirsttimes.jp/"}
    p = urlparse(gallery_url)
    m_article = re.search(r'/news/(\d+)/', p.path)
    if not m_article:
        return []
    article_id = m_article.group(1)
    article_url = f"{p.scheme}://{p.netloc}/news/{article_id}/"

    # ── SNS embed 路径 ────────────────────────────────────
    if "attachment-sns" in gallery_url:
        # 先检查 Chrome CDP 是否可用
        cdp_ok = False
        try:
            r = requests.get("http://127.0.0.1:9222/json", timeout=3)
            cdp_ok = r.status_code == 200 and bool(r.json())
        except Exception:
            pass

        if cdp_ok:
            print("  🌐 通过 CDP 渲染 Instagram embed...")
            imgs = _scrape_thefirsttimes_sns(gallery_url)
            if imgs:
                return imgs
            print("  ⚠️ CDP 渲染未抓到图片，回溯图片版兜底...")
        else:
            print("  ⚠️ Chrome CDP 不可用，回溯文章页寻找图片版图集...")

        # 兜底：回文章页找图片版 attachment 入口
        try:
            r = requests.get(article_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")
            slug_pattern = re.compile(rf"/news/{article_id}/attachment/([^/]+)/?$")
            for a in s.find_all("a", href=True):
                if "attachment-sns" in a["href"]:
                    continue
                m = slug_pattern.search(a["href"])
                if m:
                    first_slug = m.group(1)
                    real_url = f"{p.scheme}://{p.netloc}/news/{article_id}/attachment/{first_slug}/"
                    print(f"  🔄 找到图片版入口: {real_url}")
                    return _scrape_thefirsttimes(real_url)
        except Exception as e:
            print(f"  ⚠️ 回溯文章页失败: {e}")
        print("  — 文章页也无图片版图集")
        return []

    # ── 图片版路径 ────────────────────────────────────────
    try:
        r = requests.get(article_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ thefirsttimes 抓取文章页失败: {e}")
        return []

    slug_pattern = re.compile(rf"/news/{article_id}/attachment/([^/]+)/?$")
    slugs: list[str] = []
    seen: set[str] = set()
    for a in s.find_all("a", href=True):
        href = a["href"]
        if "attachment-sns" in href:
            continue
        m = slug_pattern.search(href)
        if m:
            slug = m.group(1)
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)

    if not slugs:
        return _scrape_thefirsttimes_page(gallery_url, headers)

    images: list[str] = []
    base = f"{p.scheme}://{p.netloc}"
    for slug in slugs[:MAX_IMAGES]:
        page_url = f"{base}/news/{article_id}/attachment/{slug}/"
        imgs = _scrape_thefirsttimes_page(page_url, headers)
        images.extend(imgs)
        if len(images) >= MAX_IMAGES:
            break
        time.sleep(0.2)

    return images[:MAX_IMAGES]


def _scrape_thefirsttimes_page(page_url: str, headers: dict) -> list[str]:
    """抓取单个 thefirsttimes attachment 页的大图 URL"""
    import re
    try:
        r = requests.get(page_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")
        images = []
        seen: set[str] = set()
        for img in s.find_all("img"):
            src = img.get("src", "") or img.get("data-src", "")
            if not src:
                continue
            if "wp-content/uploads" not in src:
                continue
            if "themes" in src or "footer" in src or "icon" in src.lower():
                continue
            # 去掉尺寸后缀（-WxH），保留大图
            clean = re.sub(r'-\d+x\d+(\.\w+)$', r'\1', src)
            if clean not in seen:
                seen.add(clean)
                images.append(clean)
        return images
    except Exception as e:
        print(f"  ⚠️ thefirsttimes 页面抓取失败 {page_url}: {e}")
        return []


def _scrape_kstyle(gallery_url: str) -> list[str]:
    """kstyle.com 图集：文章内嵌 cdn.livedoor.jp/kstyle/ 图片，排除 _WI.jpg 缩略图，
    去掉 /r.WxH resize 后缀取原图。"""
    import re
    headers = {**HEADERS, "Referer": "https://kstyle.com/"}

    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")

        images: list[str] = []
        seen: set[str] = set()

        for img in s.find_all("img"):
            src = (img.get("data-src") or img.get("src") or "")
            if "cdn.livedoor.jp/kstyle/" not in src:
                continue
            if src.startswith("//"):
                src = "https:" + src

            # 排除 _WI.jpg 缩略图（相关推荐文章）
            if "_WI.jpg" in src or "_WI." in src:
                continue

            # 去掉 /r.WxH resize 后缀取原图
            src = re.sub(r'/r\.\d+x\d+$', '', src)

            if src in seen:
                continue
            seen.add(src)
            images.append(src)
            if len(images) >= MAX_IMAGES:
                break

        return images
    except Exception as e:
        print(f"  ⚠️ kstyle 抓取失败: {e}")
        return []


def _scrape_yorozoonews(gallery_url: str) -> list[str]:
    """yorozoonews.jp 图集：p.potaufeu.asahi.com CDN 图片，query param ?p= 分页。
    每页展示一张主图，遍历分页收集。优先取 _640px 大图，跳过 _200px 缩略图和作者头像。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://yorozoonews.jp/"}
    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"

    images: list[str] = []
    seen: set[str] = set()
    visited_urls: set[str] = set()
    current_url = gallery_url

    for _ in range(MAX_IMAGES):
        if current_url in visited_urls:
            break
        visited_urls.add(current_url)

        try:
            r = requests.get(current_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")

            large_img = ""
            thumb_candidates: list[str] = []
            body = s.select_one(".module-article-body") or s
            for img in body.find_all("img"):
                src = (img.get("data-src") or img.get("src") or "")
                if "p.potaufeu.asahi.com" not in src or "/picture/" not in src:
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                src = re.sub(r'\?.*$', '', src)
                # 排除作者头像等非正文图片路径
                if any(p in src for p in ["/8a08-p/", "/317d-p/", "/7df7-p/"]):
                    continue
                # 优先 _640px 大图
                if "_640px.jpg" in src:
                    if src not in seen:
                        large_img = src
                        break
                elif "_200px.jpg" in src:
                    thumb_candidates.append(src)

            if large_img:
                if large_img not in seen:
                    seen.add(large_img)
                    images.append(large_img)
            elif thumb_candidates:
                # fallback: 用 _200px 缩略图转 _640px
                src = re.sub(r'_\d+px\.jpg$', '_640px.jpg', thumb_candidates[0])
                if src not in seen:
                    seen.add(src)
                    images.append(src)

            # 找下一个分页
            next_url = ""
            for a in s.find_all("a", href=True):
                text = a.get_text(strip=True).lower()
                if any(kw in text for kw in ["次の写真", "次へ", "next"]):
                    href = a["href"]
                    if "?p=" in href:
                        if href.startswith("/"):
                            href = base + href
                        elif not href.startswith("http"):
                            continue
                        next_url = href
                        break
            if not next_url:
                break
            current_url = next_url

        except Exception as e:
            print(f"  ⚠️ yorozoonews 分页抓取失败 {current_url}: {e}")
            break

    return images


def _scrape_nikkan_spa(gallery_url: str) -> list[str]:
    """nikkan-spa.jp 图集：wp-content/uploads 图片，线性翻页（"次へ" 链接）。
    每页一张大图，沿着 next 链接遍历收集所有图片。
    只取正文容器（article / .entry-content）内的无缩略图后缀原图。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://nikkan-spa.jp/"}
    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"

    images: list[str] = []
    seen: set[str] = set()
    visited_urls: set[str] = set()
    current_url = gallery_url

    # 正文容器 selector（仅从这些容器内取图）
    CONTENT_SELECTOR = "article, .entry-content, .post-content, .attachment-content, .single-content, .main-content"

    for _ in range(MAX_IMAGES):
        if current_url in visited_urls:
            break
        visited_urls.add(current_url)

        try:
            r = requests.get(current_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")

            # 只在正文容器内找图片
            containers = s.select(CONTENT_SELECTOR) or [s]

            for container in containers:
                for img in container.find_all("img"):
                    src = (img.get("data-src") or img.get("src") or "")
                    if "wp-content/uploads/" not in src:
                        continue
                    # 排除缩略图（-WxH 后缀，如 -125x207）
                    if re.search(r'-\d+x\d+(\.\w+)$', src):
                        continue
                    # 排除 _lg 后缀的 banner/特集图
                    if re.search(r'_lg\.(jpg|jpeg|png|webp)$', src):
                        continue
                    # 排除 banner/icon/logo 等关键词
                    src_lower = src.lower()
                    if any(k in src_lower for k in ["logo", "banner", "icon", "noimage",
                                                       "hatena_white", "sns_", "footer",
                                                       "prezent", "backnumber"]):
                        continue
                    if src.startswith("/"):
                        src = base + src
                    elif src.startswith("//"):
                        src = "https:" + src
                    src = src.split("?")[0]
                    if src in seen:
                        continue
                    seen.add(src)
                    images.append(src)
                    break  # 每页 1 张正文大图
                if len(images) > len(seen) - 1:  # 本页已找到，跳出容器循环
                    pass

            # 找下一个分页链接（"次へ" 文本链接）
            next_url = ""
            for a in s.find_all("a", href=True):
                text = a.get_text(strip=True)
                if any(kw in text for kw in ["次へ", "次の写真", "次"]):
                    href = a["href"]
                    if href.startswith("/"):
                        href = base + href
                    elif not href.startswith("http"):
                        continue
                    if href != current_url:
                        next_url = href
                    break
            if not next_url:
                break
            current_url = next_url

        except Exception as e:
            print(f"  ⚠️ nikkan-spa 分页抓取失败 {current_url}: {e}")
            break

    return images


def _scrape_animeanime(gallery_url: str) -> list[str]:
    """animeanime.jp 图集：/article/img/YYYY/MM/DD/article_id/img_id.html，
    从缩略图导航条提取所有图片 ID，转换为 /imgs/zoom/{id}.jpg 大图 URL。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://animeanime.jp/"}
    images: list[str] = []

    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")
        p = urlparse(gallery_url)
        base = f"{p.scheme}://{p.netloc}"

        m_article = re.search(r"/article/img/\d+/\d+/\d+/(\d+)/", p.path)
        article_id = m_article.group(1) if m_article else None

        seen_ids: set[str] = set()
        img_ids: list[str] = []

        if article_id:
            # 精准模式：只取 href 指向同一 article_id 的图片页链接
            article_img_pattern = re.compile(
                rf"/article/img/\d+/\d+/\d+/{re.escape(article_id)}/(\d+)\.html"
            )
            for a in s.find_all("a", href=True):
                href = a["href"]
                m = article_img_pattern.search(href)
                if not m:
                    continue
                img_id = m.group(1)
                if img_id not in seen_ids:
                    seen_ids.add(img_id)
                    img_ids.append(img_id)
        else:
            # fallback：从 /imgs/zoom/ 提取
            for img in s.find_all("img"):
                src = img.get("src", "")
                m = re.search(r"/imgs/zoom/(\d+)\.jpg", src)
                if m:
                    img_id = m.group(1)
                    if img_id not in seen_ids:
                        seen_ids.add(img_id)
                        img_ids.append(img_id)

        if not img_ids:
            # last-resort：取当前页大图
            for img in s.find_all("img"):
                src = img.get("src", "")
                if "/imgs/zoom/" in src:
                    if src.startswith("/"):
                        src = base + src
                    if src not in images:
                        images.append(src)
                    if len(images) >= MAX_IMAGES:
                        break
        else:
            for img_id in img_ids[:MAX_IMAGES]:
                images.append(f"{base}/imgs/zoom/{img_id}.jpg")

    except Exception as e:
        print(f"  ⚠️ animeanime 抓取失败: {e}")

    return images


def _scrape_deview(gallery_url: str) -> list[str]:
    """deview.co.jp 图集：NewsImage?am_article_id=&am_image_no= 分页。
    cdn.deview.co.jp/imgs/news/ 目录下大图，缩略图为 news_image.img.php 代理。
    逐页访问收集主图 URL。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://deview.co.jp/"}
    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"

    # 收集所有图片编号
    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.content, "html.parser", from_encoding="shift_jis")
    except Exception as e:
        print(f"  ⚠️ deview 获取页面失败: {e}")
        return []

    img_nos: set[int] = set()
    m_aid = re.search(r'am_article_id=(\d+)', p.query)
    article_id = m_aid.group(1) if m_aid else ""
    m_img = re.search(r'am_image_no=(\d+)', p.query)
    if m_img:
        img_nos.add(int(m_img.group(1)))

    for a in s.find_all("a", href=True):
        href = a["href"]
        if article_id and article_id in href:
            m2 = re.search(r'am_image_no=(\d+)', href)
            if m2:
                img_nos.add(int(m2.group(1)))

    # 逐页取大图
    images: list[str] = []
    seen: set[str] = set()
    for img_no in sorted(img_nos)[:MAX_IMAGES]:
        try:
            page_url = f"{base}/NewsImage?am_article_id={article_id}&am_image_no={img_no}"
            r = requests.get(page_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.content, "html.parser", from_encoding="shift_jis")

            for img in s.find_all("img"):
                src = (img.get("data-src") or img.get("src") or "")
                # 主图：cdn.deview.co.jp/imgs/news/X/Y/Z/hash.jpg（非 .img.php 代理）
                if "cdn.deview.co.jp/imgs/news/" not in src:
                    continue
                if ".img.php" in src or "/assets/" in src or "/contents/" in src:
                    continue
                # 补全协议
                if src.startswith("//"):
                    src = "https:" + src
                elif src.startswith("http://"):
                    src = src.replace("http://", "https://")
                if src in seen:
                    continue
                seen.add(src)
                images.append(src)
                break

        except Exception as e:
            print(f"  ⚠️ deview image_no={img_no} 失败: {e}")

    return images


def _scrape_mainichikirei(gallery_url: str) -> list[str]:
    """mainichikirei.jp 图集：storage.mainichikirei.jp CDN 图片，?photo= 参数分页。
    主图是 JS 渲染的（HTTP 直抓拿不到），因此从页面提取总页数后直接构造 CDN URL。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://mainichikirei.jp/"}
    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"

    # 从 URL 提取 article ID 和日期
    m_article = re.search(r'/(\d{8}dog\d+m\d+a)\.html', p.path)
    if not m_article:
        return []
    article_id = m_article.group(1)
    date_str = article_id[:8]  # 20260421 → 2026/04/21
    date_path = f"{date_str[:4]}/{date_str[4:6]}/{date_str[6:8]}"

    # 从页面提取总照片数（"2 / 2" 文本）
    total = 1
    try:
        r = requests.get(gallery_url, headers=headers, timeout=15)
        s = BeautifulSoup(r.text, "html.parser")
        m_nav = re.search(r'(\d+)\s*/\s*(\d+)', s.get_text())
        if m_nav:
            total = int(m_nav.group(2))
        else:
            # fallback: 统计指向同一个 article 的 ?photo= 链接
            photo_set: set[int] = set()
            for a in s.find_all("a", href=True):
                href = a["href"]
                if article_id in href:
                    mp = re.search(r'photo=(\d+)', href)
                    if mp:
                        photo_set.add(int(mp.group(1)))
            if photo_set:
                total = max(photo_set)
    except Exception as e:
        print(f"  ⚠️ mainichikirei 获取页数失败: {e}")

    total = min(total, MAX_IMAGES)
    images = []
    for pn in range(1, total + 1):
        img_url = f"https://storage.mainichikirei.jp/images/{date_path}/{article_id}/{pn:03d}.jpg"
        images.append(img_url)

    return images


def _scrape_natalie_gallery(gallery_url: str) -> list[str]:
    """natalie.mu /gallery/news/ 图集：ogre.natalie.mu CDN 大图（imwidth=1460）。
    每页一张大图，沿着 "次へ" 链接遍历所有分页收集图片。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://natalie.mu/"}
    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"

    images: list[str] = []
    seen: set[str] = set()
    visited_urls: set[str] = set()
    current_url = gallery_url

    for _ in range(MAX_IMAGES):
        if current_url in visited_urls:
            break
        visited_urls.add(current_url)

        try:
            r = requests.get(current_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")

            # 当前页大图：ogre.natalie.mu 域名，排除 thumbnail 参数
            for img in s.find_all("img"):
                src = (img.get("data-src") or img.get("src") or "")
                if "ogre.natalie.mu" not in src:
                    continue
                # 排除缩略图（thumbnail 尺寸参数）
                if "width=200" in src.lower() or "w=200" in src.lower():
                    continue
                if "impolicy=thumb" in src.lower():
                    continue
                if src.startswith("//"):
                    src = "https:" + src
                if src in seen:
                    continue
                seen.add(src)
                images.append(src)
                break

            # 找 "次へ" 分页链接
            next_url = ""
            for a in s.find_all("a", href=True):
                text = a.get_text(strip=True)
                if text == "次へ" or "次へ" in text:
                    href = a["href"]
                    if href.startswith("/"):
                        href = base + href
                    elif not href.startswith("http"):
                        continue
                    next_url = href
                    break
            if not next_url:
                break
            current_url = next_url

        except Exception as e:
            print(f"  ⚠️ natalie gallery {current_url} 失败: {e}")
            break

    return images


def _scrape_qjweb(gallery_url: str) -> list[str]:
    """qjweb.jp /article-gallery/ 图集：wp-content/uploads 大图，"次の画像" 翻页。
    每页一张主图，沿着 next 链接遍历。URL 带 -WxH 后缀的去掉取原图。"""
    import re
    from urllib.parse import urlparse

    headers = {**HEADERS, "Referer": "https://qjweb.jp/"}
    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"

    images: list[str] = []
    seen: set[str] = set()
    visited_urls: set[str] = set()
    current_url = gallery_url

    for _ in range(MAX_IMAGES):
        if current_url in visited_urls:
            break
        visited_urls.add(current_url)

        try:
            r = requests.get(current_url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")

            found = False
            for img in s.find_all("img"):
                src = (img.get("data-src") or img.get("src") or "")
                if "/wp-content/uploads/" not in src:
                    continue
                # 排除 icon/logo/banner/theme 等
                if any(k in src.lower() for k in ["/icon", "/logo", "/banner", "/theme", "/assets/"]):
                    continue
                if "/cdn-cgi/" in src:
                    continue
                # 补全协议
                if src.startswith("//"):
                    src = "https:" + src
                # 去掉 -WxH 缩略图后缀取原图
                clean = re.sub(r'-\d+x\d+(\.\w+)$', r'\1', src)
                if clean in seen:
                    continue
                seen.add(clean)
                images.append(clean)
                found = True
                break

            if not found:
                break

            # 找 "次の画像" 链接
            next_url = ""
            for a in s.find_all("a", href=True):
                text = a.get_text(strip=True)
                if "次の画像" in text:
                    href = a["href"]
                    if href.startswith("/"):
                        href = base + href
                    elif not href.startswith("http"):
                        continue
                    next_url = href
                    break
            if not next_url:
                break
            current_url = next_url

        except Exception as e:
            print(f"  ⚠️ qjweb {current_url} 失败: {e}")
            break

    return images


def _scrape_entamenext(gallery_url: str) -> list[str]:
    """entamenext.com /articles/gallery/ID/PHOTO_ID — 全部缩略图已在首页 background-image 中，
    提取 articles_photos 路径，替换尺寸为 ORG 取原图。"""
    import re
    from urllib.parse import urlparse, urlunparse

    p = urlparse(gallery_url)
    # Extract article ID from path /articles/gallery/{article_id}/{photo_id}
    m = re.match(r"/articles/gallery/(\d+)", p.path)
    if not m:
        return []
    article_id = m.group(1)
    base_url = urlunparse((p.scheme, p.netloc, f"/articles/gallery/{article_id}/1", "", "", ""))
    headers = {**HEADERS, "Referer": "https://entamenext.com/"}

    try:
        r = requests.get(base_url, headers=headers, timeout=15)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        print(f"  ⚠️ entamenext 失败: {e}")
        return []

    seen: set[str] = set()
    images: list[str] = []
    pattern = (r"background-image:\s*url\("
               r"(https://images\.entamenext\.com/articles_photos/\d+/"
               + re.escape(article_id) + r"/[^)]+)\)")
    for src in re.findall(pattern, html):
        org = re.sub(r"/[^/]+/([\w]+\.jpg)$", r"/ORG/\1", src)
        fname = org.rsplit("/", 1)[-1]
        if fname not in seen:
            seen.add(fname)
            images.append(org)
    return images


def _scrape_shueisha_online(gallery_url: str) -> list[str]:
    """shueisha.online /articles/-/ID — 图片在 ?disp=paging&page=N 中，
    class=article-photo 容器，ismcdn 大图，升级到 1200mw。"""
    import re
    from urllib.parse import urlparse, urlunparse

    p = urlparse(gallery_url)
    base_url = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    headers = {**HEADERS, "Accept-Encoding": "gzip, deflate, br",
               "Referer": "https://news.yahoo.co.jp/"}

    images: list[str] = []
    seen: set[str] = set()

    page = 1
    while True:
        try:
            r = requests.get(f"{base_url}?disp=paging&page={page}",
                             headers=headers, timeout=15)
            if r.status_code == 404:
                break
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
            found = False
            for div in soup.find_all(class_="article-photo"):
                for img in div.find_all("img"):
                    src = img.get("data-src") or img.get("src", "")
                    if "ismcdn" not in src or "common" in src:
                        continue
                    large = re.sub(r"/(\d+[a-z]+w?)/", "/1200mw/", src)
                    if large not in seen:
                        seen.add(large)
                        images.append(large)
                    found = True
            if not found:
                break
            page += 1
        except Exception as e:
            print(f"  ⚠️ shueisha.online page={page} 失败: {e}")
            break

    return images


def _scrape_wpb_shueisha(gallery_url: str) -> list[str]:
    """wpb.shueisha.co.jp /photo/ — 所有图片已静态内嵌在 .carousel-gallery-a__list img
    中，?page=N 仅控制 JS 展示位置，抓 page=1 即可获取全部。"""
    from urllib.parse import urlparse, urlunparse, urljoin

    p = urlparse(gallery_url)
    base = f"{p.scheme}://{p.netloc}"
    # Strip query so we always fetch page=1
    clean_url = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    headers = {**HEADERS, "Referer": f"{base}/"}

    try:
        resp = requests.get(clean_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ wpb.shueisha.co.jp 抓取失败: {e}")
        return []

    # .group-entry-a is the article's own photo grid (most reliable)
    # Fall back to first .carousel-gallery-a__list if not found
    container = soup.select_one(".group-entry-a") or soup.select_one(".carousel-gallery-a__list")
    if not container:
        return []

    seen: set[str] = set()
    images: list[str] = []
    for img in container.find_all("img"):
        src = img.get("data-src") or img.get("src", "")
        if not src or src.startswith("data:"):
            continue
        full = src if src.startswith("http") else urljoin(base, src)
        if full not in seen:
            seen.add(full)
            images.append(full)

    return images


def _scrape_bookbang(gallery_url: str) -> list[str]:
    """bookbang.jp /article/{id} — 分页格式 /article/{id} (p1) + ?page=N (p2+)
    每页一张主图在 article 标签内 wp-content/uploads 路径。"""
    import re
    from urllib.parse import urlparse, urlunparse

    p = urlparse(gallery_url)
    # 标准化为第1页 URL（去掉 ?page= 参数）
    base_url = urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))
    # 提取 article id（路径末尾数字）
    m = re.search(r'/article/(\d+)', p.path)
    if not m:
        return []

    headers = {**HEADERS, "Referer": "https://www.bookbang.jp/"}
    images: list[str] = []
    seen: set[str] = set()

    # 先抓第1页，同时解析总页数
    try:
        r = requests.get(base_url, headers=headers, timeout=15)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ bookbang 第1页失败: {e}")
        return []

    def _extract_image(soup: "BeautifulSoup") -> None:
        for img in soup.select("article img"):
            src = img.get("src") or img.get("data-src", "")
            if "wp-content/uploads" in src and src not in seen:
                seen.add(src)
                images.append(src)

    _extract_image(soup)

    # 解析总页数：找分页链接中最大的 ?page=N
    max_page = 1
    for a in soup.find_all("a", href=True):
        pm = re.search(r'\?page=(\d+)', a["href"])
        if pm:
            max_page = max(max_page, int(pm.group(1)))

    for page in range(2, max_page + 1):
        try:
            r = requests.get(f"{base_url}?page={page}", headers=headers, timeout=15)
            if r.status_code == 404:
                break
            r.raise_for_status()
            _extract_image(BeautifulSoup(r.text, "html.parser"))
        except Exception as e:
            print(f"  ⚠️ bookbang page={page} 失败: {e}")
            break

    return images


def _scrape_friday_kodansha(gallery_url: str) -> list[str]:
    """friday.kodansha.co.jp /article/ID/photo/HASH — 从 __NEXT_DATA__ 提取所有图片。
    页面302重定向到第一张，所有图片已在 photo_gallery.photos 数组中。"""
    import json, re

    headers = {
        **HEADERS,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "ja-JP,ja;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://news.yahoo.co.jp/",
    }
    try:
        resp = requests.get(gallery_url, headers=headers, timeout=15)
        resp.raise_for_status()
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', resp.text, re.S)
        if not m:
            return []
        data = json.loads(m.group(1))
        photos = (data.get("props", {}).get("pageProps", {})
                      .get("data", {}).get("photo_gallery", {}).get("photos", []))
        return [p["src"] for p in photos if p.get("src")]
    except Exception as e:
        print(f"  ⚠️ friday.kodansha 失败: {e}")
        return []


def _scrape_pinzuba(gallery_url: str) -> list[str]:
    """pinzuba.news /articles/-/ID?page=N 图集：ismcdn 大图（640wm/660w），多页翻页。"""
    import re
    from urllib.parse import urlparse, urljoin, urlencode, parse_qs, urlunparse

    p = urlparse(gallery_url)
    base_url = urlunparse((p.scheme, p.netloc, p.path, "", "", ""))
    headers = {**HEADERS, "Referer": "https://pinzuba.news/"}

    # 收集总页数（先抓第1页）
    try:
        resp = requests.get(base_url + "?page=1", headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except Exception as e:
        print(f"  ⚠️ pinzuba 首页失败: {e}")
        return []

    # 从分页链接推断最大页码
    page_nums = [int(m) for m in re.findall(r'[?&]page=(\d+)', html)]
    total_pages = max(page_nums) if page_nums else 1

    images: list[str] = []
    seen: set[str] = set()

    for page in range(1, total_pages + 1):
        try:
            if page == 1:
                page_html = html
            else:
                r = requests.get(f"{base_url}?page={page}", headers=headers, timeout=15)
                r.raise_for_status()
                page_html = r.text
            soup = BeautifulSoup(page_html, "html.parser")
            # Main article images only: look inside article tag, data-src with large size code
            art = soup.select_one("article, main")
            scope = art if art else soup
            for img in scope.find_all("img"):
                src = img.get("data-src") or img.get("src") or ""
                if "ismcdn.jp" not in src:
                    continue
                if any(x in src for x in ["icon", ".svg", "/common/", "32wm", "80w", "60wm", "300w"]):
                    continue
                if src.endswith(".png") and "mwimgs" in src:
                    continue  # logo/badge PNGs
                # Upgrade to largest available: replace size code with 1200wm
                large = re.sub(r'/mwimgs/([^/]+)/([^/]+)/[^/]+/', r'/mwimgs/\1/\2/1200wm/', src)
                if large not in seen:
                    seen.add(large)
                    images.append(large)
        except Exception as e:
            print(f"  ⚠️ pinzuba page={page} 失败: {e}")
    return images


def _scrape_daily_co_jp(gallery_url: str) -> list[str]:
    """daily.co.jp 图集：?ph=N 参数触发图集模式，所有图都在同一页里。
    article.detailContent > div.photoContent > div.thumb 是文章图，
    ul.figureLists 是关联推荐区需排除。b_ 缩略图升级为 f_ 大图。"""
    headers = {**HEADERS, "Referer": "https://www.daily.co.jp/"}
    # 确保带 ph=1 触发图集模式（?ph=N 中任意一个都能获取全部图）
    from urllib.parse import urlparse, urlunparse, urlencode, parse_qs
    p = urlparse(gallery_url)
    qs = parse_qs(p.query)
    if "ph" not in qs:
        qs["ph"] = ["1"]
    fetch_url = urlunparse((p.scheme, p.netloc, p.path, "", urlencode({k: v[0] for k, v in qs.items()}), ""))

    try:
        resp = requests.get(fetch_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ daily.co.jp 抓取失败: {e}")
        return []

    art = soup.select_one("article.detailContent")
    if not art:
        return []

    images: list[str] = []
    seen: set[str] = set()
    for img in art.select("div.photoContent div.thumb img"):
        src = img.get("src", "")
        if not src:
            continue
        if src.startswith("//"):
            src = "https:" + src
        # b_ 缩略图 → f_ 大图
        src = src.replace("/Images/b_", "/Images/f_")
        if src not in seen:
            seen.add(src)
            images.append(src)
    return images


def _scrape_postseven(gallery_url: str) -> list[str]:
    """news-postseven.com 图集：找「すべての写真を見る」按钮 → 分页抓取 .c-PhotoImage"""
    import re
    from urllib.parse import urljoin
    headers = {**HEADERS, "Referer": "https://www.news-postseven.com/"}
    try:
        resp = requests.get(gallery_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ postseven 抓取失败: {e}")
        return []

    # Find the "view all photos" gallery URL
    gallery_url_full = gallery_url
    for a in soup.select('a[href*=IMAGE][href*=PAGE]'):
        gallery_url_full = urljoin(gallery_url, a['href'])
        break

    # Extract total pages from PAGE=1-N
    total = 1
    m = re.search(r'PAGE=\d+-(\d+)', gallery_url_full)
    if m:
        total = min(int(m.group(1)), MAX_IMAGES)

    images = []
    seen = set()
    for pn in range(1, total + 1):
        page_url = re.sub(r'PAGE=\d+(-\d+)?', f'PAGE={pn}', gallery_url_full)
        if pn > 1:
            try:
                resp = requests.get(page_url, headers=headers, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
            except Exception:
                continue
        for img in soup.select(".c-PhotoImage img, article img[src*=uploads]"):
            src = img.get("src") or img.get("data-src") or ""
            if src and "uploads" in src and src not in seen:
                seen.add(src)
                full = urljoin(gallery_url, src)
                if full.startswith('//'): full = 'https:' + full
                images.append(full)
    return images

def _scrape_pia(gallery_url: str) -> list[str]:
    """lp.p.pia.jp 图集：data-src 懒加载图片，?id=N 分页"""
    import re
    from urllib.parse import urljoin
    headers = {**HEADERS, "Referer": "https://lp.p.pia.jp/"}
    try:
        resp = requests.get(gallery_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ pia.jp 抓取失败: {e}")
        return []

    images: list[str] = []
    seen: set[str] = set()

    # Find total pages from pagination
    total = 1
    base_url = re.sub(r'[?&]id=\d+', '', gallery_url)
    page_links = soup.select('a[href*="id="]')
    for a in page_links:
        m = re.search(r'id=(\d+)', a.get('href', ''))
        if m:
            total = max(total, int(m.group(1)))

    for pn in range(1, min(total, MAX_IMAGES) + 1):
        if pn > 1:
            page_url = f"{base_url}{'&' if '?' in base_url else '?'}id={pn}"
            try:
                resp = requests.get(page_url, headers=headers, timeout=15)
                soup = BeautifulSoup(resp.text, "html.parser")
            except Exception:
                continue
        for img in soup.find_all("img"):
            src = img.get("data-src") or img.get("src") or ""
            if not src or "logo" in src or "icon" in src or "facebook" in src:
                continue
            full = urljoin(gallery_url, src)
            if "shared/materials" in full and full not in seen:
                seen.add(full)
                images.append(full)
    return images

def _scrape_jisin(gallery_url: str) -> list[str]:
    """jisin.jp 图集：取 .slider-show img + 所有 img[src*=uploads] 图片"""
    from urllib.parse import urljoin
    headers = {**HEADERS, "Referer": "https://www.jisin.jp/"}
    try:
        resp = requests.get(gallery_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ jisin.jp 抓取失败: {e}")
        return []

    images: list[str] = []
    seen: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
        if not src or "logo" in src or "icon" in src or "banner" in src:
            continue
        # Prefer large images, skip site chrome
        if src.startswith("/"):
            src = urljoin(gallery_url, src)
        if src not in seen:
            seen.add(src)
            images.append(src)
    # Also try .slider-show specifically
    slider = soup.select_one(".slider-show")
    if slider:
        for img in slider.find_all("img"):
            src = img.get("src") or img.get("data-src") or ""
            if src and src not in seen and "logo" not in src:
                seen.add(src)
                images.insert(0, urljoin(gallery_url, src) if src.startswith("/") else src)
    return images

def _scrape_vivi_tv(gallery_url: str) -> list[str]:
    """vivi.tv 图集：图片通过 Cloudinary CDN 代理，从 URL 提取原始 wp-content/uploads 图片。"""
    import re
    from urllib.parse import unquote

    headers = {**HEADERS, "Referer": "https://www.vivi.tv/"}
    try:
        resp = requests.get(gallery_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        print(f"  ⚠️ vivi.tv 抓取失败: {e}")
        return []

    images: list[str] = []
    seen: set[str] = set()

    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or img.get("data-lazy-src") or ""
        # Cloudinary proxy: https://res.cloudinary.com/vivimedia/image/fetch/.../<orig_url>
        # 或直连: https://www.vivi.tv/wp-content/uploads/...
        orig = ""
        if "cloudinary" in src and "wp-content/uploads" in unquote(src):
            m = re.search(r'https://www\.vivi\.tv/wp-content/uploads/[^&\s]+\.(?:jpg|jpeg|png|webp)', unquote(src))
            if m:
                orig = m.group(0)
        elif "vivi.tv/wp-content/uploads/" in src and src.endswith((".jpg", ".jpeg", ".png", ".webp")):
            orig = src

        if orig:
            orig = orig.split("?")[0]
            if orig not in seen:
                seen.add(orig)
                images.append(orig)

    return images


def _extract_youtube_video_id(html_text: str) -> str:
    """从页面 HTML 提取 YouTube 嵌入视频 ID（youtube.com/embed/VIDEO_ID）"""
    import re
    m = re.search(r'youtube\.com/embed/([A-Za-z0-9_-]{11})', html_text)
    if m:
        return m.group(1)
    return ""


def scrape_gallery_images(gallery_url: str) -> list[str]:
    """通用图集抓取：先用站点 CSS selector 定位容器，再提取大图"""
    domain = _domain_of(gallery_url)

# 站点专用抓取器（分页图集）
    if "natalie.mu" in domain and "/gallery/" in gallery_url:
        images = _scrape_natalie_gallery(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "nikkansports.com" in domain:
        images = _scrape_nikkansports(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "mdpr.jp" in domain:
        images = _scrape_mdpr(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "maidonanews.jp" in domain:
        images = _scrape_maidonanews(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "encount.press" in domain:
        images = _scrape_encount(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "oricon.co.jp" in domain:
        images = _scrape_oricon(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "crank-in.net" in domain:
        images = _scrape_crank_in(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "realsound.jp" in domain:
        images = _scrape_realsound(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "lasisa.net" in domain:
        images = _scrape_lasisa(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "limo.media" in domain:
        images = _scrape_limo(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "mezamashi.media" in domain:
        images = _scrape_mezamashi(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "smart-flash.jp" in domain:
        images = _scrape_smart_flash(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "mantan-web.jp" in domain:
        images = _scrape_mantan(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "chunichi.co.jp" in domain:
        images = _scrape_chunichi(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "inside-games.jp" in domain:
        images = _scrape_inside_games(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "thetv.jp" in domain:
        images = _scrape_thetv(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "efight.jp" in domain:
        images = _scrape_efight(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "thefirsttimes.jp" in domain:
        images = _scrape_thefirsttimes(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "kstyle.com" in domain:
        images = _scrape_kstyle(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "yorozoonews.jp" in domain:
        images = _scrape_yorozoonews(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "nikkan-spa.jp" in domain:
        images = _scrape_nikkan_spa(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "animeanime.jp" in domain:
        images = _scrape_animeanime(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "mainichikirei.jp" in domain:
        images = _scrape_mainichikirei(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "deview.co.jp" in domain:
        images = _scrape_deview(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "qjweb.jp" in domain:
        images = _scrape_qjweb(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "pinzuba.news" in domain:
        images = _scrape_pinzuba(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "friday.kodansha.co.jp" in domain:
        images = _scrape_friday_kodansha(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "entamenext.com" in domain:
        images = _scrape_entamenext(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "shueisha.online" in domain:
        images = _scrape_shueisha_online(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "wpb.shueisha.co.jp" in gallery_url:
        images = _scrape_wpb_shueisha(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "bookbang.jp" in domain:
        images = _scrape_bookbang(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "daily.co.jp" in domain:
        images = _scrape_daily_co_jp(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "vivi.tv" in domain:
        images = _scrape_vivi_tv(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "times.abema.tv" in domain:
        images = _scrape_abema_tv(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "jisin.jp" in domain:
        images = _scrape_jisin(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "lp.p.pia.jp" in domain:
        images = _scrape_pia(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "news-postseven.com" in domain:
        images = _scrape_postseven(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    selector = next((v for k, v in GALLERY_SITES.items() if k in domain), "article, body")
    referer = f"https://{domain}/"

    skip_kw = ["logo", "/icon", "/icons", "banner", "/ad/", "sprite", "dummy", "blank",
               "noimage", "no-image", "placeholder", "loading",
               "/assets/", "/common/", "_square", "koudoku", "backnumber",
               "convini", "favicon"]
    img_exts = (".jpg", ".jpeg", ".png", ".webp")
    skip_exts = (".gif", ".svg", ".ico", ".bmp", ".webm")

    # 命中专用 selector 时信任容器，不做宽高过滤（缩略图会被 _to_large_url 转换）
    use_specific_selector = any(k in domain for k in GALLERY_SITES)

    try:
        resp = requests.get(gallery_url, headers={**HEADERS, "Referer": referer}, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # 先尝试站点 selector 内的图片，找不到再扫全页
        from urllib.parse import urlparse, urljoin

        def best_src(img_tag) -> str:
            """从 img 标签取最大尺寸图片 URL：优先 srcset 最大项，其次各 data-* 属性"""
            # srcset: "url1 300w, url2 600w, url3 1200w" → 取最大 w
            srcset = img_tag.get("srcset", "")
            if srcset:
                best_url, best_w = "", 0
                for part in srcset.split(","):
                    part = part.strip()
                    tokens = part.split()
                    if not tokens:
                        continue
                    u = tokens[0]
                    w = 0
                    if len(tokens) >= 2:
                        try:
                            w = int(tokens[1].rstrip("wx"))
                        except ValueError:
                            pass
                    if w > best_w:
                        best_w, best_url = w, u
                if best_url:
                    return best_url
            # data-* 候选列表（按优先级）
            for attr in ("data-src", "data-lazy-src", "data-original",
                         "data-full-src", "data-zoom-src", "data-large",
                         "data-hi-res", "data-image", "src"):
                v = img_tag.get(attr, "")
                if v and not v.startswith("data:"):
                    return v
            return ""

        def normalize(src: str) -> str:
            if src.startswith("//"):
                return "https:" + src
            if src.startswith("/"):
                p = urlparse(gallery_url)
                return f"{p.scheme}://{p.netloc}{src}"
            if not src.startswith("http"):
                return urljoin(gallery_url, src)
            return src

        containers = soup.select(selector) or [soup]
        images = []
        seen = set()

        for container in containers:
            for img in container.find_all("img"):
                src = best_src(img)
                if not src:
                    continue
                src = normalize(src)
                if src in seen:
                    continue
                src_lower = src.lower()
                if any(k in src_lower for k in skip_kw):
                    continue
                if any(src_lower.endswith(e) for e in skip_exts):
                    continue
                # 过滤极小图（专用 selector 时跳过，缩略图会被转换为大图）
                if not use_specific_selector:
                    try:
                        if int(img.get("width", 9999)) < 150:
                            continue
                        if int(img.get("height", 9999)) < 150:
                            continue
                    except ValueError:
                        pass
                # 需要是图片 URL
                has_ext = any(src_lower.endswith(e) or (e + "?") in src_lower for e in img_exts)
                has_path = any(p in src_lower for p in ["/photo/", "/image/", "/img/", "/pic/", "/photos/"])
                if not has_ext and not has_path:
                    continue
                src = _to_large_url(src, domain)
                if src in seen:
                    continue
                seen.add(src)
                images.append(src)
                if len(images) >= MAX_IMAGES:
                    break
            if len(images) >= MAX_IMAGES:
                break

        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    except Exception as e:
        print(f"  ⚠️ 抓取图集失败 ({domain}): {e}")
    return []


# ============ Notion Update ============

def update_notion_gallery_url(page_id: str, gallery_url: str):
    """将图集链接写入 Notion 页面的「图集链接」URL 属性"""
    resp = requests.patch(
        f"https://api.notion.com/v1/pages/{page_id}",
        headers=NOTION_HEADERS,
        json={"properties": {"图集链接": {"url": gallery_url}}},
        timeout=10,
    )
    if resp.status_code == 200:
        print(f"  📝 图集链接已写入 Notion")
    else:
        print(f"  ⚠️ Notion 写入失败: {resp.status_code} {resp.text[:100]}")


# ============ Download ============

def _referer_for(image_url: str, gallery_url: str = "") -> str:
    """根据图片 URL 推断下载所需的 Referer（防盗链）"""
    from urllib.parse import urlparse
    img_host = urlparse(image_url).netloc
    # storage.mantan-web.jp → 需要 gravure.mantan-web.jp 作为 Referer
    if "mantan-web.jp" in img_host:
        return "https://gravure.mantan-web.jp/"
    # 其余站点：优先用 gallery_url 的 origin，fallback 到图片自身 origin
    if gallery_url:
        p = urlparse(gallery_url)
        return f"{p.scheme}://{p.netloc}/"
    return f"https://{img_host}/"


def download_images(image_urls: list[str], article_dir: Path,
                    gallery_url: str = "") -> list[str]:
    """下载图片/视频到目录，返回成功的文件名列表。x.com 推文用 yt-dlp 下载。"""
    import subprocess
    import shutil

    ytdlp_bin = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"
    article_dir.mkdir(parents=True, exist_ok=True)
    local_files = []
    for i, url in enumerate(image_urls):
        try:
            # x.com 推文链接 → yt-dlp 下载
            if "x.com/" in url and "/status/" in url:
                out_tmpl = str(article_dir / f"{i + 1:03d}.%(ext)s")
                cmd = [
                    ytdlp_bin, url,
                    "-o", out_tmpl,
                    "--no-playlist",
                    "-S", "vcodec:h264,acodec:aac,ext:mp4,res:1080",
                    "--merge-output-format", "mp4",
                    "--recode-video", "mp4",
                    "--js-runtimes", "node",
                    "--remote-components", "ejs:github",
                ]
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                if result.returncode == 0:
                    mp4_files = sorted(article_dir.glob(f"{i + 1:03d}*.mp4"),
                                       key=lambda f: f.stat().st_mtime, reverse=True)
                    jpg_files = sorted(article_dir.glob(f"{i + 1:03d}*.jpg"),
                                       key=lambda f: f.stat().st_mtime, reverse=True)
                    if mp4_files:
                        f = mp4_files[0]
                        target = article_dir / f"{i + 1:03d}_video.mp4"
                        if f != target:
                            f.rename(target)
                        size = target.stat().st_size // 1024
                        unit = "KB" if size < 1024 else "MB"
                        print(f"    ✓ {target.name}  ({size if size < 1024 else size // 1024} {unit})  yt-dlp")
                        local_files.append(target.name)
                    elif jpg_files:
                        f = jpg_files[0]
                        target = article_dir / f"{i + 1:03d}.jpg"
                        if f != target:
                            f.rename(target)
                        print(f"    ✓ {target.name}  ({target.stat().st_size // 1024} KB)  yt-dlp")
                        local_files.append(target.name)
                else:
                    print(f"    ✗ 推文下载失败: {result.stderr[-200:]}")
                continue

            referer = _referer_for(url, gallery_url)
            is_video = url.endswith(".mp4") or ".mp4?" in url
            timeout = 120 if is_video else 15
            # 代理失败时回退直连重试
            try:
                resp = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=timeout)
            except requests.ConnectionError:
                resp = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=timeout,
                                     proxies={"http": None, "https": None})
            resp.raise_for_status()
            fname = f"{i + 1:03d}_video.mp4" if is_video else f"{i + 1:03d}.jpg"
            fpath = article_dir / fname
            with open(fpath, "wb") as f:
                f.write(resp.content)
            local_files.append(fname)
            size = len(resp.content) // 1024
            unit = "KB" if size < 1024 else "MB"
            print(f"    ✓ {fname}  ({size if size < 1024 else size//1024} {unit})  {url[:70]}")
        except Exception as e:
            print(f"    ✗ 下载失败: {e}")
        time.sleep(0.3)
    return local_files


# ============ Main ============

def process_page(page: dict, redownload: bool = False) -> bool:
    meta = parse_page_meta(page)
    if not meta["link"]:
        return False

    key = meta["key"] or meta["id"]
    article_dir = CACHE_DIR / key

    # 已处理过（有 meta.json）则跳过，除非强制重新下载
    if (article_dir / "meta.json").exists():
        if not redownload:
            print(f"  ⏭ 已缓存，跳过（加 --redownload 可强制重新下载）")
            return False
        # 清除旧图片文件，保留目录
        import shutil
        for f in article_dir.iterdir():
            if f.name != "uploaded.flag":  # 保留上传标记以外的都删除
                f.unlink() if f.is_file() else shutil.rmtree(f)
        print(f"  🔄 强制重新下载")

    # 优先用 Notion 里已填的「图集链接」，没有才从 Yahoo 文章页自动检测
    if meta.get("gallery_url"):
        gallery_url = meta["gallery_url"]
        print(f"  📎 使用 Notion 图集链接: {gallery_url[:70]}")
    else:
        print(f"  🔍 检测图集链接: {meta['link'][:60]}")
        gallery_url = detect_gallery_link(meta["link"])
        if not gallery_url:
            print(f"  — 未找到图集链接")
            return False

    print(f"  📸 图集: {gallery_url}")

    # 若 gallery_url 已是 YouTube 直链，直接提取 video ID
    _youtube_video_id = ""
    import re as _re
    _yt_direct = _re.search(r'(?:youtube\.com/watch\?v=|youtu\.be/)([A-Za-z0-9_-]{11})', gallery_url)
    if _yt_direct:
        _youtube_video_id = _yt_direct.group(1)
        print(f"  🎬 YouTube 直链: {_youtube_video_id}")

    # 纯图集站（页面不含 YouTube/IG 嵌入，跳过检测避免误判）
    _pure_photo_domains = {"crank-in.net", "oricon.co.jp", "mdpr.jp", "modelpress.net",
                           "natalie.mu", "mantan-web.jp", "daily.co.jp",
                           "vivi.tv", "times.abema.tv", "smart-flash.jp", "hochi.news",
                           "sponichi.co.jp", "nikkansports.com", "chunichi.co.jp",
                           "billboard-japan.com", "limo.media", "mezamashi.media",
                           "inside-games.jp", "bookbang.jp", "efight.jp",
                           "maidonanews.jp", "yorozoonews.jp", "nikkan-spa.jp",
                           "animeanime.jp", "mainichikirei.jp", "deview.co.jp",
                           "qjweb.jp", "pinzuba.news", "friday.kodansha.co.jp",
                           "shueisha.online", "entamenext.com", "realsound.jp", "lasisa.net"}
    _is_pure_photo = any(d in gallery_url for d in _pure_photo_domains)
    # /embed/ 页面（如 oricon /embed/photo/）可能嵌入 Instagram，不能跳过检测
    _is_embed_page = "/embed/" in gallery_url

    # 若 gallery_url 不是 Instagram/YouTube 直链，先抓页面检测嵌入内容
    # 已知纯图集站跳过检测，避免把侧栏推荐视频误判为内容
    _is_ig_url = "instagram.com/p/" in gallery_url or "instagram.com/reel/" in gallery_url
    if not _youtube_video_id and not _is_ig_url and "youtube.com" not in gallery_url and (not _is_pure_photo or _is_embed_page):
        try:
            _page_resp = requests.get(gallery_url, headers=HEADERS, timeout=15)
            # 限定在文章正文内检测，避免侧栏广告中的 IG 嵌入被误判
            _page_soup = BeautifulSoup(_page_resp.text, "html.parser")
            _article_body = _page_soup.select_one("article, .newsArticle_body, .article-body, .entry-content, .post-content, .content-main, .cont-news-embed, .single__content")
            _scan_text = str(_article_body) if (_article_body and len(_article_body.get_text(strip=True)) > 100) else _page_resp.text
            _shortcode = _extract_instagram_shortcode(_scan_text)
            if _shortcode:
                gallery_url = f"https://www.instagram.com/p/{_shortcode}/"
                print(f"  📱 检测到 Instagram 嵌入，切换到: {gallery_url}")
            else:
                _youtube_video_id = _extract_youtube_video_id(_scan_text)
                if _youtube_video_id:
                    print(f"  🎬 检测到 YouTube 嵌入: {_youtube_video_id}")
        except Exception as _e:
            print(f"  ⚠️ 嵌入内容检测失败: {_e}")

    # YouTube 视频：仅本地下载，不上传 Cloudinary
    if _youtube_video_id:
        local_files = download_youtube(_youtube_video_id, article_dir,
                                       burn_subtitles=BURN_SUBTITLES)
        if not local_files:
            print(f"  — YouTube 下载失败")
            return False
    # Instagram 帖子
    elif "instagram.com/p/" in gallery_url or "instagram.com/reel/" in gallery_url:
        local_files = download_instagram(gallery_url, article_dir)
        if not local_files:
            print(f"  — Instagram 下载失败")
            return False
    else:
        image_urls = scrape_gallery_images(gallery_url)
        if not image_urls:
            print(f"  — 图集为空")
            return False
        print(f"  下载 {len(image_urls)} 张图片...")
        local_files = download_images(image_urls, article_dir, gallery_url=gallery_url)

    if not local_files:
        return False

    # 写 meta.json
    meta_data = {
        "key": key,
        "notion_page_id": page["id"],
        "title": meta["title"],
        "article_url": meta["link"],
        "gallery_url": gallery_url,
        "images": local_files,
    }
    if _youtube_video_id:
        meta_data["youtube_video_id"] = _youtube_video_id
    # 所有视频文件仅本地保存，不上传 Cloudinary（YouTube / Instagram / Twitter 一致）
    if _youtube_video_id or any(f.endswith("_video.mp4") for f in local_files):
        meta_data["videos_local_only"] = True
    with open(article_dir / "meta.json", "w") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    # 同步写入 Notion
    update_notion_gallery_url(page["id"], gallery_url)
    # SQLite 双写：更新图集/视频字段
    try:
        from sqlite_db import update_news as sqlite_update
        images = [str(article_dir / f) for f in local_files if not f.endswith('_video.mp4')]
        videos = [str(article_dir / f) for f in local_files if f.endswith('_video.mp4')]
        gallery_data = {'gallery_images': images}
        if videos:
            gallery_data['gallery_video'] = videos[0]
        sqlite_update(key, gallery_data)
    except ImportError:
        pass
    print(f"  ✅ 已缓存 {len(local_files)} 张")
    return True


def main():
    parser = argparse.ArgumentParser(description="抓取 Yahoo 文章图集到本地")
    parser.add_argument("--limit", type=int, default=20, help="扫描最近 N 条")
    parser.add_argument("--max-images", type=int, default=None, help="每篇最多下载图片数（默认 20）")
    parser.add_argument("--notion-id", help="指定单条 Notion 页面 ID")
    parser.add_argument("--redownload", action="store_true",
                        help="强制重新下载已缓存的条目（会清除旧图片）")
    parser.add_argument("--no-subtitles", action="store_true",
                        help="跳过 YouTube 视频字幕下载和烧录")
    args = parser.parse_args()

    if args.max_images:
        global MAX_IMAGES
        MAX_IMAGES = args.max_images

    if args.no_subtitles:
        global BURN_SUBTITLES
        BURN_SUBTITLES = False

    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        print("❌ NOTION_API_KEY / NOTION_DATABASE_ID 未设置")
        sys.exit(1)

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"📂 缓存目录: {CACHE_DIR}")
    print("=" * 60)

    if args.notion_id:
        pages = [get_notion_page(args.notion_id)]
    else:
        pages = get_notion_pages(args.limit)

    print(f"共 {len(pages)} 条待检测\n")
    found = 0
    for i, page in enumerate(pages, 1):
        meta = parse_page_meta(page)
        print(f"[{i}/{len(pages)}] {meta['title'][:40]}")
        if process_page(page, redownload=args.redownload):
            found += 1
        print()

    print("=" * 60)
    print(f"完成！新增图集 {found} 个，可运行 gallery_preview.py 预览")


if __name__ == "__main__":
    main()
