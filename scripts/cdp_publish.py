"""
CDP-based Xiaohongshu publisher.

Connects to a Chrome instance via Chrome DevTools Protocol to automate
publishing articles on Xiaohongshu (RED) creator center.

CLI usage:
    # Basic commands
    python cdp_publish.py [--host HOST] [--port PORT] check-login [--headless] [--account NAME] [--reuse-existing-tab] [--preserve-upload-paths]
    python cdp_publish.py [--host HOST] [--port PORT] fill --title "标题" --content "正文" --images img1.jpg [--headless] [--account NAME] [--reuse-existing-tab] [--preserve-upload-paths]
    python cdp_publish.py [--host HOST] [--port PORT] publish --title "标题" --content "正文" --images img1.jpg [--headless] [--account NAME] [--reuse-existing-tab] [--preserve-upload-paths]
    python cdp_publish.py [--host HOST] [--port PORT] click-publish [--headless] [--account NAME] [--reuse-existing-tab] [--preserve-upload-paths]
    python cdp_publish.py [--host HOST] [--port PORT] get-login-qrcode [--wait-seconds 20]
    python cdp_publish.py [--host HOST] [--port PORT] list-feeds
    python cdp_publish.py [--host HOST] [--port PORT] search-feeds --keyword "关键词" [--sort-by 综合|最新|最多点赞|最多评论|最多收藏]
    python cdp_publish.py [--host HOST] [--port PORT] get-feed-detail --feed-id FEED_ID --xsec-token TOKEN [--load-all-comments]
    python cdp_publish.py [--host HOST] [--port PORT] post-comment-to-feed --feed-id FEED_ID --xsec-token TOKEN --content "评论内容"
    python cdp_publish.py [--host HOST] [--port PORT] respond-comment --feed-id FEED_ID --xsec-token TOKEN --content "回复内容" [--comment-id ID]
    python cdp_publish.py [--host HOST] [--port PORT] note-upvote --feed-id FEED_ID --xsec-token TOKEN
    python cdp_publish.py [--host HOST] [--port PORT] note-unvote --feed-id FEED_ID --xsec-token TOKEN
    python cdp_publish.py [--host HOST] [--port PORT] note-bookmark --feed-id FEED_ID --xsec-token TOKEN
    python cdp_publish.py [--host HOST] [--port PORT] note-unbookmark --feed-id FEED_ID --xsec-token TOKEN
    python cdp_publish.py [--host HOST] [--port PORT] profile-snapshot [--profile-url URL | --user-id USER_ID]
    python cdp_publish.py [--host HOST] [--port PORT] notes-from-profile [--profile-url URL | --user-id USER_ID]
    python cdp_publish.py [--host HOST] [--port PORT] get-notification-mentions [--wait-seconds 18]
    python cdp_publish.py [--host HOST] [--port PORT] content-data [--page-num 1] [--page-size 10] [--type 0]

    # Account management
    python cdp_publish.py [--host HOST] [--port PORT] login [--account NAME]           # open browser for QR login
    python cdp_publish.py [--host HOST] [--port PORT] re-login [--account NAME]        # clear cookies and re-login same account
    python cdp_publish.py [--host HOST] [--port PORT] switch-account [--account NAME]  # clear cookies + open login for new account
    python cdp_publish.py [--host HOST] [--port PORT] list-accounts                    # list all configured accounts
    python cdp_publish.py [--host HOST] [--port PORT] add-account NAME [--alias ALIAS] # add a new account
    python cdp_publish.py [--host HOST] [--port PORT] remove-account NAME              # remove an account

Library usage:
    from cdp_publish import XiaohongshuPublisher

    publisher = XiaohongshuPublisher()
    publisher.connect()
    publisher.check_login()
    publisher.publish(
        title="Article title",
        content="Article body text",
        image_paths=["/path/to/img1.jpg", "/path/to/img2.jpg"],
    )
"""

import json
import os
import random
import time
import sys
import csv
import base64
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from urllib.parse import parse_qs, urlencode, urlparse
from typing import Any

# Add scripts dir to path so sibling modules can be imported in both
# "python scripts/cdp_publish.py" and "import scripts.cdp_publish" modes.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from xhs_errors import CDPError, XHSRateLimitError, _PromiseCollectedError  # noqa: E402
from cdp_login import LoginMixin  # noqa: E402
from cdp_feed import FeedMixin  # noqa: E402
from cdp_publish_flow import PublishFlowMixin  # noqa: E402
from cdp_stats import StatsMixin  # noqa: E402
from xhs_util import (  # noqa: E402
    _normalize_timing_jitter, _is_local_host, _resolve_account_name,
    _build_search_filters_from_args, _format_post_time,
    validate_schedule_post_time, _format_cover_click_rate,
    _format_view_time_avg, _metric_or_dash,
    _map_note_infos_to_content_rows, _write_content_data_csv,
)

# Ensure UTF-8 output on Windows consoles
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

import requests
import websockets.sync.client as ws_client
from feed_explorer import (
    SEARCH_BASE_URL,
    LOCATION_OPTIONS,
    NOTE_TYPE_OPTIONS,
    PUBLISH_TIME_OPTIONS,
    SEARCH_SCOPE_OPTIONS,
    SORT_BY_OPTIONS,
    FeedExplorer,
    FeedExplorerError,
    SearchFilters,
    make_feed_detail_url,
    make_search_url,
)
from run_lock import SingleInstanceError, single_instance

# ---------------------------------------------------------------------------
# Configuration - centralised selectors and URLs for easy maintenance
# ---------------------------------------------------------------------------

from xhs_constants import (
    CDP_HOST, CDP_PORT,
    XHS_CREATOR_URL, XHS_HOME_URL, XHS_NOTIFICATION_URL, XHS_CREATOR_LOGIN_CHECK_URL,
    XHS_HOME_LOGIN_MODAL_KEYWORD, XHS_CONTENT_DATA_URL, XHS_CONTENT_DATA_API_PATH,
    XHS_NOTIFICATION_MENTIONS_API_PATH, XHS_SEARCH_RECOMMEND_API_PATH,
    XHS_FEED_INACCESSIBLE_KEYWORDS, SELECTORS, PAGE_LOAD_WAIT, TAB_CLICK_WAIT,
    UPLOAD_WAIT, VIDEO_PROCESS_TIMEOUT, VIDEO_PROCESS_POLL, ACTION_INTERVAL,
    MAX_TIMING_JITTER_RATIO, CDP_COMMAND_TIMEOUT, DEFAULT_LOGIN_CACHE_TTL_HOURS,
    LOGIN_CACHE_FILE,
)


class XiaohongshuPublisher(LoginMixin, FeedMixin, PublishFlowMixin, StatsMixin):
    """Automates publishing to Xiaohongshu via CDP."""

    def __init__(
        self,
        host: str = CDP_HOST,
        port: int = CDP_PORT,
        timing_jitter: float = 0.25,
        account_name: str | None = None,
        preserve_upload_paths: bool = False,
    ):
        self.host = host
        self.port = port
        self.ws = None
        self._tab_ws_url: str | None = None  # WebSocket URL of the connected tab
        self._msg_id = 0
        self.timing_jitter = _normalize_timing_jitter(timing_jitter)
        self.account_name = (account_name or "default").strip() or "default"
        self.preserve_upload_paths = bool(preserve_upload_paths)
        self.command_timeout_seconds = CDP_COMMAND_TIMEOUT
        self.login_cache_ttl_hours = DEFAULT_LOGIN_CACHE_TTL_HOURS
        self.login_cache_ttl_seconds = self.login_cache_ttl_hours * 3600
        self.login_cache_file = LOGIN_CACHE_FILE

    def _prepare_upload_file_path(self, file_path: str) -> str:
        """Return the file path to send to DOM.setFileInputFiles.

        DOM.setFileInputFiles 必须收到绝对路径：传相对路径会让渲染进程主线程卡死
        （Runtime.evaluate / Page.enable 全部超时，且不可恢复）。
        """
        if self._should_preserve_upload_path(file_path):
            return file_path
        normalized = file_path.replace("\\", "/")
        if _is_local_host(self.host) and not os.path.isabs(normalized):
            normalized = os.path.abspath(normalized)
        return normalized

    def _looks_like_windows_drive_path(self, file_path: str) -> bool:
        """Return True when the path looks like a Windows drive-letter path."""
        return len(file_path) >= 3 and file_path[0].isalpha() and file_path[1] == ":" and file_path[2] in ("\\", "/")

    def _looks_like_unc_path(self, file_path: str) -> bool:
        """Return True when the path looks like a UNC path."""
        return file_path.startswith("\\\\") or file_path.startswith("//")

    def _looks_like_windows_backslash_path(self, file_path: str) -> bool:
        """Return True when the path likely follows Windows-style backslash syntax."""
        if "\\" not in file_path or "/" in file_path:
            return False
        if file_path.startswith("\\"):
            return True
        parts = [part for part in file_path.split("\\") if part]
        return len(parts) >= 2

    def _should_preserve_upload_path(self, file_path: str) -> bool:
        """Return True when upload path should be preserved as-is."""
        if self.preserve_upload_paths:
            return True
        return (
            self._looks_like_windows_drive_path(file_path)
            or self._looks_like_unc_path(file_path)
            or self._looks_like_windows_backslash_path(file_path)
        )

    def _sleep(self, base_seconds: float, minimum_seconds: float = 0.05):
        """Sleep with optional randomized jitter to avoid rigid timing patterns."""
        base = max(minimum_seconds, float(base_seconds))
        if self.timing_jitter <= 0:
            time.sleep(base)
            return

        delta = base * self.timing_jitter
        low = max(minimum_seconds, base - delta)
        high = max(low, base + delta)
        time.sleep(random.uniform(low, high))

    # ------------------------------------------------------------------
    # CDP connection management
    # ------------------------------------------------------------------

    def _get_targets(self) -> list[dict]:
        """Get list of available browser targets (tabs). Retries once on failure."""
        url = f"http://{self.host}:{self.port}/json"
        for attempt in range(2):
            try:
                resp = requests.get(
                    url,
                    timeout=5,
                    proxies={"http": None, "https": None} if _is_local_host(self.host) else None,
                )
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                if attempt == 0:
                    if _is_local_host(self.host):
                        print(f"[cdp_publish] CDP connection failed ({e}), restarting Chrome...")
                        from chrome_launcher import ensure_chrome
                        ensure_chrome(port=self.port)
                    else:
                        print(
                            f"[cdp_publish] CDP connection failed ({e}), retrying remote endpoint "
                            f"{self.host}:{self.port}..."
                        )
                    self._sleep(2, minimum_seconds=1.0)
                else:
                    raise CDPError(f"Cannot reach Chrome on {self.host}:{self.port}: {e}")

    def _find_or_create_tab(
        self,
        target_url_prefix: str = "",
        reuse_existing_tab: bool = False,
    ) -> str:
        """
        Find a tab to connect.

        Default behavior is backward-compatible: create a new tab first.
        When `reuse_existing_tab` is enabled, prefer reusing an existing page tab
        to reduce focus switching in headed mode.
        """
        targets = self._get_targets()
        pages = [
            t for t in targets
            if t.get("type") == "page" and t.get("webSocketDebuggerUrl")
        ]

        if target_url_prefix:
            for t in pages:
                if t.get("url", "").startswith(target_url_prefix):
                    return t["webSocketDebuggerUrl"]

        if reuse_existing_tab and pages:
            # Prefer a tab that is already on xiaohongshu.com/explore with a
            # note detail open (/explore/{feed_id}) — it has the SPA Vue Router
            # context that allows note-detail overlays to mount correctly.
            # Fall back to any xiaohongshu.com tab, then finally pages[0].
            # Skip tabs that are stuck on a login/verify page.
            _skip_patterns = ("verifyMsg", "login", "signin", "about:blank")

            def _is_usable(t: dict) -> bool:
                url = t.get("url", "")
                return not any(p in url for p in _skip_patterns)

            usable = [t for t in pages if _is_usable(t)]
            if not usable:
                usable = pages  # fallback — all tabs are suspect, use first

            xhs_explore_detail = [
                t for t in usable
                if "/explore/" in t.get("url", "")
                and "xiaohongshu.com" in t.get("url", "")
            ]
            xhs_explore_any = [
                t for t in usable
                if "xiaohongshu.com/explore" in t.get("url", "")
            ]
            xhs_any = [
                t for t in usable
                if "xiaohongshu.com" in t.get("url", "")
            ]
            chosen = (
                (xhs_explore_detail or xhs_explore_any or xhs_any or usable)[0]
            )
            url = chosen.get("url", "")
            print(
                "[cdp_publish] Reusing existing tab to reduce focus switching: "
                f"{url}"
            )
            return chosen["webSocketDebuggerUrl"]

        # Create a new tab
        resp = requests.put(
            f"http://{self.host}:{self.port}/json/new?{XHS_CREATOR_URL}",
            timeout=5,
            proxies={"http": None, "https": None} if _is_local_host(self.host) else None,
        )
        if resp.ok:
            ws_url = resp.json().get("webSocketDebuggerUrl", "")
            if ws_url:
                return ws_url

        # Fallback: use first available page
        if pages:
            return pages[0]["webSocketDebuggerUrl"]

        raise CDPError("No browser tabs available.")

    def connect(self, target_url_prefix: str = "", reuse_existing_tab: bool = False):
        """Connect to a Chrome tab via WebSocket."""
        ws_url = self._find_or_create_tab(
            target_url_prefix=target_url_prefix,
            reuse_existing_tab=reuse_existing_tab,
        )
        if not ws_url:
            raise CDPError("Could not obtain WebSocket URL for any tab.")

        print(f"[cdp_publish] Connecting to {ws_url}")
        self._tab_ws_url = ws_url  # saved for reconnect after page navigation
        self.ws = ws_client.connect(ws_url, max_size=None)
        print("[cdp_publish] Connected to Chrome tab.")
        self._ensure_page_active()

    def _ensure_page_active(self):
        """让页面在「无显示器 / VNC 关闭」时也保持渲染活跃。

        Chrome 在窗口没有显示器时会节流渲染，导致小红书的话题联想插件不发起请求
        —— 这就是「必须开 VNC 才能输入标签」的根因。
        实测：下面两个调用组合可以把渲染唤醒（rAF 由 0 恢复到 ~20-30fps），
        联想请求随之恢复正常（VNC 关闭状态下连续多次验证通过）。
        """
        for method, params in (
            ("Page.setWebLifecycleState", {"state": "active"}),
            ("Emulation.setFocusEmulationEnabled", {"enabled": True}),
        ):
            try:
                self._send(method, params)
            except Exception:
                pass

    def disconnect(self):
        """Close the WebSocket connection."""
        if self.ws:
            self.ws.close()
            self.ws = None

    # ------------------------------------------------------------------
    # CDP command helpers
    # ------------------------------------------------------------------

    def _send(
        self,
        method: str,
        params: dict | None = None,
        timeout_seconds: float | None = None,
    ) -> dict:
        """Send a CDP command and return the result with a bounded wait."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        self._msg_id += 1
        message_id = self._msg_id
        msg = {"id": message_id, "method": method}
        if params:
            msg["params"] = params

        self.ws.send(json.dumps(msg))
        timeout = float(timeout_seconds or self.command_timeout_seconds)
        deadline = time.monotonic() + max(0.1, timeout)

        # Wait for the matching response
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise CDPError(
                    f"Timed out waiting for CDP response to {method} "
                    f"after {timeout:.1f}s."
                )

            try:
                raw = self.ws.recv(timeout=max(0.1, remaining))
            except TimeoutError as exc:
                raise CDPError(
                    f"Timed out waiting for CDP response to {method} "
                    f"after {timeout:.1f}s."
                ) from exc
            except Exception as exc:
                raise CDPError(f"CDP receive failed while waiting for {method}: {exc}") from exc

            try:
                data = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CDPError(
                    f"Received invalid CDP JSON while waiting for {method}: {exc}"
                ) from exc

            if data.get("id") == message_id:
                if "error" in data:
                    err = data["error"]
                    # Chrome CDP GC bug: retry once on 'Promise was collected'
                    if (isinstance(err, dict) and err.get("code") == -32000
                            and "Promise was collected" in err.get("message", "")):
                        raise _PromiseCollectedError(f"CDP error: {err}")
                    raise CDPError(f"CDP error: {err}")
                return data.get("result", {})
            # else: 它是事件。需要抓 Network 响应时收集到 _event_sink
            sink = getattr(self, "_event_sink", None)
            if sink is not None:
                sink.append(data)

    def _drain_events(self, seconds, sink=None):
        """持续读取 websocket 并把事件收集到 sink（用于抓 Network 响应）。

        与 _send 的区别：_send 只等自己那条命令的响应、其余事件丢弃；
        这里专门用来在「页面自己发请求」时把 responseReceived 等事件收下来。
        """
        if sink is None:
            sink = getattr(self, "_event_sink", None)
        end = time.time() + float(seconds)
        while time.time() < end:
            try:
                raw = self.ws.recv(timeout=0.4)
            except Exception:
                continue
            try:
                data = json.loads(raw)
            except Exception:
                continue
            if "id" not in data and sink is not None:
                sink.append(data)
        return sink

    def _evaluate(self, expression: str, timeout_seconds: float | None = None) -> Any:
        """Execute JavaScript in the page and return the result value."""
        # Retry once on 'Promise was collected' — Chrome CDP GC bug that occurs
        # under memory pressure (e.g. during image uploads).
        for attempt in range(2):
            try:
                result = self._send("Runtime.evaluate", {
                    "expression": expression,
                    "returnByValue": True,
                    "awaitPromise": True,
                }, timeout_seconds=timeout_seconds)
            except _PromiseCollectedError:
                if attempt == 0:
                    time.sleep(0.5)
                    continue
                raise CDPError("CDP error: Promise was collected (retry exhausted)")
            remote_obj = result.get("result", {})
            if remote_obj.get("subtype") == "error":
                raise CDPError(f"JS error: {remote_obj.get('description', remote_obj)}")
            return remote_obj.get("value")

    def _reconnect(self):
        """Reconnect WebSocket to the same tab (used after full-page navigations)."""
        if not self._tab_ws_url:
            raise CDPError("Cannot reconnect: no tab WebSocket URL saved.")
        try:
            if self.ws:
                self.ws.close()
        except Exception:
            pass
        self.ws = ws_client.connect(self._tab_ws_url, max_size=None)
        # 新会话会丢掉 Emulation/生命周期 override，重新激活一次
        self._ensure_page_active()

    def _navigate(self, url: str):
        """Navigate the current tab to the given URL and wait for load."""
        print(f"[cdp_publish] Navigating to {url}")
        self._send("Page.enable")
        self._send("Page.navigate", {"url": url})
        self._sleep(PAGE_LOAD_WAIT, minimum_seconds=1.0)
        # Page.navigate can trigger a full-page reload which closes the WebSocket.
        # Silently reconnect so callers do not need to handle this.
        try:
            self._evaluate("1")  # lightweight liveness check
        except Exception:
            self._reconnect()
            # After reconnect the page may still be loading; give it extra time.
            self._sleep(2, minimum_seconds=1.5)
        # 导航后重新激活：无显示器时新页面会回到被节流状态
        self._ensure_page_active()

    # ------------------------------------------------------------------
    # Login check
    # ------------------------------------------------------------------

def main():
    import argparse
    from chrome_launcher import ensure_chrome, restart_chrome

    parser = argparse.ArgumentParser(description="Xiaohongshu CDP Publisher")
    parser.add_argument(
        "--host",
        default=CDP_HOST,
        help=f"CDP host (default: {CDP_HOST})",
    )
    parser.add_argument("--port", type=int, default=CDP_PORT,
                        help=f"CDP remote debugging port (default: {CDP_PORT})")
    parser.add_argument("--headless", action="store_true",
                        help="Use headless Chrome (no GUI window)")
    parser.add_argument("--account", help="Account name to use (default: default account)")
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
        help=(
            "Prefer reusing an existing tab before creating a new one. "
            "Useful in headed mode to reduce foreground focus switching."
        ),
    )
    parser.add_argument(
        "--preserve-upload-paths",
        action="store_true",
        help=(
            "Force preserving original upload file paths instead of converting "
            "backslashes to forward slashes before DOM.setFileInputFiles. "
            "Windows/UNC paths are auto-detected by default."
        ),
    )
    parser.add_argument(
        "--no-login-cache",
        action="store_true",
        help="Disable the 12h login-status cache for this run (always re-check).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # check-login
    sub.add_parser("check-login", help="Check login status (exit 0=logged in, 1=not)")

    # login-probe - 零导航探针（查 web_session cookie），用于扫码期间高频轮询
    sub.add_parser("login-probe", help="Zero-navigation login probe (no page reload)")

    p_qrcode = sub.add_parser(
        "get-login-qrcode",
        aliases=["get_login_qrcode"],
        help="Get login QR code image payload for remote display",
    )
    p_qrcode.add_argument(
        "--wait-seconds",
        type=float,
        default=20.0,
        help="Seconds to wait for QR code to appear (default: 20)",
    )

    # fill - fill form without clicking publish
    p_fill = sub.add_parser("fill", help="Fill title/content/images or video without publishing")
    p_fill.add_argument("--title", required=True)
    p_fill.add_argument("--content", default=None)
    p_fill.add_argument("--content-file", default=None, help="Read content from file")
    p_fill_media = p_fill.add_mutually_exclusive_group(required=True)
    p_fill_media.add_argument("--images", nargs="+", help="Local image file paths")
    p_fill_media.add_argument("--video", help="Local video file path")

    # publish - fill form and click publish
    p_pub = sub.add_parser("publish", help="Fill form and click publish")
    p_pub.add_argument("--title", required=True)
    p_pub.add_argument("--content", default=None)
    p_pub.add_argument("--content-file", default=None, help="Read content from file")
    p_pub_media = p_pub.add_mutually_exclusive_group(required=True)
    p_pub_media.add_argument("--images", nargs="+", help="Local image file paths")
    p_pub_media.add_argument("--video", help="Local video file path")

    # click-publish - just click the publish button on current page
    sub.add_parser("click-publish", help="Click publish button on already-filled page")

    p_list_feeds = sub.add_parser(
        "list-feeds",
        aliases=["list_feeds"],
        help="Get home recommendation feeds",
    )

    # search-feeds - search note feeds by keyword
    p_search = sub.add_parser(
        "search-feeds",
        aliases=["search_feeds"],
        help="Search Xiaohongshu feeds by keyword",
    )
    p_search.add_argument("--keyword", required=True, help="Search keyword")
    p_search.add_argument("--sort-by", choices=SORT_BY_OPTIONS, help="Sort by option")
    p_search.add_argument("--note-type", choices=NOTE_TYPE_OPTIONS, help="Note type filter")
    p_search.add_argument(
        "--publish-time",
        choices=PUBLISH_TIME_OPTIONS,
        help="Publish time filter",
    )
    p_search.add_argument(
        "--search-scope",
        choices=SEARCH_SCOPE_OPTIONS,
        help="Search scope filter",
    )
    p_search.add_argument("--location", choices=LOCATION_OPTIONS, help="Location filter")

    # get-feed-detail - get note detail by feed id and token
    p_detail = sub.add_parser(
        "get-feed-detail",
        aliases=["get_feed_detail"],
        help="Get feed detail by feed id and xsec token",
    )
    p_detail.add_argument("--feed-id", required=True, help="Feed id")
    p_detail.add_argument("--xsec-token", required=True, help="xsec token")
    p_detail.add_argument(
        "--load-all-comments",
        action="store_true",
        help="Scroll to load more top-level comments before extracting detail",
    )
    p_detail.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Target max number of top-level comments to load (default: 20)",
    )
    p_detail.add_argument(
        "--click-more-replies",
        action="store_true",
        help="Try to expand visible reply groups while loading comments",
    )
    p_detail.add_argument(
        "--reply-limit",
        type=int,
        default=10,
        help="Skip expanding reply groups above this size when possible (default: 10)",
    )
    p_detail.add_argument(
        "--scroll-speed",
        choices=("slow", "normal", "fast"),
        default="normal",
        help="Comment loading scroll speed (default: normal)",
    )

    # post-comment-to-feed - post top-level comment to feed detail
    p_comment = sub.add_parser(
        "post-comment-to-feed",
        aliases=["post_comment_to_feed"],
        help="Post a top-level comment to feed detail",
    )
    p_comment.add_argument("--feed-id", required=True, help="Feed id")
    p_comment.add_argument("--xsec-token", required=True, help="xsec token")
    p_comment_content = p_comment.add_mutually_exclusive_group(required=True)
    p_comment_content.add_argument("--content", help="Comment content")
    p_comment_content.add_argument("--content-file", help="Read comment content from file")

    # respond-comment - reply to an existing comment on feed detail
    p_reply = sub.add_parser(
        "respond-comment",
        aliases=["respond_comment"],
        help="Reply to an existing comment on feed detail",
    )
    p_reply.add_argument("--feed-id", required=True, help="Feed id")
    p_reply.add_argument("--xsec-token", required=True, help="xsec token")
    p_reply_content = p_reply.add_mutually_exclusive_group(required=True)
    p_reply_content.add_argument("--content", help="Reply content")
    p_reply_content.add_argument("--content-file", help="Read reply content from file")
    p_reply.add_argument("--comment-id", help="Target comment id")
    p_reply.add_argument("--comment-author", help="Target comment author name (fuzzy match)")
    p_reply.add_argument("--comment-snippet", help="Target comment text snippet (fuzzy match)")

    # profile-snapshot - read user profile summary
    p_profile = sub.add_parser(
        "profile-snapshot",
        aliases=["profile_snapshot"],
        help="Get user profile snapshot from profile page",
    )
    p_profile_target = p_profile.add_mutually_exclusive_group(required=True)
    p_profile_target.add_argument("--profile-url", help="Full profile URL")
    p_profile_target.add_argument("--user-id", help="User id for profile URL composition")

    # notes-from-profile - list notes from user profile page
    p_profile_notes = sub.add_parser(
        "notes-from-profile",
        aliases=["notes_from_profile"],
        help="List notes from profile page",
    )
    p_profile_notes_target = p_profile_notes.add_mutually_exclusive_group(required=True)
    p_profile_notes_target.add_argument("--profile-url", help="Full profile URL")
    p_profile_notes_target.add_argument("--user-id", help="User id for profile URL composition")
    p_profile_notes.add_argument("--limit", type=int, default=20, help="Max notes to return (default: 20)")
    p_profile_notes.add_argument(
        "--max-scrolls",
        type=int,
        default=3,
        help="Extra scroll rounds for lazy-loaded notes (default: 3)",
    )

    # note-upvote / note-unvote - toggle like state
    p_upvote = sub.add_parser(
        "note-upvote",
        aliases=["note_upvote"],
        help="Set note to upvoted state",
    )
    p_upvote.add_argument("--feed-id", required=True, help="Feed id")
    p_upvote.add_argument("--xsec-token", required=True, help="xsec token")

    p_unvote = sub.add_parser(
        "note-unvote",
        aliases=["note_unvote"],
        help="Set note to not-upvoted state",
    )
    p_unvote.add_argument("--feed-id", required=True, help="Feed id")
    p_unvote.add_argument("--xsec-token", required=True, help="xsec token")

    # note-bookmark / note-unbookmark - toggle favorite state
    p_bookmark = sub.add_parser(
        "note-bookmark",
        aliases=["note_bookmark"],
        help="Set note to bookmarked state",
    )
    p_bookmark.add_argument("--feed-id", required=True, help="Feed id")
    p_bookmark.add_argument("--xsec-token", required=True, help="xsec token")

    p_unbookmark = sub.add_parser(
        "note-unbookmark",
        aliases=["note_unbookmark"],
        help="Set note to not-bookmarked state",
    )
    p_unbookmark.add_argument("--feed-id", required=True, help="Feed id")
    p_unbookmark.add_argument("--xsec-token", required=True, help="xsec token")

    # get-notification-mentions - capture notification mentions API response
    p_mentions = sub.add_parser(
        "get-notification-mentions",
        aliases=["get_notification_mentions"],
        help="Capture notification mentions API payload from /notification page",
    )
    p_mentions.add_argument(
        "--wait-seconds",
        type=float,
        default=18.0,
        help="Max seconds to wait for mentions API request (default: 18)",
    )

    # content-data - fetch creator content data table
    p_content_data = sub.add_parser(
        "content-data",
        aliases=["content_data"],
        help="Fetch creator content data table from statistics page",
    )
    p_content_data.add_argument(
        "--page-num",
        type=int,
        default=1,
        help="Page number (default: 1)",
    )
    p_content_data.add_argument(
        "--page-size",
        type=int,
        default=10,
        help="Page size (default: 10)",
    )
    p_content_data.add_argument(
        "--type",
        dest="note_type",
        type=int,
        default=0,
        help="Type filter value used by API (default: 0)",
    )
    p_content_data.add_argument(
        "--csv-file",
        help="Optional CSV output path",
    )

    # login - open browser for QR code login (always headed)
    sub.add_parser("login", help="Open browser for QR code login (always headed mode)")

    # re-login - clear cookies and re-login the same account (always headed)
    sub.add_parser("re-login", help="Clear cookies and re-login same account (always headed)")

    # switch-account - clear cookies and open login page (always headed)
    sub.add_parser("switch-account",
                   help="Clear cookies and open login page for new account (always headed)")

    # list-accounts - list all configured accounts
    sub.add_parser("list-accounts", help="List all configured accounts")

    # add-account - add a new account
    p_add = sub.add_parser("add-account", help="Add a new account")
    p_add.add_argument("name", help="Account name (unique identifier)")
    p_add.add_argument("--alias", help="Display name / description")

    # remove-account - remove an account
    p_rm = sub.add_parser("remove-account", help="Remove an account")
    p_rm.add_argument("name", help="Account name to remove")
    p_rm.add_argument("--delete-profile", action="store_true",
                      help="Also delete the Chrome profile directory")

    # set-default-account - set default account
    p_def = sub.add_parser("set-default-account", help="Set the default account")
    p_def.add_argument("name", help="Account name to set as default")

    args = parser.parse_args()
    host = args.host
    port = args.port
    headless = args.headless
    account = args.account
    cache_account_name = _resolve_account_name(account)
    reuse_existing_tab = args.reuse_existing_tab
    timing_jitter = _normalize_timing_jitter(args.timing_jitter)
    local_mode = _is_local_host(host)

    if timing_jitter != args.timing_jitter:
        print(
            "[cdp_publish] Warning: --timing-jitter out of range. "
            f"Clamped to {timing_jitter:.2f}."
        )
    # Account management commands that don't need Chrome
    if args.command == "list-accounts":
        from account_manager import list_accounts
        accounts = list_accounts()
        if not accounts:
            print("No accounts configured.")
            return
        print(f"{'Name':<20} {'Alias':<25} {'Default':<10}")
        print("-" * 55)
        for acc in accounts:
            default_mark = "*" if acc["is_default"] else ""
            print(f"{acc['name']:<20} {acc['alias']:<25} {default_mark:<10}")
        return

    elif args.command == "add-account":
        from account_manager import add_account, get_profile_dir
        if add_account(args.name, args.alias):
            print(f"Account '{args.name}' added.")
            print(f"Profile dir: {get_profile_dir(args.name)}")
            print("\nTo log in to this account, run:")
            print(f"  python cdp_publish.py --account {args.name} login")
        else:
            print(f"Error: Account '{args.name}' already exists.", file=sys.stderr)
            sys.exit(1)
        return

    elif args.command == "remove-account":
        from account_manager import remove_account
        if remove_account(args.name, args.delete_profile):
            print(f"Account '{args.name}' removed.")
        else:
            print(f"Error: Cannot remove account '{args.name}'.", file=sys.stderr)
            sys.exit(1)
        return

    elif args.command == "set-default-account":
        from account_manager import set_default_account
        if set_default_account(args.name):
            print(f"Default account set to '{args.name}'.")
        else:
            print(f"Error: Account '{args.name}' not found.", file=sys.stderr)
            sys.exit(1)
        return

    # Commands that require Chrome - login/re-login/switch-account always headed
    if args.command in ("login", "re-login", "switch-account"):
        headless = False

    if local_mode:
        if not ensure_chrome(port=port, headless=headless, account=account):
            print("Failed to start Chrome. Exiting.")
            sys.exit(1)
    else:
        print(
            f"[cdp_publish] Remote CDP mode enabled: {host}:{port}. "
            "Skipping local Chrome launch/restart."
        )

    print(f"[cdp_publish] Timing jitter ratio: {timing_jitter:.2f}")
    print(f"[cdp_publish] Login cache: enabled (ttl={DEFAULT_LOGIN_CACHE_TTL_HOURS:g}h).")
    if reuse_existing_tab:
        print("[cdp_publish] Tab selection mode: prefer reusing existing tab.")

    publisher = XiaohongshuPublisher(
        host=host,
        port=port,
        timing_jitter=timing_jitter,
        account_name=cache_account_name,
        preserve_upload_paths=args.preserve_upload_paths,
    )
    if getattr(args, "no_login_cache", False):
        publisher.login_cache_ttl_seconds = 0
        print("[cdp_publish] Login cache disabled (--no-login-cache).")

    try:
        if args.command == "check-login":
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            logged_in = publisher.check_login()
            if not logged_in and headless:
                print(
                    "[cdp_publish] Headless mode: cannot scan QR code.\n"
                    "  Run with 'login' command or without --headless to log in."
                )
            sys.exit(0 if logged_in else 1)

        elif args.command == "login-probe":
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            logged = publisher.probe_login_state()
            print("LOGIN_PROBE: " + json.dumps({"logged_in": bool(logged)}))
            sys.exit(0 if logged else 1)

        elif args.command in ("get-login-qrcode", "get_login_qrcode"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            payload = publisher.get_login_qrcode(wait_seconds=args.wait_seconds)
            print("GET_LOGIN_QRCODE_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("fill", "publish"):
            content = args.content
            if args.content_file:
                with open(args.content_file, encoding="utf-8") as f:
                    content = f.read().strip()
            if not content:
                print("Error: --content or --content-file required.", file=sys.stderr)
                sys.exit(1)

            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if getattr(args, "video", None):
                publisher.publish_video(
                    title=args.title, content=content, video_path=args.video
                )
            else:
                publisher.publish(
                    title=args.title, content=content, image_paths=args.images
                )
            print("FILL_STATUS: READY_TO_PUBLISH")

            if args.command == "publish":
                publisher._click_publish()
                print("PUBLISH_STATUS: PUBLISHED")

        elif args.command == "click-publish":
            publisher.connect(
                target_url_prefix="https://creator.xiaohongshu.com/publish",
                reuse_existing_tab=reuse_existing_tab,
            )
            publisher._click_publish()
            print("PUBLISH_STATUS: PUBLISHED")

        elif args.command in ("list-feeds", "list_feeds"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.list_feeds()
            print("LIST_FEEDS_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("search-feeds", "search_feeds"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            filters = _build_search_filters_from_args(args)
            search_result = publisher.search_feeds(keyword=args.keyword, filters=filters)
            feeds = search_result.get("feeds", [])
            recommended_keywords = search_result.get("recommended_keywords", [])
            payload = {
                "keyword": args.keyword,
                "recommended_keywords_count": len(recommended_keywords),
                "recommended_keywords": recommended_keywords,
                "count": len(feeds),
                "feeds": feeds,
            }
            print("SEARCH_FEEDS_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("get-feed-detail", "get_feed_detail"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            detail_result = publisher.get_feed_detail(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                load_all_comments=args.load_all_comments,
                limit=args.limit,
                click_more_replies=args.click_more_replies,
                reply_limit=args.reply_limit,
                scroll_speed=args.scroll_speed,
            )
            payload = {
                "feed_id": args.feed_id,
                "xsec_token": args.xsec_token,
                "load_all_comments": args.load_all_comments,
                "comment_loading": detail_result.get("comment_loading"),
                "detail": detail_result.get("detail"),
            }
            print("GET_FEED_DETAIL_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("post-comment-to-feed", "post_comment_to_feed"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            comment_content = args.content
            if args.content_file:
                with open(args.content_file, encoding="utf-8") as f:
                    comment_content = f.read().strip()
            if not comment_content:
                print("Error: --content or --content-file required.", file=sys.stderr)
                sys.exit(1)

            payload = publisher.post_comment_to_feed(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                content=comment_content,
            )
            print("POST_COMMENT_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("respond-comment", "respond_comment"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            reply_content = args.content
            if args.content_file:
                with open(args.content_file, encoding="utf-8") as f:
                    reply_content = f.read().strip()
            if not reply_content:
                print("Error: --content or --content-file required.", file=sys.stderr)
                sys.exit(1)

            payload = publisher.respond_comment(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                content=reply_content,
                comment_id=args.comment_id,
                comment_author=args.comment_author,
                comment_snippet=args.comment_snippet,
            )
            print("RESPOND_COMMENT_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("profile-snapshot", "profile_snapshot"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.get_profile_snapshot(
                profile_url=args.profile_url,
                user_id=args.user_id,
            )
            print("PROFILE_SNAPSHOT_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("notes-from-profile", "notes_from_profile"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.list_profile_notes(
                profile_url=args.profile_url,
                user_id=args.user_id,
                limit=args.limit,
                max_scrolls=args.max_scrolls,
            )
            print("PROFILE_NOTES_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("note-upvote", "note_upvote"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.set_note_upvote_state(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                upvoted=True,
            )
            print("NOTE_UPVOTE_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("note-unvote", "note_unvote"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.set_note_upvote_state(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                upvoted=False,
            )
            print("NOTE_UNVOTE_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("note-bookmark", "note_bookmark"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.set_note_bookmark_state(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                bookmarked=True,
            )
            print("NOTE_BOOKMARK_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("note-unbookmark", "note_unbookmark"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.set_note_bookmark_state(
                feed_id=args.feed_id,
                xsec_token=args.xsec_token,
                bookmarked=False,
            )
            print("NOTE_UNBOOKMARK_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("get-notification-mentions", "get_notification_mentions"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_home_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.get_notification_mentions(wait_seconds=args.wait_seconds)
            print("GET_NOTIFICATION_MENTIONS_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

        elif args.command in ("content-data", "content_data"):
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            if not publisher.check_login():
                print("NOT_LOGGED_IN")
                sys.exit(1)

            payload = publisher.get_content_data(
                page_num=args.page_num,
                page_size=args.page_size,
                note_type=args.note_type,
            )
            print("CONTENT_DATA_RESULT:")
            print(json.dumps(payload, ensure_ascii=False, indent=2))

            if args.csv_file:
                csv_path = _write_content_data_csv(
                    csv_file=args.csv_file,
                    rows=payload.get("rows", []),
                )
                print(f"CONTENT_DATA_CSV: {csv_path}")

        elif args.command == "login":
            # Ensure headed mode for QR scanning
            if local_mode:
                restart_chrome(port=port, headless=False, account=account)
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            publisher.open_login_page()
            print("LOGIN_READY")

        elif args.command == "re-login":
            # Ensure headed mode, clear cookies, re-open login page for same account
            if local_mode:
                restart_chrome(port=port, headless=False, account=account)
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            publisher.clear_cookies()
            publisher._sleep(1, minimum_seconds=0.5)
            publisher.open_login_page()
            print("RE_LOGIN_READY")

        elif args.command == "switch-account":
            # Ensure headed mode, clear cookies, open login page
            if local_mode:
                restart_chrome(port=port, headless=False, account=account)
            publisher.connect(reuse_existing_tab=reuse_existing_tab)
            publisher.clear_cookies()
            publisher._sleep(1, minimum_seconds=0.5)
            publisher.open_login_page()
            print("SWITCH_ACCOUNT_READY")

    finally:
        publisher.disconnect()


if __name__ == "__main__":
    try:
        with single_instance("post_to_xhs_publish"):
            main()
    except SingleInstanceError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(3)
