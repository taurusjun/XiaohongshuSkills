"""内容数据/通知 —— 从 cdp_publish.py 抽出的 mixin（P6 拆分第 5 步）。"""
import base64
import json
import os
import time
from typing import Any

from xhs_errors import CDPError
from xhs_constants import (
    XHS_CONTENT_DATA_URL, XHS_CONTENT_DATA_API_PATH,
    XHS_NOTIFICATION_URL, XHS_NOTIFICATION_MENTIONS_API_PATH,
)
from xhs_util import _map_note_infos_to_content_rows, _write_content_data_csv


class StatsMixin:
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

