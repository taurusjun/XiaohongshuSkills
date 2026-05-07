#!/usr/bin/env python3
"""
统一媒体下载器：输入 URL + 本地存储路径，自动识别 YouTube / Instagram / Twitter / 直链并下载。

用法：
    # CLI
    python unified_media_downloader.py "https://www.youtube.com/watch?v=XXXXX"
    python unified_media_downloader.py "https://www.instagram.com/p/XXXXX/"
    python unified_media_downloader.py "https://x.com/user/status/XXXXX" -o ./downloads

    # 模块导入
    from unified_media_downloader import download_media
    files = download_media("https://youtube.com/watch?v=abc", output_dir="/tmp/videos")
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

try:
    from dotenv import load_dotenv
    _script_dir = Path(__file__).parent
    _env_file = _script_dir / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except ImportError:
    pass

# ── 常量 ──────────────────────────────────────────────────────────
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "media_downloads"
MAX_FILES = 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}
CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
YTDLP_BIN = shutil.which("yt-dlp") or "/opt/homebrew/bin/yt-dlp"


# ================================================================
# URL 识别
# ================================================================

def detect_url_type(url: str) -> str:
    """返回 'youtube' | 'instagram' | 'twitter' | 'direct_video' | 'direct_image' | 'unknown'"""
    url_lower = url.lower()

    if re.search(r'(youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/|youtube\.com/shorts/)', url_lower):
        return "youtube"
    if re.search(r'instagram\.com/(p|reel)/', url_lower):
        return "instagram"
    if re.search(r'(x\.com|twitter\.com)/\w+/status/', url_lower):
        return "twitter"

    parsed_path = urlparse(url).path.lower()
    if any(parsed_path.endswith(ext) for ext in (".mp4", ".mov", ".webm", ".mkv", ".avi")):
        return "direct_video"
    if any(parsed_path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp")):
        return "direct_image"

    return "unknown"


def extract_youtube_id(url: str) -> str:
    m = re.search(r'(?:v=|youtu\.be/|/embed/|/shorts/)([A-Za-z0-9_-]{11})', url)
    return m.group(1) if m else ""


def extract_instagram_shortcode(url: str) -> str:
    m = re.search(r'instagram\.com/(?:p|reel)/([A-Za-z0-9_-]+)/?', url)
    return m.group(1) if m else ""


def extract_tweet_id(url: str) -> str:
    m = re.search(r'(?:x\.com|twitter\.com)/\w+/status/(\d+)', url)
    return m.group(1) if m else ""


# ================================================================
# CDP Cookie 提取（共享）
# ================================================================

def _extract_cookies_from_cdp(domain_filter: str = "",
                               cdp_host: str = CDP_HOST,
                               cdp_port: int = CDP_PORT) -> dict:
    """从 Chrome CDP 提取指定域名的 cookies，返回 {name: value} 字典。"""
    try:
        import websocket
    except ImportError:
        print("  ⚠️ 需要安装: pip install websocket-client")
        return {}

    try:
        tabs = requests.get(f"http://{cdp_host}:{cdp_port}/json", timeout=5).json()
    except Exception as e:
        print(f"  ⚠️ 无法连接 Chrome CDP: {e}")
        return {}

    page_tab = next((t for t in tabs if t.get("type") == "page"), None)
    if not page_tab:
        print("  ⚠️ 找不到可用的 page tab")
        return {}

    cookies = {}
    try:
        ws = websocket.create_connection(page_tab["webSocketDebuggerUrl"], timeout=10)
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                for c in msg.get("result", {}).get("cookies", []):
                    if not domain_filter or domain_filter in c.get("domain", ""):
                        cookies[c["name"]] = c["value"]
                break
        ws.close()
    except Exception as e:
        print(f"  ⚠️ Cookie 提取失败: {e}")

    return cookies


def _cookies_to_netscape(cookies: dict, domain_filter: str = "") -> str:
    """从 CDP 提取需域名的 cookies 并格式化为 Netscape 格式字符串。"""
    all_cookies = _get_all_cookies_raw()
    if not all_cookies:
        return ""

    filtered = [c for c in all_cookies
                if not domain_filter or any(d in c.get("domain", "") for d in domain_filter)]

    lines = ["# Netscape HTTP Cookie File"]
    for c in filtered:
        domain = c["domain"]
        flag = "TRUE" if domain.startswith(".") else "FALSE"
        expires = int(c.get("expires", 0))
        if expires < 0:
            expires = 0
        lines.append(
            f"{domain}\t{flag}\t{c.get('path', '/')}\t"
            f"{'TRUE' if c.get('secure') else 'FALSE'}\t{expires}\t"
            f"{c['name']}\t{c['value']}"
        )
    return "\n".join(lines)


def _get_all_cookies_raw() -> list[dict]:
    """从 CDP 获取所有 cookies 原始列表。"""
    try:
        import websocket
    except ImportError:
        return []

    try:
        tabs = requests.get(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5).json()
        page_tab = next((t for t in tabs if t.get("type") == "page"), None)
        if not page_tab:
            return []

        ws = websocket.create_connection(page_tab["webSocketDebuggerUrl"], timeout=10)
        ws.send(json.dumps({"id": 1, "method": "Network.getAllCookies"}))
        all_cookies = []
        while True:
            msg = json.loads(ws.recv())
            if msg.get("id") == 1:
                all_cookies = msg.get("result", {}).get("cookies", [])
                break
        ws.close()
        return all_cookies
    except Exception:
        return []


# ================================================================
# YouTube
# ================================================================

def _trim_black_start(fpath: Path) -> tuple[Path, float]:
    """用 ffmpeg blackdetect 检测并裁掉开头黑屏。返回 (文件路径, 裁剪秒数)。"""
    detect = subprocess.run(
        ["ffmpeg", "-i", str(fpath), "-vf", "blackdetect=d=0.1:pix_th=0.10",
         "-f", "null", "-"],
        capture_output=True, text=True,
    )
    m = re.search(r"black_end:([\d.]+)", detect.stderr)
    if not m:
        return fpath, 0.0
    black_end = float(m.group(1))
    if black_end < 0.5:
        return fpath, 0.0

    print(f"  ✂️ 裁剪开头黑屏 {black_end:.1f}s")
    tmp = fpath.with_suffix(".trimmed.mp4")
    r = subprocess.run(
        ["ffmpeg", "-ss", str(black_end), "-i", str(fpath), "-c", "copy", str(tmp), "-y"],
        capture_output=True,
    )
    if r.returncode == 0 and tmp.exists() and tmp.stat().st_size > 0:
        fpath.unlink()
        tmp.rename(fpath)
    elif tmp.exists():
        tmp.unlink()
    return fpath, black_end


def _shift_vtt(src: Path, dst: Path, offset_sec: float) -> None:
    """将 VTT 字幕所有时间戳向前移 offset_sec 秒。"""
    def _shift_ts(ts: str, delta: float) -> str:
        parts = ts.strip().replace(",", ".").split(":")
        if len(parts) == 2:
            h, ms = 0, float(parts[0]) * 60 + float(parts[1])
        else:
            h, ms = int(parts[0]), float(parts[1]) * 60 + float(parts[2])
        total = max(0.0, h * 3600 + ms - delta)
        hh = int(total // 3600)
        mm = int((total % 3600) // 60)
        ss = total % 60
        return f"{hh:02d}:{mm:02d}:{ss:06.3f}"

    text = src.read_text(encoding="utf-8", errors="replace")
    def _replace(m):
        return f"{_shift_ts(m.group(1), offset_sec)} --> {_shift_ts(m.group(2), offset_sec)}"
    text = re.sub(r"([\d:,.]+)\s*-->\s*([\d:,.]+)", _replace, text)
    dst.write_text(text, encoding="utf-8")


def _burn_bilingual_subtitles(fpath: Path, article_dir: Path,
                               trim_offset: float = 0.0) -> Path:
    """烧录 ja + zh-Hans 双语字幕。中文底部，日文上方。"""
    ja_files = sorted(article_dir.glob("*.ja.vtt")) + sorted(article_dir.glob("*.ja-orig.vtt"))
    zh_files = sorted(article_dir.glob("*.zh-Hans.vtt")) + sorted(article_dir.glob("*.zh_Hans.vtt"))

    if not ja_files and not zh_files:
        print("  ⚠️ 未找到字幕文件，跳过烧录")
        return fpath

    labels = []
    if ja_files: labels.append("ja")
    if zh_files: labels.append("zh")
    print(f"  📝 烧录字幕: {'+'.join(labels)}"
          + (f"（时间轴偏移 -{trim_offset:.1f}s）" if trim_offset else ""))

    tmp_dir = Path(tempfile.mkdtemp())
    tmp_zh = tmp_dir / "sub_zh.vtt"
    tmp_ja = tmp_dir / "sub_ja.vtt"
    if zh_files:
        _shift_vtt(zh_files[0], tmp_zh, trim_offset)
    if ja_files:
        _shift_vtt(ja_files[0], tmp_ja, trim_offset)

    base = "FontSize=18,BorderStyle=3,Outline=1,Shadow=0,Bold=1"
    vf_parts = []
    if zh_files:
        vf_parts.append(
            f"subtitles=filename={tmp_zh}:force_style='{base},"
            f"PrimaryColour=&H00ffff,OutlineColour=&H40000000,Alignment=2,MarginV=20'"
        )
    if ja_files:
        vf_parts.append(
            f"subtitles=filename={tmp_ja}:force_style='{base},"
            f"PrimaryColour=&Hffffff,OutlineColour=&H40000000,Alignment=2,MarginV=60'"
        )

    out_path = fpath.with_suffix(".subbed.mp4")
    r = subprocess.run(
        ["ffmpeg", "-i", str(fpath),
         "-vf", ",".join(vf_parts),
         "-c:v", "libx264", "-crf", "23", "-preset", "fast",
         "-c:a", "copy", str(out_path), "-y"],
        capture_output=True, timeout=900,
    )
    shutil.rmtree(tmp_dir, ignore_errors=True)
    if r.returncode == 0 and out_path.exists() and out_path.stat().st_size > 0:
        fpath.unlink()
        out_path.rename(fpath)
        print("  ✅ 字幕烧录完成")
        for f in ja_files + zh_files:
            try: f.unlink()
            except Exception: pass
    else:
        if out_path.exists():
            out_path.unlink()
        print(f"  ⚠️ 字幕烧录失败，保留原视频\n"
              f"{r.stderr[-300:].decode(errors='ignore') if r.stderr else ''}")
    return fpath


def download_youtube(url_or_id: str, output_dir: Path, *,
                     burn_subtitles: bool = False,
                     trim_black_start: bool = False,
                     cdp_host: str = CDP_HOST,
                     cdp_port: int = CDP_PORT,
                     timeout: int = 300) -> list[str]:
    """下载 YouTube 视频。返回 ['001_video.mp4'] 或 []。"""
    video_id = extract_youtube_id(url_or_id) or url_or_id

    # 提取 cookies 并写入 Netscape 格式临时文件
    cookie_file = Path(tempfile.mktemp(suffix=".txt"))
    try:
        netscape = _cookies_to_netscape(["youtube.com", "google.com"])
        if netscape:
            cookie_file.write_text(netscape)
    except Exception as e:
        print(f"  ⚠️ 提取 YouTube cookies 失败: {e}")

    output_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(output_dir / "%(title).50s.%(ext)s")
    cmd = [
        YTDLP_BIN,
        f"https://www.youtube.com/watch?v={video_id}",
        "-o", out_tmpl,
        "--no-playlist",
        "-S", "vcodec:h264,acodec:aac,ext:mp4,res:1080",
        "--merge-output-format", "mp4",
        "--recode-video", "mp4",
        *(["--write-auto-sub", "--sub-langs", "ja,zh-Hans",
           "--sub-format", "vtt/best"] if burn_subtitles else []),
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
    ]
    if cookie_file.exists() and cookie_file.stat().st_size > 30:
        cmd += ["--cookies", str(cookie_file)]

    print(f"  ▶ yt-dlp 下载 YouTube: {video_id}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if result.returncode != 0:
            print(f"  ❌ yt-dlp 失败: {result.stderr[-300:]}")
            return []
    except subprocess.TimeoutExpired:
        print("  ❌ yt-dlp 超时")
        return []
    finally:
        if cookie_file.exists():
            cookie_file.unlink()

    # 找下载的 mp4
    mp4_files = sorted(output_dir.glob("*.mp4"),
                        key=lambda f: f.stat().st_mtime, reverse=True)
    if not mp4_files:
        all_videos = sorted(
            [f for f in output_dir.iterdir() if f.suffix in (".webm", ".mkv", ".m4v")],
            key=lambda f: f.stat().st_mtime, reverse=True,
        )
        if all_videos:
            new_name = output_dir / (all_videos[0].stem + ".mp4")
            all_videos[0].rename(new_name)
            mp4_files = [new_name]

    if not mp4_files:
        print("  ❌ 未找到下载的视频文件")
        return []

    fpath = mp4_files[0]
    if trim_black_start:
        fpath, trim_offset = _trim_black_start(fpath)
    else:
        trim_offset = 0.0

    if burn_subtitles:
        fpath = _burn_bilingual_subtitles(fpath, output_dir, trim_offset=trim_offset)

    # 统一命名
    target = output_dir / "001_video.mp4"
    if fpath != target:
        if target.exists():
            target.unlink()
        fpath.rename(target)
        fpath = target

    size_mb = fpath.stat().st_size // (1024 * 1024)
    print(f"  ✅ YouTube 视频已保存: {fpath.name} ({size_mb} MB)")
    return [fpath.name]


# ================================================================
# Instagram
# ================================================================

INSTAGRAM_JS = r"""
(function(){
    var code = '__SHORTCODE__';
    var seen = {};
    var results = [];

    function bestImg(obj) {
        if (!obj.image_versions2) return '';
        var cands = obj.image_versions2.candidates || [];
        var best = cands.reduce(function(a,b){ return (b.width||0)>(a.width||0)?b:a; }, cands[0]||{});
        return best.url || '';
    }
    function bestVid(obj) {
        if (obj.video_url) return obj.video_url;
        var vers = obj.video_versions || [];
        return vers.length ? vers[0].url : '';
    }
    function addMedia(obj) {
        var url = bestVid(obj) || bestImg(obj);
        if (!url || seen[url]) return;
        var idMatch = url.match(/\/(\d{5,})_/);
        var deduKey = idMatch ? idMatch[1] : url;
        if (seen[deduKey]) return;
        seen[deduKey] = 1;
        results.push({url: url, type: bestVid(obj) ? 'video' : 'img'});
    }
    function walk(obj, depth) {
        if (!obj || typeof obj !== 'object' || depth > 20) return;
        if (obj.code === code) {
            addMedia(obj);
            (obj.carousel_media || []).forEach(addMedia);
            return;
        }
        if (Array.isArray(obj)) { obj.forEach(function(i){ walk(i, depth+1); }); }
        else { Object.values(obj).forEach(function(v){ walk(v, depth+1); }); }
    }
    document.querySelectorAll('script[type="application/json"]').forEach(function(s){
        if (s.textContent.indexOf(code) === -1) return;
        try { walk(JSON.parse(s.textContent), 0); } catch(e){}
    });
    return JSON.stringify(results);
})()
"""


def _get_ig_tab(tab_list: list[dict]) -> dict | None:
    t = next((t for t in tab_list
              if t.get("type") == "page" and "instagram.com" in t.get("url", "")), None)
    return t or next((t for t in tab_list if t.get("type") == "page"), None)


def _extract_instagram_media_cdp(gallery_url: str,
                                  cdp_host: str, cdp_port: int) -> list[dict]:
    """通过 CDP 导航到 Instagram 帖子并提取媒体 URL。返回 [{url, type}]。"""
    shortcode = extract_instagram_shortcode(gallery_url)
    if not shortcode:
        print(f"  ⚠️ 无法解析 Instagram shortcode: {gallery_url}")
        return []

    js_extract = INSTAGRAM_JS.replace("__SHORTCODE__", shortcode)

    try:
        import websocket
    except ImportError:
        print("  ⚠️ 需要安装: pip install websocket-client")
        return []

    try:
        tabs = requests.get(f"http://{cdp_host}:{cdp_port}/json", timeout=5).json()
    except Exception as e:
        print(f"  ⚠️ 无法连接 Chrome CDP: {e}")
        return []

    page_tab = _get_ig_tab(tabs)
    if not page_tab:
        print("  ⚠️ 找不到可用的 Chrome tab")
        return []

    # Phase 1: 导航
    try:
        ws = websocket.create_connection(page_tab["webSocketDebuggerUrl"], timeout=15)
        ws.send(json.dumps({"id": 1, "method": "Page.enable"}))
        ws.recv()
        ws.send(json.dumps({"id": 2, "method": "Page.navigate",
                            "params": {"url": gallery_url}}))
        print(f"  🌐 导航到 Instagram: {gallery_url}")
        start = time.time()
        while time.time() - start < 20:
            try:
                msg = json.loads(ws.recv())
                if msg.get("method") == "Page.loadEventFired":
                    break
            except Exception:
                break
        try:
            ws.close()
        except Exception:
            pass
    except Exception as e:
        print(f"  ⚠️ CDP 导航失败: {e}")
        return []

    # Phase 2: 重连 + 提取
    time.sleep(3)
    try:
        tabs2 = requests.get(f"http://{cdp_host}:{cdp_port}/json", timeout=5).json()
        tab2 = _get_ig_tab(tabs2)
        if not tab2:
            print("  ⚠️ 导航后找不到 tab")
            return []

        ws2 = websocket.create_connection(tab2["webSocketDebuggerUrl"], timeout=15)
        ws2.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                             "params": {"expression": js_extract}}))
        media_items = []
        while True:
            msg = json.loads(ws2.recv())
            if msg.get("id") == 1:
                val = msg.get("result", {}).get("result", {}).get("value", "[]")
                media_items = json.loads(val) if val else []
                break
        ws2.close()
    except Exception as e:
        print(f"  ⚠️ CDP 提取失败: {e}")
        return []

    return media_items


def _download_instagram_ytdlp(gallery_url: str, output_dir: Path,
                               timeout: int = 300) -> list[str]:
    """yt-dlp 备用方案下载 Instagram。"""
    netscape = _cookies_to_netscape(["instagram.com"])
    cookie_file = None
    if netscape:
        cookie_file = Path(tempfile.mktemp(suffix=".txt"))
        cookie_file.write_text(netscape)

    output_dir.mkdir(parents=True, exist_ok=True)
    out_tmpl = str(output_dir / "%(id)s.%(ext)s")
    cmd = [
        YTDLP_BIN, gallery_url,
        "-o", out_tmpl,
        "--no-playlist",
        "--merge-output-format", "mp4",
        "--recode-video", "mp4",
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
    ]
    if cookie_file and cookie_file.stat().st_size > 30:
        cmd += ["--cookies", str(cookie_file)]

    print(f"  ▶ yt-dlp 备用下载 Instagram...")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("  ❌ yt-dlp 超时")
        return []
    finally:
        if cookie_file:
            try: cookie_file.unlink()
            except Exception: pass

    if result.returncode != 0:
        print(f"  ❌ yt-dlp 失败: {result.stderr[-300:]}")
        return []

    # 找到下载的文件并重命名为统一格式
    saved = []
    for idx, f in enumerate(sorted(output_dir.iterdir(),
                                    key=lambda x: x.stat().st_mtime), 1):
        if f.suffix.lower() == ".mp4":
            target = output_dir / f"{idx:03d}_video.mp4"
        elif f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            target = output_dir / f"{idx:03d}.jpg"
        else:
            continue
        if f != target:
            f.rename(target)
        saved.append(target.name)
        print(f"    ✓ {target.name}  (yt-dlp)")

    return saved


def download_instagram(url: str, output_dir: Path, *,
                       cdp_host: str = CDP_HOST,
                       cdp_port: int = CDP_PORT,
                       use_ytdlp_fallback: bool = True,
                       timeout: int = 300) -> list[str]:
    """下载 Instagram 帖子。返回 ['001.jpg', '002_video.mp4', ...] 或 []。"""
    # 主方案：CDP
    media_items = _extract_instagram_media_cdp(url, cdp_host, cdp_port)

    if not media_items:
        if use_ytdlp_fallback:
            print("  ⚠️ CDP 提取失败，尝试 yt-dlp 备用方案...")
            return _download_instagram_ytdlp(url, output_dir, timeout)
        print("  ⚠️ 未找到 Instagram 媒体")
        return []

    n_img = sum(1 for x in media_items if x["type"] == "img")
    n_vid = sum(1 for x in media_items if x["type"] == "video")
    print(f"  📦 找到 {n_img} 张图 + {n_vid} 个视频")

    # 下载
    output_dir.mkdir(parents=True, exist_ok=True)
    ig_cookies = _extract_cookies_from_cdp("instagram.com", cdp_host, cdp_port)
    dl_headers = {
        **HEADERS,
        "Cookie": "; ".join(f"{k}={v}" for k, v in ig_cookies.items()),
        "Referer": "https://www.instagram.com/",
    }

    saved = []
    img_idx = 1
    for item in media_items:
        murl, mtype = item["url"], item["type"]
        try:
            resp = requests.get(murl, headers=dl_headers, timeout=30)
            resp.raise_for_status()
            fname = (f"{img_idx:03d}_video.mp4" if mtype == "video"
                     else f"{img_idx:03d}.jpg")
            (output_dir / fname).write_bytes(resp.content)
            print(f"    ✓ {fname}  ({len(resp.content) // 1024} KB)")
            saved.append(fname)
            img_idx += 1
        except Exception as e:
            print(f"    ✗ 下载失败: {e}")
        time.sleep(0.2)

    return saved


# ================================================================
# Twitter / X
# ================================================================

def download_twitter(url: str, output_dir: Path, *,
                     timeout: int = 300) -> list[str]:
    """用 yt-dlp 下载 x.com 推文的视频/图片。返回 ['001_video.mp4', ...] 或 []。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    tweet_id = extract_tweet_id(url)
    out_tmpl = str(output_dir / f"{tweet_id}.%(ext)s" if tweet_id
                   else str(output_dir / "%(id)s.%(ext)s"))
    cmd = [
        YTDLP_BIN, url,
        "-o", out_tmpl,
        "--no-playlist",
        "-S", "vcodec:h264,acodec:aac,ext:mp4,res:1080",
        "--merge-output-format", "mp4",
        "--recode-video", "mp4",
        "--js-runtimes", "node",
        "--remote-components", "ejs:github",
    ]
    print(f"  ▶ yt-dlp 下载推文: {tweet_id}")
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        print("  ✗ yt-dlp 超时")
        return []

    if result.returncode != 0:
        print(f"  ❌ yt-dlp 失败: {result.stderr[-300:]}")
        return []

    # 找到下载的文件并重命名为统一格式
    mp4_files = sorted(output_dir.glob("*.mp4"),
                        key=lambda f: f.stat().st_mtime, reverse=True)
    jpg_files = sorted(
        [f for f in output_dir.iterdir() if f.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")],
        key=lambda f: f.stat().st_mtime, reverse=True,
    )

    saved = []
    idx = 1
    for f in mp4_files:
        target = output_dir / f"{idx:03d}_video.mp4"
        if f != target:
            f.rename(target)
        saved.append(target.name)
        size = target.stat().st_size // 1024
        unit = "KB" if size < 1024 else "MB"
        print(f"    ✓ {target.name}  ({size if size < 1024 else size // 1024} {unit})  yt-dlp")
        idx += 1
    for f in jpg_files:
        target = output_dir / f"{idx:03d}.jpg"
        if f != target:
            f.rename(target)
        saved.append(target.name)
        print(f"    ✓ {target.name}  ({target.stat().st_size // 1024} KB)  yt-dlp")
        idx += 1

    return saved


# ================================================================
# 直链下载
# ================================================================

def download_direct(url: str, output_dir: Path, *,
                    timeout: int = 120) -> list[str]:
    """下载直链图片/视频文件。返回 ['001.jpg'] 或 ['001_video.mp4'] 或 []。"""
    output_dir.mkdir(parents=True, exist_ok=True)
    is_video = url.endswith(".mp4") or ".mp4?" in url or "/video.twimg.com/" in url
    t = 120 if is_video else 15

    try:
        parsed = urlparse(url)
        referer = f"{parsed.scheme}://{parsed.netloc}/"
        resp = requests.get(url, headers={**HEADERS, "Referer": referer}, timeout=t)
        resp.raise_for_status()
        fname = "001_video.mp4" if is_video else "001.jpg"
        (output_dir / fname).write_bytes(resp.content)
        size = len(resp.content) // 1024
        unit = "KB" if size < 1024 else "MB"
        print(f"    ✓ {fname}  ({size if size < 1024 else size // 1024} {unit})")
        return [fname]
    except Exception as e:
        print(f"    ✗ 直链下载失败: {e}")
        return []


# ================================================================
# 公共入口
# ================================================================

def download_media(url: str, output_dir: str | Path | None = None, *,
                   cdp_host: str = CDP_HOST,
                   cdp_port: int = CDP_PORT,
                   burn_subtitles: bool = False,
                   trim_black_start: bool = False,
                   max_files: int = MAX_FILES,
                   timeout: int = 300) -> list[str]:
    """
    自动识别 URL 类型并下载所有媒体。

    Args:
        url: YouTube / Instagram / Twitter / 直链地址
        output_dir: 目标目录（默认 ~/.cache/media_downloads/<host>_<id>/）
        burn_subtitles: YouTube 双语字幕烧录
        trim_black_start: YouTube 黑屏裁剪
        max_files: 最多下载文件数
        timeout: 下载超时秒数

    Returns:
        本地文件名列表，如 ['001.jpg', '002_video.mp4']
    """
    url_type = detect_url_type(url)
    print(f"🔍 URL 类型: {url_type}  →  {url[:80]}")

    # 默认输出目录
    if output_dir is None:
        url_id = ""
        if url_type == "youtube":
            url_id = extract_youtube_id(url)
        elif url_type == "instagram":
            url_id = extract_instagram_shortcode(url)
        elif url_type == "twitter":
            url_id = extract_tweet_id(url)
        else:
            url_id = Path(urlparse(url).path).stem
        output_dir = DEFAULT_CACHE_DIR / f"{url_type}_{url_id}"
    else:
        output_dir = Path(output_dir)

    print(f"📂 输出目录: {output_dir}")
    output_dir = Path(output_dir)

    # 分发
    if url_type == "youtube":
        files = download_youtube(url, output_dir,
                                 burn_subtitles=burn_subtitles,
                                 trim_black_start=trim_black_start,
                                 cdp_host=cdp_host, cdp_port=cdp_port,
                                 timeout=timeout)
    elif url_type == "instagram":
        files = download_instagram(url, output_dir,
                                   cdp_host=cdp_host, cdp_port=cdp_port,
                                   timeout=timeout)
    elif url_type == "twitter":
        files = download_twitter(url, output_dir, timeout=timeout)
    elif url_type in ("direct_video", "direct_image"):
        files = download_direct(url, output_dir, timeout=timeout)
    else:
        print(f"❌ 无法识别 URL 类型: {url}")
        return []

    files = files[:max_files]
    print(f"✅ 完成: {len(files)} 个文件")
    for f in files:
        print(f"   {output_dir / f}")
    return files


# ================================================================
# CLI
# ================================================================

def main():
    parser = argparse.ArgumentParser(description="统一媒体下载器")
    parser.add_argument("url", help="媒体 URL")
    parser.add_argument("-o", "--output", help="输出目录")
    parser.add_argument("--cdp-host", default=CDP_HOST)
    parser.add_argument("--cdp-port", type=int, default=CDP_PORT)
    parser.add_argument("--burn-subtitles", action="store_true",
                        help="YouTube 双语字幕烧录")
    parser.add_argument("--trim-black-start", action="store_true",
                        help="YouTube 开头黑屏裁剪")
    parser.add_argument("--max-files", type=int, default=MAX_FILES)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--no-cdp", action="store_true",
                        help="Instagram 跳过 CDP，直接用 yt-dlp")
    parser.add_argument("--no-subtitles", action="store_true")
    parser.add_argument("--no-trim", action="store_true")
    args = parser.parse_args()

    files = download_media(
        args.url,
        output_dir=args.output,
        cdp_host=args.cdp_host,
        cdp_port=args.cdp_port,
        burn_subtitles=args.burn_subtitles and not args.no_subtitles,
        trim_black_start=args.trim_black_start and not args.no_trim,
        max_files=args.max_files,
        timeout=args.timeout,
    )

    if not files:
        sys.exit(1)


if __name__ == "__main__":
    main()
