"""
Unified publish pipeline for Xiaohongshu.

Single CLI entry point that orchestrates:
  chrome_launcher → login check → image/video download → form fill → publish (default)

Usage:
    # Publish immediately after filling (default behavior)
    python publish_pipeline.py --title "标题" --content "正文" --image-urls URL1 URL2
    python publish_pipeline.py --title-file t.txt --content-file body.txt --image-urls URL1

    # Fill form only for manual review (preview mode)
    python publish_pipeline.py --title "标题" --content "正文" --image-urls URL1 --preview

    # Headless mode (no GUI window) - faster for automated publishing
    python publish_pipeline.py --headless --title-file t.txt --content-file body.txt --image-urls URL1

    # Publish to a specific account
    python publish_pipeline.py --account myaccount --title "标题" --content "正文" --image-urls URL1

    # Explicit auto-publish flag (optional compatibility flag)
    python publish_pipeline.py --title "标题" --content "正文" --image-urls URL1 --auto-publish

    # Prefer reusing existing tab (reduce focus switching in headed mode)
    python publish_pipeline.py --reuse-existing-tab --title "标题" --content "正文" --image-urls URL1

    # Use local image files instead of URLs
    python publish_pipeline.py --title "标题" --content "正文" --images img1.jpg img2.jpg
    # Skip local file check (for WSL/remote CDP + Windows/UNC paths)
    python publish_pipeline.py --title "标题" --content "正文" --images "\\\\wsl.localhost\\Ubuntu\\home\\me\\a.jpg" --skip-file-check

    # Preserve original Windows/UNC upload paths
    python publish_pipeline.py --title "标题" --content "正文" --images "\\\\wsl.localhost\\Ubuntu\\home\\me\\a.jpg" --skip-file-check --preserve-upload-paths

    # Publish a video (local file)
    python publish_pipeline.py --title "标题" --content "正文" --video video.mp4

    # Publish a video (from URL)
    python publish_pipeline.py --title "标题" --content "正文" --video-url "https://example.com/video.mp4"

Exit codes:
    0 = success (PUBLISHED, or READY_TO_PUBLISH in preview mode)
    1 = not logged in (NOT_LOGGED_IN) - headless auto-fallback will restart headed
    2 = error (see stderr)
"""

import argparse
import json
import os
import random
import re
import sys
import time

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    # 加载 .env（兼容直接运行和 subprocess 调用）
    try:
        from dotenv import load_dotenv
        load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    except ImportError:
        pass
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Add scripts dir to path so sibling modules can be imported
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from chrome_launcher import ensure_chrome, restart_chrome
from cdp_publish import XiaohongshuPublisher, CDPError
from image_downloader import ImageDownloader
from run_lock import SingleInstanceError, single_instance


MAX_TIMING_JITTER_RATIO = 0.7


def _normalize_timing_jitter(value: float) -> float:
    """Clamp timing jitter to a safe range."""
    return max(0.0, min(MAX_TIMING_JITTER_RATIO, value))


def _is_local_host(host: str) -> bool:
    """Return True when host points to the local machine."""
    return host.strip().lower() in {"127.0.0.1", "localhost", "::1"}


def _resolve_account_name(account_name: str | None) -> str:
    """Resolve explicit or default account name for login cache scoping."""
    if account_name and account_name.strip():
        return account_name.strip()
    try:
        from account_manager import get_default_account
        resolved = get_default_account()
        if isinstance(resolved, str) and resolved.strip():
            return resolved.strip()
    except Exception:
        pass
    return "default"


def _jitter_ms(base_ms: int, jitter_ratio: float, minimum_ms: int = 0) -> int:
    """Return a randomized delay in milliseconds around the base value."""
    base = max(minimum_ms, int(base_ms))
    if jitter_ratio <= 0:
        return base

    delta = int(round(base * jitter_ratio))
    low = max(minimum_ms, base - delta)
    high = max(low, base + delta)
    return random.randint(low, high)


def _jitter_seconds(
    base_seconds: float,
    jitter_ratio: float,
    minimum_seconds: float = 0.05,
) -> float:
    """Return a randomized delay in seconds around the base value."""
    base = max(minimum_seconds, float(base_seconds))
    if jitter_ratio <= 0:
        return base

    delta = base * jitter_ratio
    low = max(minimum_seconds, base - delta)
    high = max(low, base + delta)
    return random.uniform(low, high)


def _extract_topic_tags_from_last_line(content: str) -> tuple[str, list[str]]:
    """Extract topic tags from the last non-empty line.

    Expected format of the last line: "#标签1 #标签2 #标签3"
    Returns:
        (content_without_tag_line, tags)
    """
    lines = content.splitlines()

    # Ignore trailing blank lines when finding the last meaningful line.
    while lines and not lines[-1].strip():
        lines.pop()

    if not lines:
        return content, []

    last_line = lines[-1].strip()
    parts = [p for p in last_line.split() if p]
    if not parts:
        return content, []

    # Every token must look like '#xxx' and cannot contain spaces.
    if not all(re.fullmatch(r"#[^\s#]+", part) for part in parts):
        return content, []

    body = "\n".join(lines[:-1]).strip()
    return body, parts


def _verify_local_files_exist(
    file_paths: list[str],
    media_label: str,
    skip_file_check: bool,
):
    """Verify local files exist unless explicitly skipped."""
    if skip_file_check:
        print(
            f"[pipeline] Step 3: Skipping local {media_label} file check "
            "(--skip-file-check)."
        )
        return

    for file_path in file_paths:
        if not os.path.isfile(file_path):
            print(f"Error: {media_label} file not found: {file_path}", file=sys.stderr)
            sys.exit(2)


# ---------------------------------------------------------------------------
# 话题标签写入（三层：缓存直插 / 敲字兜底 / 校验+回填）
# ---------------------------------------------------------------------------

def _topic_cache_connect():
    import sqlite3
    from config.yahoo_conf import DB_PATH
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS topic_cache ("
        " name TEXT PRIMARY KEY, topic_id TEXT NOT NULL, link TEXT,"
        " updated_at TEXT)"
    )
    return conn


def _topic_cache_get(name):
    """命中返回 (topic_id, link)，未命中返回 None。"""
    try:
        conn = _topic_cache_connect()
        try:
            return conn.execute(
                "SELECT topic_id, link FROM topic_cache WHERE name=?", (name,)
            ).fetchone()
        finally:
            conn.close()
    except Exception:
        return None


def _topic_cache_put(name, topic_id, link):
    try:
        conn = _topic_cache_connect()
        try:
            conn.execute(
                "INSERT INTO topic_cache(name, topic_id, link, updated_at)"
                " VALUES(?,?,?,datetime('now','localtime'))"
                " ON CONFLICT(name) DO UPDATE SET topic_id=excluded.topic_id,"
                " link=excluded.link, updated_at=excluded.updated_at",
                (name, topic_id, link or ""),
            )
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print("[pipeline] warning: write topic_cache failed (ignored): %s" % e)


def _count_topic_chips(publisher):
    """编辑器内真实话题 chip 数。必须限定在编辑器内 —— 推荐话题组件里也有 a.tiptap-topic。"""
    try:
        v = publisher._evaluate(
            "document.querySelectorAll('div.ProseMirror a.tiptap-topic').length"
        )
        return int(v or 0)
    except Exception:
        return -1


def _read_editor_topics(publisher):
    try:
        raw = publisher._evaluate(
            "JSON.stringify(Array.from(document.querySelectorAll("
            "'div.ProseMirror a.tiptap-topic')).map(function(e){"
            "try{return JSON.parse(e.getAttribute('data-topic'))}catch(err){return null}})"
            ".filter(Boolean))"
        )
        return json.loads(raw) if raw else []
    except Exception:
        return []


def _cache_topics_from_editor(publisher):
    """把编辑器里 chip 的真实 id 回填缓存，下次同标签即走快路径。"""
    for t in _read_editor_topics(publisher):
        d = (t or {}).get("data") or {}
        if d.get("id") and d.get("name"):
            _topic_cache_put(d["name"], d["id"], d.get("link"))


def _insert_topic_via_tiptap(publisher, name, topic_id, link):
    """用 Tiptap 事务直接插 chip：零键盘、零下拉、零 API。"""
    data = {"id": topic_id, "name": name}
    if link:
        data["link"] = link
    js = (
        "(() => {"
        " var el = document.querySelector('div.ProseMirror');"
        " if (!el || !el.editor) return {ok:false, reason:'NO_EDITOR'};"
        " var cnt = function(){ return document.querySelectorAll('div.ProseMirror a.tiptap-topic').length; };"
        " var before = cnt();"
        " el.editor.chain().focus('end').insertContent({type:'topic', attrs:{data: "
        + json.dumps(data, ensure_ascii=False) +
        "}}).run();"
        " return {ok: cnt() > before, chips: before + '->' + cnt()};"
        "})()"
    )
    try:
        r = publisher._evaluate(js, timeout_seconds=20)
        return bool(r and r.get("ok"))
    except Exception:
        return False


def _type_char(publisher, ch):
    if ch in ("\n", "\r"):
        publisher._send("Input.dispatchKeyEvent", {
            "type": "keyDown", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
        publisher._send("Input.dispatchKeyEvent", {
            "type": "keyUp", "key": "Enter", "code": "Enter",
            "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13})
    else:
        publisher._send("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "text": ch})
        publisher._send("Input.dispatchKeyEvent", {"type": "keyUp", "key": ch})
    time.sleep(0.06)


def _clear_typed(publisher, name):
    """删掉刚敲进去的字符，避免残留在正文里。"""
    try:
        r = publisher._evaluate("""(function(){
            var ed = document.querySelector('div.ProseMirror').editor;
            if (!ed) return {ok:false, reason:'NO_EDITOR'};
            var s = ed.state, to = s.selection.to;
            var expected = %s;
            var from = Math.max(0, to - expected.length);
            var actual = s.doc.textBetween(from, to, "");
            if (actual !== expected) return {ok:false, actual: actual};
            ed.chain().focus().deleteRange({from: from, to: to}).run();
            return {ok:true};
        })()""" % json.dumps("#" + name))
        return bool(r and r.get("ok"))
    except Exception:
        return False


def _activate_editor(publisher):
    """无 VNC 时纯 JS focus() 不足以让编辑器接收键盘输入，必须先发真实鼠标点击。"""
    publisher._evaluate("""
        Object.defineProperty(document,'visibilityState',{get:()=>'visible',configurable:true});
        Object.defineProperty(document,'hidden',{get:()=>false,configurable:true});
        document.dispatchEvent(new Event('visibilitychange'));
    """)
    rect = publisher._evaluate("""(function(){
        var e=document.querySelector('div.tiptap.ProseMirror,div.ProseMirror[contenteditable]');
        if(!e)return null; var r=e.getBoundingClientRect();
        return {x:r.x+r.width/2, y:r.y+r.height/2};})()""")
    if not rect:
        return False
    for evt in ("mousePressed", "mouseReleased"):
        publisher._send("Input.dispatchMouseEvent", {
            "type": evt, "x": rect["x"], "y": rect["y"], "button": "left", "clickCount": 1})
        time.sleep(0.05)
    time.sleep(0.3)
    return True


def _select_topic_by_typing(publisher, name, timing_jitter=0.25, attempts=3):
    """敲 #name 触发下拉 → 轮询等它渲染 → 点精确匹配项 → 以 chip 数校验成败。

    - 轮询代替固定等待（下拉实测需 ~2s，固定 1.5s 会误判失败）
    - 以「编辑器内 chip 是否真的增加」为成败依据
    - 失败重试：实测页面联想常在第 1 次不出、第 2 次才出
    """
    before = _count_topic_chips(publisher)
    if before < 0:
        return False

    js_pick = """(function(){
        var items = Array.from(document.querySelectorAll('.item')).filter(function(e){return e.offsetParent && e.innerText;});
        if (!items.length) return null;
        var exact = null, partial = null;
        for (var el of items) {
          var first = (el.innerText.split("\\n")[0] || "").trim().replace(/^#/, "");
          if (first === %s) { exact = el; break; }
          if (!partial && el.innerText.indexOf(%s) >= 0) partial = el;
        }
        var t = exact || partial;
        if (!t) return null;
        t.click();
        return (t.innerText.split("\\n")[0] || "").trim();
    })()""" % (json.dumps(name), json.dumps(name))

    for attempt in range(1, attempts + 1):
        publisher._evaluate("""(function(){
            var e=document.querySelector('div.tiptap.ProseMirror,div.ProseMirror[contenteditable]');
            if(!e)return; e.focus(); var s=window.getSelection(),r=document.createRange();
            r.selectNodeContents(e); r.collapse(false); s.removeAllRanges(); s.addRange(r);})()""")
        time.sleep(0.2)

        _type_char(publisher, "\n")
        _type_char(publisher, "#")
        for ch in name:
            _type_char(publisher, ch)

        clicked = None
        deadline = time.time() + 6.0
        while time.time() < deadline:
            try:
                clicked = publisher._evaluate(js_pick, timeout_seconds=15)
            except Exception:
                clicked = None
            if clicked:
                break
            time.sleep(0.3)

        if clicked:
            time.sleep(0.6)
            if _count_topic_chips(publisher) > before:
                if clicked.lstrip("#") != name:
                    print("[pipeline] warning: topic name mismatch: want #%s, picked %s" % (name, clicked))
                return True

        _clear_typed(publisher, name)   # 只在确认真的敲进去过时才删，避免误删 chip
        time.sleep(0.4)
        if attempt < attempts:
            print("[pipeline] retry topic tag #%s (attempt %d/%d)" % (name, attempt, attempts))

    return False


def _select_topics(publisher, tags, timing_jitter=0.25):
    """写入话题标签。

    优先缓存直插（Tiptap 事务，零键盘零下拉）；未命中才敲字+下拉，
    成功后把 chip 的真实 id 回填缓存，下次同标签即走快路径。
    """
    if not tags:
        return

    print("[pipeline] Step 4.1: Selecting %d topic tag(s)..." % len(tags))
    if not _activate_editor(publisher):
        print("[pipeline] warning: editor not found, skip topic tags")
        return

    failed_tags = []
    for tag in tags:
        name = tag.lstrip("#").strip()
        if not name:
            continue

        cached = _topic_cache_get(name)
        if cached and _insert_topic_via_tiptap(publisher, name, cached[0], cached[1]):
            print("[pipeline] Topic selected (cache): #%s" % name)
            continue
        if cached:
            print("[pipeline] warning: cache insert failed, falling back to typing: #%s" % name)

        if _select_topic_by_typing(publisher, name, timing_jitter):
            print("[pipeline] Topic selected (typed): #%s" % name)
            _cache_topics_from_editor(publisher)
        else:
            failed_tags.append(name)
            print("[pipeline] Topic NOT selected: #%s" % name)

    if failed_tags:
        print("[pipeline] Warning: Some topic tags were not selected: " + ", ".join(failed_tags))




def main():
    parser = argparse.ArgumentParser(
        description="Xiaohongshu publish pipeline - unified entry point"
    )

    # Title
    title_group = parser.add_mutually_exclusive_group(required=True)
    title_group.add_argument("--title", help="Article title text")
    title_group.add_argument("--title-file", help="Read title from UTF-8 file")

    # Content
    content_group = parser.add_mutually_exclusive_group(required=True)
    content_group.add_argument("--content", help="Article body text")
    content_group.add_argument("--content-file", help="Read content from UTF-8 file")

    # Scheduled publishing
    parser.add_argument(
        "--post-time",
        default=None,
        help="Timer for publishing on note",
    )

    # Media: images OR video (mutually exclusive)
    media_group = parser.add_mutually_exclusive_group(required=True)
    media_group.add_argument(
        "--image-urls", nargs="+", help="Image URLs to download"
    )
    media_group.add_argument(
        "--images", nargs="+", help="Local image file paths"
    )
    media_group.add_argument(
        "--video", help="Local video file path"
    )
    media_group.add_argument(
        "--video-url", help="Video URL to download"
    )

    # Publish mode
    parser.add_argument(
        "--auto-publish",
        action="store_true",
        default=False,
        help=(
            "Compatibility flag. Publish is now the default behavior unless "
            "--preview is enabled."
        ),
    )

    parser.add_argument(
        "--preview",
        action="store_true",
        default=False,
        help="Preview mode: fill content only and never click publish button",
    )

    # Headless mode
    parser.add_argument(
        "--headless",
        action="store_true",
        default=False,
        help="Run Chrome in headless mode (no GUI). Auto-falls back to headed if login is needed.",
    )

    parser.add_argument(
        "--timing-jitter",
        type=float,
        default=0.25,
        help=(
            "Timing jitter ratio for operation delays (default: 0.25). "
            "Set 0 to disable random jitter."
        ),
    )

    parser.add_argument(
        "--reuse-existing-tab",
        action="store_true",
        default=False,
        help=(
            "Prefer reusing an existing Chrome tab before creating a new one. "
            "Useful in headed mode to reduce foreground focus switching."
        ),
    )

    # Optional temp dir for downloaded images
    parser.add_argument(
        "--temp-dir",
        default=None,
        help="Directory for downloaded images (default: auto-created temp dir)",
    )
    parser.add_argument(
        "--skip-file-check",
        action="store_true",
        default=False,
        help=(
            "Skip local media file existence check. Useful when running in WSL "
            "or using remote CDP with Windows/UNC paths."
        ),
    )
    parser.add_argument(
        "--preserve-upload-paths",
        action="store_true",
        default=False,
        help=(
            "Force preserving original upload file paths instead of converting "
            "backslashes to forward slashes before DOM.setFileInputFiles. "
            "Windows/UNC paths are auto-detected by default."
        ),
    )

    # Account selection
    parser.add_argument(
        "--account",
        default=None,
        help="Account name to publish to (default: default account)",
    )

    # CDP port
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="CDP host (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=9222,
        help="CDP remote debugging port (default: 9222)",
    )

    args = parser.parse_args()
    host = args.host
    port = args.port
    headless = args.headless
    account = args.account
    cache_account_name = _resolve_account_name(account)
    reuse_existing_tab = args.reuse_existing_tab
    timing_jitter = _normalize_timing_jitter(args.timing_jitter)
    local_mode = _is_local_host(host)
    post_time = args.post_time

    if timing_jitter != args.timing_jitter:
        print(
            "[pipeline] Warning: --timing-jitter out of range. "
            f"Clamped to {timing_jitter:.2f}."
        )

    # --- Resolve title ---
    if args.title_file:
        with open(args.title_file, encoding="utf-8") as f:
            title = f.read().strip()
    else:
        title = args.title

    if not title:
        print("Error: title is empty.", file=sys.stderr)
        sys.exit(2)

    # --- Resolve content ---
    if args.content_file:
        with open(args.content_file, encoding="utf-8") as f:
            content = f.read().strip()
    else:
        content = args.content

    if not content:
        print("Error: content is empty.", file=sys.stderr)
        sys.exit(2)

    content, topic_tags = _extract_topic_tags_from_last_line(content)
    if topic_tags:
        print(
            "[pipeline] Detected topic tags from last line: "
            f"{' '.join(topic_tags)}"
        )

    # --- Step 1: Ensure Chrome is running ---
    mode_label = "headless" if headless else "headed"
    account_label = cache_account_name
    print(
        f"[pipeline] Step 1: Ensuring Chrome is running "
        f"({mode_label}, account: {account_label}, host: {host}, port: {port})..."
    )
    print(f"[pipeline] Timing jitter ratio: {timing_jitter:.2f}")
    if reuse_existing_tab:
        print("[pipeline] Tab selection mode: prefer reusing existing tab.")
    if local_mode:
        if not ensure_chrome(port=port, headless=headless, account=account):
            print("Error: Failed to start Chrome.", file=sys.stderr)
            sys.exit(2)
    else:
        print(
            f"[pipeline] Remote CDP mode enabled: {host}:{port}. "
            "Skipping local Chrome launch/restart."
        )

    # --- Step 2: Connect and check login ---
    print("[pipeline] Step 2: Checking login status...")
    publisher = XiaohongshuPublisher(
        host=host,
        port=port,
        timing_jitter=timing_jitter,
        account_name=cache_account_name,
        preserve_upload_paths=args.preserve_upload_paths,
    )
    try:
        publisher.connect(reuse_existing_tab=reuse_existing_tab)
        logged_in = publisher.check_login()
        if not logged_in:
            publisher.disconnect()
            no_fallback = os.environ.get("CDP_NO_LOGIN_FALLBACK", "").lower() in ("1", "true", "yes")
            if headless and not no_fallback:
                if local_mode:
                    # Auto-fallback: restart Chrome in headed mode for QR login
                    print("[pipeline] Headless mode: not logged in. Switching to headed mode for login...")
                    restart_chrome(port=port, headless=False, account=account)
                    publisher.connect(reuse_existing_tab=reuse_existing_tab)
                    publisher.open_login_page()
                else:
                    print(
                        "[pipeline] Headless + remote mode: cannot auto-restart remote Chrome. "
                        "Attempting to open login page on existing remote browser..."
                    )
                    publisher.connect(reuse_existing_tab=reuse_existing_tab)
                    publisher.open_login_page()
            else:
                print("[pipeline] Headless mode: not logged in. Run without --headless to login first.")
            print("NOT_LOGGED_IN")
            sys.exit(1)
    except CDPError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(2)

    # --- Determine publish mode: video or image ---
    is_video_mode = bool(args.video or args.video_url)

    # --- Step 3: Prepare media ---
    image_paths = []
    video_path = None
    downloader = None

    if is_video_mode:
        if args.video_url:
            print("[pipeline] Step 3: Downloading video...")
            downloader = ImageDownloader(temp_dir=args.temp_dir)
            video_path = downloader.download_video(args.video_url)
            if not video_path:
                print("Error: Video download failed.", file=sys.stderr)
                sys.exit(2)
        else:
            video_path = args.video
            _verify_local_files_exist(
                file_paths=[video_path],
                media_label="Video",
                skip_file_check=args.skip_file_check,
            )
            print(f"[pipeline] Step 3: Using local video: {video_path}")
    elif args.image_urls:
        print(f"[pipeline] Step 3: Downloading {len(args.image_urls)} image(s)...")
        downloader = ImageDownloader(temp_dir=args.temp_dir)
        image_paths = downloader.download_all(args.image_urls)
        if not image_paths:
            print("Error: All image downloads failed.", file=sys.stderr)
            sys.exit(2)
    else:
        image_paths = args.images
        _verify_local_files_exist(
            file_paths=image_paths,
            media_label="Image",
            skip_file_check=args.skip_file_check,
        )
        print(f"[pipeline] Step 3: Using {len(image_paths)} local image(s).")

    # --- Step 4: Fill form ---
    print("[pipeline] Step 4: Filling form...")
    try:
        if is_video_mode:
            publisher.publish_video(
                title=title, content=content, video_path=video_path
            )
        else:
            publisher.publish(
                title=title, content=content, image_paths=image_paths, post_time=post_time
            )
        _select_topics(publisher, topic_tags, timing_jitter=timing_jitter)
        print("FILL_STATUS: READY_TO_PUBLISH")
    except CDPError as e:
        print(f"Error during form fill: {e}", file=sys.stderr)
        if downloader:
            downloader.cleanup()
        sys.exit(2)

    # --- Step 5: Publish (optional) ---
    should_publish = not args.preview
    if args.auto_publish:
        print("[pipeline] --auto-publish is now default and can be omitted.")
    if args.preview:
        print("[pipeline] Preview mode is on, skipping publish click.")

    if should_publish:
        print("[pipeline] Step 5: Clicking publish button...")
        try:
            note_link = publisher._click_publish(post_time != None)
            print("PUBLISH_STATUS: PUBLISHED")
            if note_link:
                print(f"[pipeline] Note published at: {note_link}")
        except CDPError as e:
            print(f"Error clicking publish: {e}", file=sys.stderr)
            if downloader:
                downloader.cleanup()
            sys.exit(2)

    # --- Cleanup ---
    publisher.disconnect()
    if downloader:
        downloader.cleanup()

    print("[pipeline] Done.")


if __name__ == "__main__":
    try:
        with single_instance("post_to_xhs_publish"):
            main()
    except SingleInstanceError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
