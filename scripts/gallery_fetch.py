#!/usr/bin/env python3
"""
从 Notion 日本新闻条目中，检测 Yahoo 文章包含的 mdpr.jp 图集链接，
下载图片到本地缓存 ~/.cache/xhs_images/<key>/

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
MAX_IMAGES = 9
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

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
}

# URL に含まれる「図集っぽい」キーワード（なければ外部リンク全体を対象）
GALLERY_URL_HINTS = ["photo", "picture", "gallery", "image", "img", "pic", "slide"]


# ============ Notion ============

def get_notion_pages(limit: int = 20) -> list:
    """获取勾选了「图集下载」的条目"""
    resp = requests.post(
        f"https://api.notion.com/v1/databases/{NOTION_DATABASE_ID}/query",
        headers=NOTION_HEADERS,
        json={
            "filter": {"property": "图集下载", "checkbox": {"equals": True}},
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
    }


# ============ Gallery Detection ============

def _domain_of(url: str) -> str:
    from urllib.parse import urlparse
    return urlparse(url).netloc.lstrip("www.")


def _is_valid_gallery_url(url: str) -> bool:
    """URL 必须有实际路径（排除首页、纯域名链接）"""
    from urllib.parse import urlparse
    path = urlparse(url).path.rstrip("/")
    # 路径为空或只有一级且很短（如 /news）也排除
    parts = [p for p in path.split("/") if p]
    return len(parts) >= 2


def detect_gallery_link(article_url: str) -> str:
    """从 Yahoo 文章页找已知图集站点的外链，URL 必须含图集关键词且有实际路径"""
    if not article_url:
        return ""
    try:
        resp = requests.get(article_url, headers=HEADERS, timeout=15)
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href.startswith("http"):
                continue
            domain = _domain_of(href)
            matched = next((k for k in GALLERY_SITES if k in domain), None)
            if not matched:
                continue
            # 必须含图集关键词 且 有实际路径深度
            href_lower = href.lower()
            if any(hint in href_lower for hint in GALLERY_URL_HINTS) and _is_valid_gallery_url(href):
                print(f"  🔗 找到图集外链 ({domain}): {href[:70]}")
                return href
    except Exception as e:
        print(f"  ⚠️ 检测图集链接失败: {e}")
    return ""


def _scrape_oricon(gallery_url: str) -> list[str]:
    """oricon.co.jp 分页图集：逐页抓 div.main_photo img"""
    import re
    from urllib.parse import urlparse
    headers = {**HEADERS, "Referer": "https://www.oricon.co.jp/"}

    # 从当前页获取所有分页链接，取最大页码
    resp = requests.get(gallery_url, headers=headers, timeout=15)
    soup = BeautifulSoup(resp.text, "html.parser")
    p = urlparse(gallery_url)
    base_origin = f"{p.scheme}://{p.netloc}"

    # 用 path（不含 query）找分页
    base_path = re.sub(r'/photo/\d+/?$', '/photo/', p.path)
    page_nums = set()
    for a in soup.find_all("a", href=True):
        m = re.search(r'/photo/(\d+)/?$', a["href"])
        if m and base_path.split("/photo/")[0] in a["href"]:
            page_nums.add(int(m.group(1)))

    total = max(page_nums) if page_nums else 1
    total = min(total, MAX_IMAGES)
    images = []

    for page in range(1, total + 1):
        try:
            url = f"{base_origin}{base_path}{page}/"
            r = requests.get(url, headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")
            img = s.select_one("div.main_photo img, .main_photo_image img")
            if img:
                src = img.get("src", "")
                if src:
                    if src.startswith("/"):
                        src = base_origin + src
                    images.append(src)
        except Exception as e:
            print(f"  ⚠️ oricon page {page} 失败: {e}")
        time.sleep(0.3)

    return images


def _scrape_crank_in(gallery_url: str) -> list[str]:
    """crank-in.net 每张图独立分页，遍历所有页抓主图"""
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
            r = requests.get(f"{base}/{page}", headers=headers, timeout=15)
            s = BeautifulSoup(r.text, "html.parser")
            img = s.select_one(".photo-link-img img")
            if img:
                src = img.get("src", "")
                if src and not src.startswith("data:"):
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        p = urlparse(gallery_url)
                        src = f"{p.scheme}://{p.netloc}{src}"
                    # 转大图
                    src = re.sub(r'_\d+\.jpg', '_1200.jpg', src)
                    images.append(src)
        except Exception as e:
            print(f"  ⚠️ crank-in page {page} 失败: {e}")
        time.sleep(0.3)

    return images


def _to_large_url(src: str, domain: str) -> str:
    """将缩略图 URL 转换为大图 URL（站点专用规则）"""
    if "oricon.co.jp" in domain:
        return src.replace("_p_s_", "_p_o_")
    if "crank-in.net" in domain:
        import re
        return re.sub(r'_\d+\.jpg', '_1200.jpg', src)
    return src


def scrape_gallery_images(gallery_url: str) -> list[str]:
    """通用图集抓取：先用站点 CSS selector 定位容器，再提取大图"""
    domain = _domain_of(gallery_url)

    # 站点专用抓取器（分页图集）
    if "oricon.co.jp" in domain:
        images = _scrape_oricon(gallery_url)
        print(f"  📷 抓到 {len(images)} 张图片")
        return images
    if "crank-in.net" in domain:
        images = _scrape_crank_in(gallery_url)
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

def download_images(image_urls: list[str], article_dir: Path) -> list[str]:
    """下载图片到目录，返回成功的文件名列表"""
    article_dir.mkdir(parents=True, exist_ok=True)
    local_files = []
    for i, url in enumerate(image_urls):
        try:
            resp = requests.get(url, headers={**HEADERS, "Referer": "https://mdpr.jp/"}, timeout=15)
            resp.raise_for_status()
            fname = f"{i + 1:03d}.jpg"
            fpath = article_dir / fname
            with open(fpath, "wb") as f:
                f.write(resp.content)
            local_files.append(fname)
            print(f"    ✓ {fname}  ({len(resp.content) // 1024} KB)  {url[:70]}")
        except Exception as e:
            print(f"    ✗ 下载失败: {e}")
        time.sleep(0.3)
    return local_files


# ============ Main ============

def process_page(page: dict) -> bool:
    meta = parse_page_meta(page)
    if not meta["link"]:
        return False

    key = meta["key"] or meta["id"]
    article_dir = CACHE_DIR / key

    # 已处理过（有 meta.json）则跳过
    if (article_dir / "meta.json").exists():
        print(f"  ⏭ 已缓存，跳过")
        return False

    print(f"  🔍 检测图集链接: {meta['link'][:60]}")
    gallery_url = detect_gallery_link(meta["link"])
    if not gallery_url:
        print(f"  — 未找到图集链接")
        return False

    print(f"  📸 图集: {gallery_url}")
    image_urls = scrape_gallery_images(gallery_url)
    if not image_urls:
        print(f"  — 图集为空")
        return False

    print(f"  下载 {len(image_urls)} 张图片...")
    local_files = download_images(image_urls, article_dir)
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
    with open(article_dir / "meta.json", "w") as f:
        json.dump(meta_data, f, ensure_ascii=False, indent=2)

    # 同步写入 Notion
    update_notion_gallery_url(page["id"], gallery_url)
    print(f"  ✅ 已缓存 {len(local_files)} 张")
    return True


def main():
    parser = argparse.ArgumentParser(description="抓取 Yahoo 文章图集到本地")
    parser.add_argument("--limit", type=int, default=20, help="扫描最近 N 条")
    parser.add_argument("--max-images", type=int, default=None, help="每篇最多下载图片数（默认 9）")
    parser.add_argument("--notion-id", help="指定单条 Notion 页面 ID")
    args = parser.parse_args()

    if args.max_images:
        global MAX_IMAGES
        MAX_IMAGES = args.max_images

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
        if process_page(page):
            found += 1
        print()

    print("=" * 60)
    print(f"完成！新增图集 {found} 个，可运行 gallery_preview.py 预览")


if __name__ == "__main__":
    main()
