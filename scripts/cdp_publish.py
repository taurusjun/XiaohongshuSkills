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


class XiaohongshuPublisher(LoginMixin, FeedMixin):
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

    def _build_content_data_result(
        self,
        payload: dict[str, Any],
        request_url: str,
        page_num: int,
        page_size: int,
        note_type: int,
        capture_mode: str,
    ) -> dict[str, Any]:
        """Normalize content-data API payload into CLI output."""
        data = payload.get("data")
        note_infos = data.get("note_infos") if isinstance(data, dict) else []
        if not isinstance(note_infos, list):
            note_infos = []
        rows = _map_note_infos_to_content_rows(note_infos)

        query = parse_qs(urlparse(request_url).query)

        def _query_int(name: str, default: int) -> int:
            raw = (query.get(name) or [str(default)])[0]
            try:
                return int(raw)
            except (TypeError, ValueError):
                return default

        return {
            "request_url": request_url,
            "requested_page_num": page_num,
            "requested_page_size": page_size,
            "requested_type": note_type,
            "resolved_page_num": _query_int("page_num", page_num),
            "resolved_page_size": _query_int("page_size", page_size),
            "resolved_type": _query_int("type", note_type),
            "total": data.get("total") if isinstance(data, dict) else None,
            "count_returned": len(rows),
            "rows": rows,
            "capture_mode": capture_mode,
        }

    def _fetch_content_data_via_page_fetch(
        self,
        page_num: int,
        page_size: int,
        note_type: int,
    ) -> dict[str, Any]:
        """Fetch content-data API from browser page context using explicit params."""
        query = urlencode(
            {
                "page_num": page_num,
                "page_size": page_size,
                "type": note_type,
            }
        )
        request_path = f"{XHS_CONTENT_DATA_API_PATH}?{query}"
        result = self._evaluate(f"""
            (async () => {{
                try {{
                    const response = await fetch({json.dumps(request_path)}, {{
                        method: "GET",
                        credentials: "include",
                        cache: "no-store",
                        headers: {{
                            "Accept": "application/json, text/plain, */*"
                        }}
                    }});
                    const body = await response.text();
                    return {{
                        ok: response.ok,
                        status: response.status,
                        url: response.url,
                        body,
                    }};
                }} catch (error) {{
                    return {{
                        ok: false,
                        status: 0,
                        url: {json.dumps(request_path)},
                        error: String(error),
                        body: "",
                    }};
                }}
            }})()
        """)

        if not isinstance(result, dict):
            raise CDPError("Unexpected page-fetch result for content data API.")

        if not result.get("ok"):
            raise CDPError(
                "Content data page fetch failed: "
                f"status={result.get('status')}, error={result.get('error') or 'unknown'}"
            )

        body_text = result.get("body", "")
        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise CDPError(
                "Failed to decode content data API JSON from page fetch: "
                f"{exc}; preview={body_text[:300]}"
            ) from exc

        if not isinstance(payload, dict):
            raise CDPError("Unexpected content data payload structure.")

        return self._build_content_data_result(
            payload=payload,
            request_url=str(result.get("url") or request_path),
            page_num=page_num,
            page_size=page_size,
            note_type=note_type,
            capture_mode="page_fetch",
        )

    def _capture_content_data_from_page_request(
        self,
        page_num: int,
        page_size: int,
        note_type: int,
    ) -> dict[str, Any]:
        """Capture the page-triggered content-data request via CDP."""
        self._send("Page.enable")
        self._send("Network.enable", {
            "maxPostDataSize": 65536,
            "maxResourceBufferSize": 10 * 1024 * 1024,
        })
        self._send("Page.navigate", {"url": XHS_CONTENT_DATA_URL})

        request_url_by_id: dict[str, str] = {}
        target_request_id = ""
        target_request_url = ""
        body_text = ""
        deadline = time.time() + 18

        while time.time() < deadline:
            timeout = min(1.0, max(0.1, deadline - time.time()))
            try:
                raw = self.ws.recv(timeout=timeout)
            except TimeoutError:
                continue

            message = json.loads(raw)
            method = message.get("method")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request_id = params.get("requestId")
                request = params.get("request", {})
                if isinstance(request_id, str):
                    request_url_by_id[request_id] = request.get("url", "")

            elif method == "Network.responseReceived":
                request_id = params.get("requestId")
                if target_request_id or not isinstance(request_id, str):
                    continue
                request_url = request_url_by_id.get(request_id, "")
                if XHS_CONTENT_DATA_API_PATH not in request_url:
                    continue
                status = params.get("response", {}).get("status")
                if status == 200:
                    target_request_id = request_id
                    target_request_url = request_url

            elif method == "Network.loadingFinished" and target_request_id:
                if params.get("requestId") == target_request_id:
                    try:
                        body_result = self._send(
                            "Network.getResponseBody",
                            {"requestId": target_request_id},
                        )
                        body_text = body_result.get("body", "")
                        if body_result.get("base64Encoded"):
                            body_text = base64.b64decode(body_text).decode(
                                "utf-8", errors="replace"
                            )
                    except CDPError:
                        pass
                    break

        if not body_text:
            if target_request_id:
                raise CDPError(
                    "Failed to retrieve response body for content data request. "
                    f"url={target_request_url}"
                )
            raise CDPError(
                "Timed out waiting for content data request. "
                "Please open data-analysis page manually and retry."
            )

        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as exc:
            raise CDPError(
                "Failed to decode content data API JSON: "
                f"{exc}; preview={body_text[:300]}"
            ) from exc

        if not isinstance(payload, dict):
            raise CDPError("Unexpected content data payload structure.")

        return self._build_content_data_result(
            payload=payload,
            request_url=target_request_url,
            page_num=page_num,
            page_size=page_size,
            note_type=note_type,
            capture_mode="network_capture",
        )

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

    def _schedule_click_notification_mentions_tab(self) -> str:
        """Schedule a click on mentions tab after evaluate returns."""
        clicked_text = self._evaluate("""
            (() => {
                const keywordSet = new Set([
                    "评论和@",
                    "评论和 @",
                    "评论与@",
                    "提到我的",
                    "@我的",
                    "mentions",
                ]);
                const selectors = [
                    "[role='tab']",
                    "button",
                    "a",
                    "div[class*='tab']",
                    "div[class*='menu-item']",
                    "li[class*='tab-item']",
                    "li[class*='tab']",
                ];
                const seen = new Set();
                const candidates = [];
                for (const selector of selectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!(node instanceof HTMLElement)) {
                            continue;
                        }
                        if (node.offsetParent === null) {
                            continue;
                        }
                        if (seen.has(node)) {
                            continue;
                        }
                        seen.add(node);
                        candidates.push(node);
                    }
                }

                for (const node of candidates) {
                    const text = (node.innerText || node.textContent || "")
                        .replace(/\\s+/g, " ")
                        .trim();
                    if (!text) {
                        continue;
                    }
                    if (text.length > 24) {
                        continue;
                    }
                    const normalized = text.replace(/\\d+/g, "").replace(/\\s+/g, "");
                    const exactMatches = [
                        normalized,
                        text.replace(/\\d+/g, "").trim(),
                    ];
                    if (!exactMatches.some((candidate) => keywordSet.has(candidate))) {
                        continue;
                    }
                    window.setTimeout(() => {
                        try {
                            node.click();
                        } catch (error) {
                            // ignored
                        }
                    }, 80);
                    return text;
                }
                return "";
            })()
        """)
        if isinstance(clicked_text, str):
            return clicked_text.strip()
        return ""

    def _fetch_notification_mentions_via_page(self) -> dict[str, Any] | None:
        """Fetch mentions API directly in page context using logged-in cookies."""
        result = self._evaluate("""
            (() => fetch(
                "https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20&cursor=",
                {
                    method: "GET",
                    credentials: "include",
                    headers: {
                        "Accept": "application/json, text/plain, */*",
                    },
                }
            ).then(async (resp) => {
                const text = await resp.text();
                return {
                    ok: resp.ok,
                    status: resp.status,
                    url: resp.url,
                    body: text,
                };
            }).catch((error) => {
                return {
                    ok: false,
                    error: String(error),
                };
            }))()
        """)
        if not isinstance(result, dict):
            return None
        if not result.get("ok"):
            return None
        if int(result.get("status", 0)) != 200:
            return None
        body = result.get("body")
        if not isinstance(body, str) or not body.strip():
            return None
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None

        data = payload.get("data")
        items: list[Any] = []
        if isinstance(data, dict):
            for key in ("message_list", "items", "mentions", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    items = value
                    break

        return {
            "request_url": result.get("url") or (
                "https://edith.xiaohongshu.com/api/sns/web/v1/you/mentions?num=20&cursor="
            ),
            "count": len(items),
            "has_more": data.get("has_more") if isinstance(data, dict) else None,
            "cursor": data.get("cursor") if isinstance(data, dict) else None,
            "items": items,
            "raw_payload": payload,
            "capture_mode": "page_fetch",
        }

    def get_notification_mentions(self, wait_seconds: float = 18.0) -> dict[str, Any]:
        """
        Capture notification mentions API payload from notification page requests.

        The API is captured from real browser traffic to preserve platform
        signatures/cookies generated by page scripts.
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        wait_seconds = max(5.0, float(wait_seconds))

        self._send("Page.enable")
        self._send("Network.enable", {"maxPostDataSize": 65536})
        self._send("Network.setCacheDisabled", {"cacheDisabled": True})
        self._send("Page.navigate", {"url": XHS_NOTIFICATION_URL})
        self._sleep(1.2, minimum_seconds=0.5)

        direct_payload = self._fetch_notification_mentions_via_page()
        if direct_payload is not None:
            return direct_payload

        clicked_tab = self._schedule_click_notification_mentions_tab()
        if clicked_tab:
            print(f"[cdp_publish] Notification tab clicked: {clicked_tab}")

        request_meta_by_id: dict[str, dict[str, str]] = {}
        target_request_id = ""
        target_request_url = ""
        deadline = time.time() + wait_seconds

        while time.time() < deadline:
            timeout = min(1.0, max(0.1, deadline - time.time()))
            try:
                raw = self.ws.recv(timeout=timeout)
            except TimeoutError:
                continue

            message = json.loads(raw)
            method = message.get("method")
            params = message.get("params", {})

            if method == "Network.requestWillBeSent":
                request_id = params.get("requestId")
                request = params.get("request", {})
                if isinstance(request_id, str):
                    request_meta_by_id[request_id] = {
                        "url": request.get("url", ""),
                        "method": str(request.get("method", "")).upper(),
                    }
                continue

            if method == "Network.responseReceived":
                request_id = params.get("requestId")
                if not isinstance(request_id, str):
                    continue

                request_meta = request_meta_by_id.get(request_id, {})
                request_url = request_meta.get("url", "")
                if XHS_NOTIFICATION_MENTIONS_API_PATH not in request_url:
                    continue

                if request_meta.get("method") == "OPTIONS":
                    continue

                status = params.get("response", {}).get("status")
                if status != 200:
                    raise CDPError(
                        "Notification mentions API responded with non-200 status: "
                        f"{status}, url={request_url}"
                    )

                target_request_id = request_id
                target_request_url = request_url
                break

        if not target_request_id:
            raise CDPError(
                "Timed out waiting for notification mentions request. "
                "Please open notification page manually and retry."
            )

        body_result = self._send("Network.getResponseBody", {"requestId": target_request_id})
        body_text = body_result.get("body", "")
        if body_result.get("base64Encoded"):
            body_text = base64.b64decode(body_text).decode("utf-8", errors="replace")

        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError as e:
            raise CDPError(
                "Failed to decode notification mentions API JSON: "
                f"{e}; preview={body_text[:300]}"
            ) from e

        if not isinstance(payload, dict):
            raise CDPError("Unexpected notification mentions payload structure.")

        data = payload.get("data")
        items: list[Any] = []
        if isinstance(data, dict):
            for key in ("message_list", "items", "mentions", "list"):
                value = data.get(key)
                if isinstance(value, list):
                    items = value
                    break

        return {
            "request_url": target_request_url,
            "count": len(items),
            "has_more": data.get("has_more") if isinstance(data, dict) else None,
            "cursor": data.get("cursor") if isinstance(data, dict) else None,
            "items": items,
            "raw_payload": payload,
            "capture_mode": "network_capture",
        }

    def get_content_data(
        self,
        page_num: int = 1,
        page_size: int = 10,
        note_type: int = 0,
    ) -> dict[str, Any]:
        """
        Fetch creator content data table from data-analysis API.

        Args:
            page_num: Page number (1-based).
            page_size: Rows per page.
            note_type: API type filter value (default: 0).
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        if page_num < 1:
            raise CDPError("--page-num must be >= 1.")
        if page_size < 1:
            raise CDPError("--page-size must be >= 1.")
        result = self._capture_content_data_from_page_request(
            page_num=page_num,
            page_size=page_size,
            note_type=note_type,
        )
        rows = result.get("rows", [])
        if not rows:
            raise CDPError(
                "get_content_data: 返回0行数据。"
                "可能原因：未登录创作者后台、账号无已发布内容、或 API 请求超时。"
            )
        return result

    # ------------------------------------------------------------------
    # Publishing actions
    # ------------------------------------------------------------------

    def _query_node_id(self, selector: str) -> int:
        """Return the first DOM node id matching selector, or 0 when absent."""
        self._send("DOM.enable")
        doc = self._send("DOM.getDocument")
        root_id = doc["root"]["nodeId"]
        result = self._send("DOM.querySelector", {
            "nodeId": root_id,
            "selector": selector,
        })
        return int(result.get("nodeId", 0) or 0)

    def _count_uploaded_images(self) -> int:
        """Estimate how many uploaded image previews are visible."""
        count = self._evaluate(f"""
            (() => {{
                const selectors = [
                    {json.dumps(SELECTORS["image_preview_items"])},
                    ".img-preview-area [class*='preview']",
                    ".draggable-item",
                    "[class*='img-preview'] .pr"
                ];
                let maxCount = 0;
                for (const selector of selectors) {{
                    try {{
                        maxCount = Math.max(maxCount, document.querySelectorAll(selector).length);
                    }} catch (error) {{}}
                }}
                return maxCount;
            }})()
        """)
        return int(count or 0)

    def _wait_for_uploaded_images(self, expected_count: int, timeout_seconds: float = 60.0):
        """Wait until image preview count reaches the expected value."""
        deadline = time.time() + max(5.0, float(timeout_seconds))
        last_count = -1
        ticks = 0
        while time.time() < deadline:
            current_count = self._count_uploaded_images()
            ticks += 1
            if current_count != last_count:
                print(
                    "[cdp_publish] Waiting for uploaded image previews: "
                    f"{current_count}/{expected_count}"
                )
                last_count = current_count
            elif ticks % 10 == 0:
                # Every 5s, report to show we're still waiting
                print(
                    "[cdp_publish] Still waiting for image previews: "
                    f"{current_count}/{expected_count} (elapsed {ticks*0.5:.0f}s)"
                )
            if current_count >= expected_count:
                return
            self._sleep(0.5, minimum_seconds=0.15)

        # Timeout — dump diagnostic info
        final_count = self._count_uploaded_images()
        page_state = self._evaluate("""
            JSON.stringify({
                title: document.title,
                url: location.href,
                previewAreaHTML: document.querySelector('.img-preview-area')?.innerHTML?.substring(0, 300) || 'not found',
                allPreviews: document.querySelectorAll('[class*=\"preview\"], [class*=\"img\"], .pr').length,
                fileInputs: document.querySelectorAll('input[type=\"file\"]').length
            })
        """)
        print(f"[cdp_publish] Timeout diagnostic: final_count={final_count}, state={page_state}")
        raise CDPError(
            f"Timed out waiting for image upload preview {expected_count}. "
            "The creator page structure may have changed."
        )

    def _find_content_editor_selector(self) -> str | None:
        """Return the best available content editor selector for the current page."""
        placeholder_literal = json.dumps(SELECTORS["content_placeholder_text"])
        selector = self._evaluate(f"""
            (() => {{
                const directSelectors = [
                    {json.dumps(SELECTORS["content_editor"])},
                    {json.dumps(SELECTORS["content_editor_alt"])},
                    {json.dumps(SELECTORS["content_editor_alt2"])},
                    "[role='textbox']",
                ];
                for (const selector of directSelectors) {{
                    const node = document.querySelector(selector);
                    if (
                        node instanceof HTMLElement &&
                        node.offsetParent !== null &&
                        node.getBoundingClientRect().width > 0 &&
                        node.getBoundingClientRect().height > 0
                    ) {{
                        return selector;
                    }}
                }}

                const placeholder = {placeholder_literal};
                const candidates = document.querySelectorAll("p[data-placeholder], div[data-placeholder]");
                for (const node of candidates) {{
                    const value = (node.getAttribute("data-placeholder") || "").trim();
                    if (!value.includes(placeholder)) {{
                        continue;
                    }}
                    let current = node;
                    for (let depth = 0; depth < 5 && current; depth += 1) {{
                        current = current.parentElement;
                        if (
                            current instanceof HTMLElement &&
                            current.getAttribute("role") === "textbox"
                        ) {{
                            return "[role='textbox']";
                        }}
                    }}
                }}
                return null;
            }})()
        """)
        return selector if isinstance(selector, str) and selector.strip() else None

    def _get_publish_button_rect(self) -> dict[str, Any] | None:
        """Locate the current publish button rect.

        xhs-publish-btn is a Web Component with closed Shadow DOM — querySelector
        cannot pierce it. Instead we use CDP DOM APIs:
          DOM.describeNode(pierce=True) → finds the shadow root
          DOM.querySelectorAll on shadow root → finds .ce-btn.bg-red
          DOM.getBoxModel → exact pixel coordinates, no ratio guessing
        """
        self._send("DOM.enable")

        # Find xhs-publish-btn node
        doc = self._send("DOM.getDocument", {"depth": 1})
        root_id = doc["root"]["nodeId"]
        result = self._send("DOM.querySelectorAll", {
            "nodeId": root_id,
            "selector": SELECTORS["publish_button"],
        })
        xhs_ids = result.get("nodeIds", [])

        if xhs_ids:
            # Check submit-disabled via attribute
            attrs_result = self._send("DOM.getAttributes", {"nodeId": xhs_ids[0]})
            attrs = attrs_result.get("attributes", [])
            attr_map = {attrs[i]: attrs[i + 1] for i in range(0, len(attrs) - 1, 2)}
            if attr_map.get("submit-disabled") == "true":
                return None

            # Pierce into shadow root to find .ce-btn.bg-red
            desc = self._send("DOM.describeNode", {
                "nodeId": xhs_ids[0], "depth": -1, "pierce": True,
            })
            shadow_roots = desc["node"].get("shadowRoots", [])
            if shadow_roots:
                sr_id = shadow_roots[0]["nodeId"]
                btn_result = self._send("DOM.querySelectorAll", {
                    "nodeId": sr_id, "selector": ".ce-btn.bg-red",
                })
                btn_ids = btn_result.get("nodeIds", [])
                if btn_ids:
                    box = self._send("DOM.getBoxModel", {"nodeId": btn_ids[0]})
                    content = box.get("model", {}).get("content", [])
                    if len(content) >= 8:
                        x1, y1, x2, _, _, y2, _, _ = content
                        return {
                            "x": x1, "y": y1,
                            "width": x2 - x1, "height": y2 - y1,
                        }

            # shadow root not accessible — fall back to outer element rect
            box = self._send("DOM.getBoxModel", {"nodeId": xhs_ids[0]})
            content = box.get("model", {}).get("content", [])
            if len(content) >= 8:
                x1, y1, x2, _, _, y2, _, _ = content
                return {"x": x1, "y": y1, "width": x2 - x1, "height": y2 - y1}

        # Legacy: .publish-page-publish-btn button.bg-red
        return self._evaluate(f"""
            (() => {{
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 0 &&
                    node.getBoundingClientRect().height > 0
                );
                const toRect = (node) => {{
                    const rect = node.getBoundingClientRect();
                    return {{ x: rect.x, y: rect.y, width: rect.width, height: rect.height }};
                }};
                const legacyBtn = document.querySelector({json.dumps(SELECTORS["publish_button_legacy"])});
                if (visible(legacyBtn)) return toRect(legacyBtn);
                const keywords = [
                    {json.dumps(SELECTORS["publish_button_text"])},
                    {json.dumps(SELECTORS["schedule_publish_button_text"])},
                ];
                for (const node of document.querySelectorAll("button, [role='button'], .d-button")) {{
                    if (!visible(node)) continue;
                    const text = (node.innerText || node.textContent || "").trim();
                    if (keywords.includes(text)) return toRect(node);
                }}
                return null;
            }})()
        """)
        if rect is not None:
            return rect
        # Fallback: CDP DOM flattener for closed Shadow DOM
        try:
            doc = self._send("DOM.getFlattenedDocument", {"depth": -1, "pierce": True})
            for node in doc.get("nodes", []):
                if node.get("localName") == "button" and "bg-red" in str(node.get("attributes", [])):
                    bm = self._send("DOM.getBoxModel", {"nodeId": node["nodeId"]})
                    c = bm.get("model", {}).get("content", [])
                    if len(c) >= 4:
                        return {"x": c[0], "y": c[1], "width": c[2]-c[0], "height": c[5]-c[1]}
        except Exception:
            pass
        return None

    def _is_publish_button_ready(self) -> bool:
        """Return True when the publish button is present, visible and not disabled."""
        ready = self._evaluate(f"""
            (() => {{
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 0 &&
                    node.getBoundingClientRect().height > 0
                );

                // Current: xhs-publish-btn — ready when present, visible, submit-disabled != true
                const xhsBtn = document.querySelector({json.dumps(SELECTORS["publish_button"])});
                if (visible(xhsBtn) && xhsBtn.getAttribute("submit-disabled") !== "true") {{
                    return true;
                }}

                // Legacy selectors
                for (const sel of [{json.dumps(SELECTORS["publish_button_legacy"])}, "button.publishBtn"]) {{
                    const btn = document.querySelector(sel);
                    if (!visible(btn) || btn.hasAttribute("disabled")) continue;
                    if (String(btn.className || "").includes("disabled")) continue;
                    return true;
                }}
                return false;
            }})()
        """)
        if ready:
            return True
        # Fallback: CDP DOM flattener for closed Shadow DOM
        try:
            doc = self._send("DOM.getFlattenedDocument", {"depth": -1, "pierce": True})
            for node in doc.get("nodes", []):
                if node.get("localName") == "button" and "bg-red" in str(node.get("attributes", [])):
                    if "disabled" not in str(node.get("attributes", [])):
                        return True
        except Exception:
            pass
        return False

    def _wait_for_publish_button_ready(self, timeout_seconds: float = VIDEO_PROCESS_TIMEOUT):
        """Wait until the publish button becomes interactive."""
        deadline = time.time() + max(5.0, float(timeout_seconds))
        while time.time() < deadline:
            if self._is_publish_button_ready():
                print("[cdp_publish] Publish button is ready.")
                return
            self._sleep(VIDEO_PROCESS_POLL, minimum_seconds=0.4)

        raise CDPError(
            f"Publish button did not become ready within {int(timeout_seconds)}s."
        )

    def _click_tab(self, tab_selector: str, tab_text: str):
        """Click a publish-mode tab by selector and text content.
        使用 Input.dispatchMouseEvent 发送真实鼠标事件，避免 Vue SPA event.click() 无效。
        只在视口内可见的元素中查找，避免命中离屏（移动端）隐藏副本。"""
        print(f"[cdp_publish] Clicking '{tab_text}' tab...")
        tab_text_literal = json.dumps(tab_text)

        pos = self._evaluate(f"""
            (function() {{
                var targetText = {tab_text_literal};
                var fuzzyKeywords = [targetText];
                if (targetText.indexOf('图文') !== -1) {{
                    fuzzyKeywords.push('图文', '上传图文');
                }}
                if (targetText.indexOf('视频') !== -1) {{
                    fuzzyKeywords.push('视频', '上传视频');
                }}

                function matches(text) {{
                    var t = (text || '').trim();
                    if (!t) return false;
                    if (t === targetText) return true;
                    for (var i = 0; i < fuzzyKeywords.length; i++) {{
                        if (t.indexOf(fuzzyKeywords[i]) !== -1) return true;
                    }}
                    return false;
                }}

                var candidates = document.querySelectorAll(
                    'div.creator-tab, .creator-tab, [class*=\"creator-tab\"], [role=\"tab\"]'
                );
                for (var i = 0; i < candidates.length; i++) {{
                    if (!matches(candidates[i].textContent)) continue;
                    var r = candidates[i].getBoundingClientRect();
                    // 只在视口可见范围内（排除离屏的移动端隐藏副本）
                    if (r.width > 0 && r.height > 0 && r.left >= 0 && r.top >= 0) {{
                        return JSON.stringify({{
                            x: Math.round(r.left + r.width/2),
                            y: Math.round(r.top + r.height/2)
                        }});
                    }}
                }}
                return '';
            }})()
        """)

        if not pos or not isinstance(pos, str) or not pos.strip():
            upload_ready = self._evaluate(
                f"!!document.querySelector('{SELECTORS['upload_input']}') || "
                f"!!document.querySelector('{SELECTORS['upload_input_alt']}')"
            )
            if "图文" in tab_text and upload_ready:
                print("[cdp_publish] Tab not found but upload input is ready. Continuing...")
                return
            raise CDPError(
                f"Could not find visible '{tab_text}' tab. The page structure may have changed."
            )

        coords = json.loads(pos)
        x, y = coords["x"], coords["y"]
        print(f"[cdp_publish] Found visible tab at ({x}, {y}), clicking via JS + mouse event...")

        # JS click 优先（Vue SPA 导航后 hydration 期间 mouse event 可能不触发）
        self._evaluate(f"""
            (function() {{
                var candidates = document.querySelectorAll(
                    'div.creator-tab, .creator-tab, [class*="creator-tab"], [role="tab"]'
                );
                for (var i = 0; i < candidates.length; i++) {{
                    var t = (candidates[i].textContent || '').trim();
                    if (t.indexOf({tab_text_literal}) !== -1 || t.indexOf('图文') !== -1) {{
                        var r = candidates[i].getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) {{
                            candidates[i].click();
                            return 'clicked:' + t;
                        }}
                    }}
                }}
                return 'not_found';
            }})()
        """)
        self._sleep(0.3, minimum_seconds=0.2)

        # 再补发 mouse event 双保险
        self._send("Input.dispatchMouseEvent", {
            "type": "mousePressed", "x": x, "y": y,
            "button": "left", "clickCount": 1
        })
        time.sleep(0.08)
        self._send("Input.dispatchMouseEvent", {
            "type": "mouseReleased", "x": x, "y": y,
            "button": "left", "clickCount": 1
        })

        print(f"[cdp_publish] Tab '{tab_text}' clicked, waiting for upload area...")
        self._sleep(TAB_CLICK_WAIT, minimum_seconds=0.8)

    def _click_image_text_tab(self):
        """Click the '上传图文' tab to switch to image+text publish mode.
        XHS 默认打开"上传视频"tab，需要点击切换为图文模式。
        使用 Input.dispatchMouseEvent 发送真实鼠标事件以触发 Vue 组件切换。"""
        self._click_tab(SELECTORS["image_text_tab"], SELECTORS["image_text_tab_text"])

        # 验证：确认已切到图文模式（视频上传区消失，或出现图文 input）
        # 不能只查 input[type=file]，视频模式也有 file input
        in_image_mode = self._evaluate("""
            (function() {
                // 图文模式标志：没有"上传视频"按钮，或者有 .img-preview-area
                var hasVideoBtn = !!document.querySelector('.upload-video-btn, [class*="upload-video"]');
                var hasImgArea = !!document.querySelector('.img-preview-area, .upload-img-input, .upload-input');
                // 检查当前激活 tab 文字
                var activeTab = '';
                var tabs = document.querySelectorAll('div.creator-tab, .creator-tab');
                for (var i = 0; i < tabs.length; i++) {
                    if (tabs[i].classList.contains('active') || tabs[i].getAttribute('aria-selected') === 'true') {
                        activeTab = tabs[i].textContent.trim();
                    }
                }
                return JSON.stringify({hasVideoBtn: hasVideoBtn, hasImgArea: hasImgArea, activeTab: activeTab});
            })()
        """)
        print(f"[cdp_publish] Tab mode check: {in_image_mode}")
        upload_ready = self._evaluate(
            f"!!document.querySelector('{SELECTORS['upload_input']}') || "
            f"!!document.querySelector('{SELECTORS['upload_input_alt']}')"
        )
        if not upload_ready:
            raise CDPError(
                f"Failed to switch to '{SELECTORS['image_text_tab_text']}' tab — "
                "upload input not found after click."
            )
        print("[cdp_publish] Image+text tab activated, upload input ready.")

    def _click_video_tab(self):
        """Click the '上传视频' tab to switch to video publish mode."""
        self._click_tab(SELECTORS["video_tab"], SELECTORS["video_tab_text"])

    def _activate_current_tab(self):
        """Bring the current CDP tab to the foreground so JS events fire correctly."""
        if not self._tab_ws_url:
            return
        import re as _re
        m = _re.search(r'/devtools/page/([^/]+)$', self._tab_ws_url)
        if not m:
            return
        target_id = m.group(1)
        try:
            self._send("Target.activateTarget", {"targetId": target_id})
            print(f"[cdp_publish] Tab activated (targetId={target_id})")
        except Exception as e:
            print(f"[cdp_publish] Tab activate failed (non-fatal): {e}")

    def _upload_images(self, image_paths: list[str]):
        """Upload images via the file input element."""
        if not image_paths:
            print("[cdp_publish] No images to upload, skipping.")
            return

        # XHS relies on the change event to show the upload preview.
        # This event is throttled on background tabs, so activate the tab first.
        self._activate_current_tab()
        self._sleep(0.5, minimum_seconds=0.3)

        preserve_flags = [self._should_preserve_upload_path(path) for path in image_paths]
        prepared_paths = [self._prepare_upload_file_path(path) for path in image_paths]

        # 本地模式下先校验文件存在：把不存在的路径交给 setFileInputFiles 会卡死渲染进程
        if _is_local_host(self.host):
            missing = [path for path in prepared_paths if not os.path.isfile(path)]
            if missing:
                raise CDPError(
                    "Image file(s) not found; refusing to call setFileInputFiles "
                    "(it would hang the renderer): " + ", ".join(missing)
                )

        print(f"[cdp_publish] Uploading {len(image_paths)} image(s)...")
        if self.preserve_upload_paths:
            print("[cdp_publish] Upload path normalization disabled; preserving original paths.")
        elif any(preserve_flags):
            print("[cdp_publish] Auto-detected Windows/UNC upload paths; preserving original paths.")

        for index, file_path in enumerate(prepared_paths, start=1):
            node_id = 0
            selectors = (
                (SELECTORS["upload_input"], SELECTORS["upload_input_alt"])
                if index == 1
                else (SELECTORS["upload_input_alt"], SELECTORS["upload_input"])
            )
            for selector in selectors:
                node_id = self._query_node_id(selector)
                if node_id:
                    break

            if not node_id:
                raise CDPError(
                    "Could not find file input element.\n"
                    "The page structure may have changed. Check references/publish-workflow.md."
                )

            self._send("DOM.setFileInputFiles", {
                "nodeId": node_id,
                "files": [file_path],
            })
            # Verify files were set
            self._sleep(0.3, minimum_seconds=0.1)
            file_count = self._evaluate(f"""
                (() => {{
                    const el = document.querySelector({json.dumps(selectors[0])});
                    return el && el.files ? el.files.length : -1;
                }})()
            """)
            print(f"[cdp_publish] Image {index}/{len(prepared_paths)} submitted: {file_path} (files on input: {file_count})")
            if file_count == 0:
                alt_node_id = self._query_node_id(selectors[1] if selectors[1] != selectors[0] else 'input[type="file"]')
                if alt_node_id and alt_node_id != node_id:
                    print(f"[cdp_publish] Retrying with alt selector, nodeId={alt_node_id}")
                    self._send("DOM.setFileInputFiles", {
                        "nodeId": alt_node_id,
                        "files": [file_path],
                    })
                    self._sleep(0.3, minimum_seconds=0.1)
            self._wait_for_uploaded_images(index)
            self._sleep(0.9, minimum_seconds=0.25)

        print("[cdp_publish] Images uploaded. Waiting for editor to appear...")
        self._sleep(UPLOAD_WAIT, minimum_seconds=2.0)

    def _upload_video(self, video_path: str):
        """Upload a video file via the file input element."""
        preserve_path = self._should_preserve_upload_path(video_path)
        prepared_path = self._prepare_upload_file_path(video_path)
        print(f"[cdp_publish] Uploading video: {prepared_path}")
        if self.preserve_upload_paths:
            print("[cdp_publish] Upload path normalization disabled; preserving original paths.")
        elif preserve_path:
            print("[cdp_publish] Auto-detected Windows/UNC upload path; preserving original path.")

        node_id = 0
        for selector in (SELECTORS["upload_input"], SELECTORS["upload_input_alt"]):
            node_id = self._query_node_id(selector)
            if node_id:
                break

        if not node_id:
            raise CDPError(
                "Could not find file input element for video upload.\n"
                "The page structure may have changed."
            )

        # Set the video file
        self._send("DOM.setFileInputFiles", {
            "nodeId": node_id,
            "files": [prepared_path],
        })

        print("[cdp_publish] Video file submitted. Waiting for processing...")

    def _wait_video_processing(self):
        """Wait for the video to finish processing after upload.

        The Xiaohongshu creator page shows a progress/processing indicator
        while the video is being uploaded and transcoded. We wait until the
        publish button becomes clickable, which is more reliable on the
        current creator center than checking title/editor presence alone.
        """
        print("[cdp_publish] Waiting for video processing to complete...")
        deadline = time.time() + VIDEO_PROCESS_TIMEOUT
        last_pct = ""

        while time.time() < deadline:
            if self._is_publish_button_ready():
                print("[cdp_publish] Video processing complete - publish button is ready.")
                self._sleep(1.0, minimum_seconds=0.25)
                return

            # Try to read progress text for user feedback
            pct = self._evaluate("""
                (function() {
                    // Look for progress percentage text
                    var els = document.querySelectorAll(
                        '[class*="progress"], [class*="percent"], [class*="upload"]'
                    );
                    for (var i = 0; i < els.length; i++) {
                        var t = els[i].textContent.trim();
                        if (t && /\\d+%/.test(t)) return t;
                    }
                    return '';
                })()
            """) or ""
            if pct and pct != last_pct:
                print(f"[cdp_publish] Video processing: {pct}")
                last_pct = pct

            time.sleep(VIDEO_PROCESS_POLL)

        raise CDPError(
            f"Video processing did not complete within {VIDEO_PROCESS_TIMEOUT}s. "
            "The video may be too large or processing is slow."
        )

    def _fill_title(self, title: str):
        """Fill in the article title."""
        print(f"[cdp_publish] Setting title: {title[:40]}...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)

        for selector in (SELECTORS["title_input"], SELECTORS["title_input_alt"]):
            found = self._evaluate(f"!!document.querySelector('{selector}')")
            if found:
                escaped_title = json.dumps(title)
                self._evaluate(f"""
                    (function() {{
                        var el = document.querySelector('{selector}');
                        var nativeSetter = Object.getOwnPropertyDescriptor(
                            window.HTMLInputElement.prototype, 'value'
                        ).set;
                        el.removeAttribute('maxlength');
                        el.focus();
                        nativeSetter.call(el, {escaped_title});
                        el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                        el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                        el.blur();
                    }})();
                """)
                print("[cdp_publish] Title set.")
                return

        raise CDPError("Could not find title input element.")

    def _fill_content(self, content: str):
        """Fill in the article body content using the current creator editor."""
        print(f"[cdp_publish] Setting content ({len(content)} chars)...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)
        selector = self._find_content_editor_selector()
        if not selector:
            raise CDPError("Could not find content editor element.")

        escaped = json.dumps(content)
        placeholder_literal = json.dumps(SELECTORS["content_placeholder_text"])
        result = self._evaluate(f"""
            (() => {{
                const selector = {json.dumps(selector)};
                const placeholder = {placeholder_literal};
                let el = document.querySelector(selector);
                if (!(el instanceof HTMLElement) || el.offsetParent === null) {{
                    const candidates = document.querySelectorAll("p[data-placeholder], div[data-placeholder]");
                    for (const node of candidates) {{
                        const value = (node.getAttribute("data-placeholder") || "").trim();
                        if (!value.includes(placeholder)) {{
                            continue;
                        }}
                        let current = node;
                        for (let depth = 0; depth < 5 && current; depth += 1) {{
                            current = current.parentElement;
                            if (
                                current instanceof HTMLElement &&
                                current.getAttribute("role") === "textbox"
                            ) {{
                                el = current;
                                break;
                            }}
                        }}
                        if (el instanceof HTMLElement) {{
                            break;
                        }}
                    }}
                }}

                if (!(el instanceof HTMLElement)) {{
                    return false;
                }}

                const text = {escaped};
                const parts = text.split("\\n");
                const lines = parts.length ? parts : [""];

                el.focus();
                while (el.firstChild) {{
                    el.removeChild(el.firstChild);
                }}

                for (const line of lines) {{
                    const paragraph = document.createElement("p");
                    if (line) {{
                        paragraph.textContent = line;
                    }} else {{
                        paragraph.appendChild(document.createElement("br"));
                    }}
                    el.appendChild(paragraph);
                }}

                el.dispatchEvent(new Event("input", {{ bubbles: true }}));
                el.dispatchEvent(new Event("change", {{ bubbles: true }}));
                return true;
            }})()
        """)
        if not result:
            raise CDPError("Could not set content into creator editor.")

        print(f"[cdp_publish] Content set via selector: {selector}")

    def _set_schedule_post_time(self, post_time: str | None):
        """Set schedule publish time if necessary.

        Rewritten to use two synchronous evaluate calls (no async/await) so
        Chrome cannot GC a Promise while the page is in background/hidden state.
        """
        if post_time is None:
            return

        print(f"[cdp_publish] Setting schedule publish time: {post_time}")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)

        # Override visibilityState so Vue click handlers fire correctly.
        self._evaluate("""
            Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
            Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
        """)

        # Step 1 (synchronous): click the schedule switch if not already enabled.
        switch_result = self._evaluate(f"""
            (function() {{
                var switchEl = document.querySelector({json.dumps(SELECTORS["schedule_switch"])});
                if (!(switchEl instanceof HTMLElement) || switchEl.offsetParent === null) {{
                    return 'missing';
                }}
                if (switchEl.getAttribute('aria-checked') !== 'true') {{
                    switchEl.click();
                    return 'clicked';
                }}
                return 'already_on';
            }})()
        """)

        if switch_result == 'missing':
            raise CDPError("Could not set scheduled publish time. Reason: Schedule publish switch is missing.")

        # Wait for the switch animation with Python sleep — no JS Promise needed.
        if switch_result == 'clicked':
            self._sleep(0.4, minimum_seconds=0.3)

        # Step 2 (synchronous): set the datetime value.
        time_result = self._evaluate(f"""
            (function() {{
                var el = document.querySelector({json.dumps(SELECTORS["schedule_datetime_input"])});
                if (!(el instanceof HTMLInputElement)) {{
                    return 'missing_input';
                }}
                var nativeSetter = Object.getOwnPropertyDescriptor(
                    window.HTMLInputElement.prototype, 'value'
                ).set;
                el.focus();
                el.select();
                nativeSetter.call(el, {json.dumps(post_time)});
                el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                el.dispatchEvent(new Event('blur', {{ bubbles: true }}));
                return 'ok';
            }})()
        """)

        if time_result != 'ok':
            raise CDPError(f"Could not set scheduled publish time. Reason: {time_result}")

        print("[cdp_publish] Schedule publish time set.")
        return

    def _like_note(self):
        """Like the current note."""
        print("[cdp_publish] Liking note...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)

        liked = self._evaluate("""
            (function() {{
                // Try various like button selectors
                var selectors = [
                    '.like-button, [class*="like"], [class*="heart"]',
                    'button[aria-label*="like"], button[aria-label*="赞"]',
                    '[data-testid*="like"], [data-testid*="heart"]',
                    'svg[class*="like"], svg[class*="heart"]'
                ];

                for (var sel of selectors) {{
                    var elements = document.querySelectorAll(sel);
                    for (var el of elements) {{
                        // Check if it's not already liked
                        if (!el.classList.contains('liked') && !el.classList.contains('active')) {{
                            el.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }})();
        """)

        if liked:
            print("[cdp_publish] Note liked.")
        else:
            print("[cdp_publish] Could not find like button or already liked.")

        return liked

    def _collect_note(self):
        """Collect the current note."""
        print("[cdp_publish] Collecting note...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)

        collected = self._evaluate("""
            (function() {{
                // Try various collect button selectors
                var selectors = [
                    '.collect-button, [class*="collect"], [class*="bookmark"]',
                    'button[aria-label*="collect"], button[aria-label*="收藏"]',
                    '[data-testid*="collect"], [data-testid*="bookmark"]',
                    'svg[class*="collect"], svg[class*="bookmark"]'
                ];

                for (var sel of selectors) {{
                    var elements = document.querySelectorAll(sel);
                    for (var el of elements) {{
                        // Check if it's not already collected
                        if (!el.classList.contains('collected') && !el.classList.contains('active')) {{
                            el.click();
                            return true;
                        }}
                    }}
                }}
                return false;
            }})();
        """)

        if collected:
            print("[cdp_publish] Note collected.")
        else:
            print("[cdp_publish] Could not find collect button or already collected.")

        return collected

    def _move_mouse(self, x: float, y: float):
        """Move mouse cursor via CDP to support hover-driven UI."""
        self._send("Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": float(x),
            "y": float(y),
        })

    def _click_mouse(self, x: float, y: float):
        """Perform a real left-click via CDP at the given coordinates."""
        for event_type in ("mousePressed", "mouseReleased"):
            self._send("Input.dispatchMouseEvent", {
                "type": event_type,
                "x": float(x),
                "y": float(y),
                "button": "left",
                "clickCount": 1,
            })
            time.sleep(0.05)

    def _click_element_by_cdp(self, description: str, js_get_rect: str):
        """Click an element using CDP Input.dispatchMouseEvent for reliable clicks.

        Modern web frameworks (Vue/React) often ignore JS .click() calls.
        Dispatching real mouse events via CDP always works.

        Args:
            description: Human-readable description for logging.
            js_get_rect: JavaScript expression that returns {x, y, width, height}
                         of the element to click, or null if not found.
        """
        rect = self._evaluate(js_get_rect)
        if not rect:
            raise CDPError(
                f"Could not find {description}. "
                "Please click it manually in the browser."
            )

        # Compute center of the element
        cx = rect["x"] + rect["width"] / 2
        cy = rect["y"] + rect["height"] / 2
        print(f"[cdp_publish] Clicking {description} at ({cx:.0f}, {cy:.0f})...")

        # Dispatch a full mouse click sequence via CDP
        for event_type in ("mousePressed", "mouseReleased"):
            self._send("Input.dispatchMouseEvent", {
                "type": event_type,
                "x": cx,
                "y": cy,
                "button": "left",
                "clickCount": 1,
            })
            time.sleep(0.05)

    def _click_publish_button_inner(self) -> bool:
        """直接对闭合 shadow DOM 里的真按钮调 .click()。

        xhs-publish-btn 的按钮位于闭合 shadow root 内，页面 JS 拿不到；但 CDP 的
        DOM.describeNode(pierce=True) 能穿透找到它，DOM.resolveNode 拿到 JS 句柄后
        直接调 .click()。比合成坐标鼠标事件可靠得多（后者对 shadow DOM 按钮经常
        不被 Vue 接受）。
        """
        try:
            self._send("DOM.enable")
            doc = self._send("DOM.getDocument", {"depth": 1})
            host_ids = self._send("DOM.querySelectorAll", {
                "nodeId": doc["root"]["nodeId"],
                "selector": SELECTORS["publish_button"],
            }).get("nodeIds", [])
            if not host_ids:
                return False
            desc = self._send("DOM.describeNode", {
                "nodeId": host_ids[0], "depth": -1, "pierce": True,
            })
            shadow_roots = desc["node"].get("shadowRoots", [])
            if not shadow_roots:
                return False
            inner_ids = self._send("DOM.querySelectorAll", {
                "nodeId": shadow_roots[0]["nodeId"],
                "selector": ".ce-btn.bg-red",
            }).get("nodeIds", [])
            if not inner_ids:
                return False
            resolved = self._send("DOM.resolveNode", {"nodeId": inner_ids[0]})
            object_id = resolved.get("object", {}).get("objectId")
            if not object_id:
                return False
            result = self._send("Runtime.callFunctionOn", {
                "objectId": object_id,
                "functionDeclaration": "function(){ this.click(); return true; }",
                "returnByValue": True,
            })
            try:
                self._send("Runtime.releaseObject", {"objectId": object_id})
            except Exception:
                pass
            return bool(result and result.get("result", {}).get("value"))
        except Exception as e:
            print(f"[cdp_publish] inner publish click failed: {e}")
            return False

    def _click_publish(self, scheduled: bool = False):
        """Click the publish button using CDP mouse events."""
        print("[cdp_publish] Clicking publish button...")
        self._sleep(ACTION_INTERVAL, minimum_seconds=0.25)
        self._wait_for_publish_button_ready(timeout_seconds=20.0)

        # Override visibilityState so the Vue click handler fires even when
        # the Chrome window has no active display (VNC disconnected).
        self._evaluate("""
            Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
            Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
        """)

        # Scroll publish button into viewport before clicking.
        self._evaluate("""
            var btn = document.querySelector('xhs-publish-btn');
            if (btn) btn.scrollIntoView({block: 'center', behavior: 'instant'});
        """)
        self._sleep(0.3, minimum_seconds=0.3)

        rect = self._get_publish_button_rect()
        if not rect:
            raise CDPError(
                "Could not find publish button. "
                "The creator center page structure may have changed."
            )

        cx = rect["x"] + rect["width"] / 2
        cy = rect["y"] + rect["height"] / 2

        # 点击前记录页面已有的 24 位 hex（用于识别"点击后新出现"的笔记 id，
        # 避免把页面上本来就存在的其他笔记 id 误当成本次发布成功）
        baseline_ids = set()
        try:
            raw0 = self._evaluate("(function(){var m=(document.body.innerText||'').match(/\\b[0-9a-fA-F]{24}\\b/g)||[];return JSON.stringify(m);})()")
            baseline_ids = set(json.loads(raw0) or [])
        except Exception:
            pass

        # 优先直接对闭合 shadow DOM 里的真按钮调 .click()；坐标点击仅作兜底。
        if self._click_publish_button_inner():
            print("[cdp_publish] Publish button clicked (inner shadow DOM .click()).")
        else:
            print(f"[cdp_publish] Inner click unavailable; falling back to coordinate click at ({cx:.0f}, {cy:.0f})...")
            self._click_mouse(cx, cy)
            print("[cdp_publish] Publish button clicked (coordinate).")

        # 等发布确认：只认强信号。确认不了就抛错 —— 让上层不写库、稿子留在待发队列。
        note_link = None
        confirmed = False
        deadline = time.time() + 20.0
        while time.time() < deadline:
            self._sleep(1.0, minimum_seconds=0.5)
            raw = self._evaluate("""
                (function() {
                    var links = document.querySelectorAll('a[href*="xiaohongshu.com/explore"]');
                    var btn = document.querySelector('xhs-publish-btn');
                    var titleEl = document.querySelector('div.d-input input');
                    var previews = document.querySelectorAll('.img-preview-area .pr').length;
                    var chips = document.querySelectorAll('div.ProseMirror a.tiptap-topic').length;
                    var text = document.body.innerText || '';
                    var ids = text.match(/\\b[0-9a-fA-F]{24}\\b/g) || [];
                    return JSON.stringify({
                        ids: ids,
                        link: links.length ? links[0].href : null,
                        url: location.href,
                        loading: btn ? btn.getAttribute('submit-loading') : null,
                        titleEmpty: !titleEl || !titleEl.value,
                        previews: previews,
                        chips: chips,
                        successText: /发布成功|定时发布成功|已提交|发布完成/.test(text)
                    });
                })();
            """)
            try:
                info = json.loads(raw) if raw else {}
            except Exception:
                info = {}
            if info.get("link"):
                note_link = info["link"]
                confirmed = True
                break
            new_ids = [i for i in (info.get("ids") or []) if i not in baseline_ids]
            if new_ids:
                note_link = "https://www.xiaohongshu.com/explore/" + new_ids[0]
                confirmed = True
                break
            if "published=true" in (info.get("url") or ""):
                confirmed = True
                break
            if "/new/home" in (info.get("url") or ""):
                confirmed = True
                break
            if info.get("successText"):
                confirmed = True
                break
            if info.get("titleEmpty") and info.get("previews") == 0 and info.get("chips") == 0:
                confirmed = True
                break

        if confirmed:
            print("[cdp_publish] Publish confirmed.")
            return note_link

        raise CDPError(
            "Publish NOT confirmed within 20s "
            "(no published=true / note link / success text / form reset). "
            "Leaving article unmarked so it stays in the pending queue."
        )

    # ------------------------------------------------------------------
    # Main publish workflow
    # ------------------------------------------------------------------

    def publish(
        self,
        title: str,
        content: str,
        image_paths: list[str] | None = None,
        post_time: str | None = None,
    ):
        """
        Execute the full publish workflow:
        1. Navigate to creator publish page
        2. Click '上传图文' tab
        3. Upload images (this triggers the editor to appear)
        4. Fill title
        5. Fill content
        6. Set schedule publish time (if necessary)

        Args:
            title: Article title
            content: Article body text (paragraphs separated by newlines)
            image_paths: List of local file paths to images to upload
            post_time: Optional scheduled publish time (e.g. "2026-03-01 10:00")
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        if not image_paths:
            raise CDPError("At least one image is required to publish on Xiaohongshu.")
        
        if post_time and not validate_schedule_post_time(post_time):
            raise CDPError(
                "Scheduled publish time is invalid. "
                "It must follow the format 'yyyy-MM-dd HH:mm' and fall within the next 14 days."
            )

        # Step 1: Navigate to publish page
        # 先 dismiss 任何 beforeunload 弹窗（上次发布失败后可能残留），再强制 reload
        try:
            self._send("Page.handleJavaScriptDialog", {"accept": True})
        except Exception:
            pass
        current_url = self._evaluate("location.href") or ""
        if "creator.xiaohongshu.com" in current_url:
            # 已在 creator 域 — navigate 到干净 URL（不做 reload，
            # 避免 ?published=true 等残留参数重载到非表单页）
            print("[cdp_publish] Already on creator page, navigating to clean URL...")
            try:
                self._send("Page.handleJavaScriptDialog", {"accept": True})
            except Exception:
                pass
            self._navigate(XHS_CREATOR_URL)
        else:
            self._navigate(XHS_CREATOR_URL)
        self._sleep(2, minimum_seconds=1.0)

        # Step 2: Click '上传图文' tab
        self._click_image_text_tab()

        # Step 3: Upload images (editor appears after upload)
        self._upload_images(image_paths)

        # Step 4: Fill title
        self._fill_title(title)

        # Step 5: Fill content
        self._fill_content(content)

        # Step 6: Set schedule publish time (if provided)
        self._set_schedule_post_time(post_time)

        print(
            "\n[cdp_publish] Content has been filled in.\n"
            "  Please review in the browser before publishing.\n"
        )

    def publish_video(
        self,
        title: str,
        content: str,
        video_path: str,
    ):
        """
        Execute the full video publish workflow:
        1. Navigate to creator publish page
        2. Click '上传视频' tab
        3. Upload video file and wait for processing
        4. Fill title
        5. Fill content

        Args:
            title: Article title
            content: Article body text (paragraphs separated by newlines)
            video_path: Local file path to the video to upload
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        if not video_path:
            raise CDPError("A video file is required to publish video on Xiaohongshu.")

        # Step 1: Navigate to publish page
        self._navigate(XHS_CREATOR_URL)
        time.sleep(2)

        # Step 2: Click '上传视频' tab
        self._click_video_tab()

        # Step 3: Upload video and wait for processing
        self._upload_video(video_path)
        self._wait_video_processing()

        # Step 4: Fill title
        self._fill_title(title)

        # Step 5: Fill content
        self._fill_content(content)

        print(
            "\n[cdp_publish] Video content has been filled in.\n"
            "  Please review in the browser before publishing.\n"
        )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

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
