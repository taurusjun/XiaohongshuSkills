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


def _select_topics(
    publisher: XiaohongshuPublisher,
    tags: list[str],
    timing_jitter: float = 0.25,
):
    """Select XHS topic tags by simulating real keyboard input.

    Uses Input.dispatchKeyEvent (keyDown+text per char) so XHS Vue/ProseMirror
    processes the input natively and converts #tag<space> into a proper tiptap-topic
    chip with a real XHS topic ID.  execCommand/insertText bypasses XHS event
    handlers and never creates topic chips.
    """
    if not tags:
        return

    print(f"[pipeline] Step 4.1: Selecting {len(tags)} topic tag(s)...")
    failed_tags = []

    def _type_char(ch: str):
        """Send a single character via keyDown+keyUp (keyDown carries the text)."""
        if ch in ("\n", "\r"):
            # ProseMirror needs a real Enter key event to create a new paragraph
            publisher._send("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
            })
            publisher._send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "Enter", "code": "Enter",
                "windowsVirtualKeyCode": 13, "nativeVirtualKeyCode": 13,
            })
        else:
            publisher._send("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "text": ch})
            publisher._send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": ch})
        time.sleep(0.06)

    def _focus_editor_end():
        publisher._evaluate("""
            (function(){
                var e=document.querySelector('div.tiptap.ProseMirror,div.ProseMirror[contenteditable]');
                if(!e)return;
                e.focus();
                var s=window.getSelection(),r=document.createRange();
                r.selectNodeContents(e);r.collapse(false);
                s.removeAllRanges();s.addRange(r);
            })()
        """)

    # Override visibilityState so Vue event handlers process keyboard events
    # when Chrome window has no active display (e.g. VNC disconnected).
    publisher._evaluate("""
        Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
        Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
        document.dispatchEvent(new Event('visibilitychange'));
    """)

    # Scroll editor into viewport so keyboard events and dropdown clicks land correctly.
    publisher._evaluate("""
        var e = document.querySelector('div.tiptap.ProseMirror,div.ProseMirror[contenteditable]');
        if (e) e.scrollIntoView({block: 'center', behavior: 'instant'});
    """)
    time.sleep(0.3)

    # Mouse click on editor center — required to actually activate the editor
    # for keyboard input. JS focus() alone is insufficient without VNC.
    editor_rect = publisher._evaluate("""
        (function(){
            var e = document.querySelector('div.tiptap.ProseMirror,div.ProseMirror[contenteditable]');
            if (!e) return null;
            var r = e.getBoundingClientRect();
            return {x: r.x + r.width/2, y: r.y + r.height/2};
        })()
    """)
    if editor_rect:
        for ev in ("mousePressed", "mouseReleased"):
            publisher._send("Input.dispatchMouseEvent", {
                "type": ev, "x": editor_rect["x"], "y": editor_rect["y"],
                "button": "left", "clickCount": 1,
            })
        time.sleep(0.2)

    _focus_editor_end()
    time.sleep(0.2)

    for index, tag in enumerate(tags):
        normalized_tag = tag.lstrip("#").strip()
        if not normalized_tag:
            continue

        # Two newlines before first tag: one empty line + one for the tag line
        if index == 0:
            _type_char("\n")
            _type_char("\n")
            time.sleep(0.2)

        # Type # + each character of the tag name
        _type_char("#")
        time.sleep(0.1)
        for ch in normalized_tag:
            _type_char(ch)

        # Wait for XHS to show the topic suggestion dropdown
        suggest_wait = _jitter_seconds(1.5, timing_jitter, minimum_seconds=1.0)
        time.sleep(suggest_wait)

        # Confirm: use JS element.click() on the first matching .item in the dropdown.
        # dispatchMouseEvent and ArrowDown+Enter are unreliable without an active display.
        # JS click() reliably triggers Vue's click handler regardless of visibility state.
        tag_keyword = normalized_tag
        clicked = publisher._evaluate(f"""
            (function() {{
                var items = Array.from(document.querySelectorAll('.item'));
                for (var el of items) {{
                    if (el.offsetParent && el.innerText && el.innerText.includes({repr(tag_keyword)})) {{
                        el.click();
                        return true;
                    }}
                }}
                return false;
            }})()
        """)
        time.sleep(_jitter_seconds(0.4, timing_jitter, minimum_seconds=0.2))

        print(f"[pipeline] Topic selected: {tag}")

        if index < len(tags) - 1:
            time.sleep(_jitter_seconds(0.3, timing_jitter, minimum_seconds=0.15))

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
