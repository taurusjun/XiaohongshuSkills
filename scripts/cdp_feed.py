"""搜索/详情/评论/profile —— 从 cdp_publish.py 抽出的 mixin（P6 拆分第 3 步）。"""
import base64
import json
import time
from typing import Any

from feed_explorer import (
    SEARCH_BASE_URL, LOCATION_OPTIONS, NOTE_TYPE_OPTIONS, PUBLISH_TIME_OPTIONS,
    SEARCH_SCOPE_OPTIONS, SORT_BY_OPTIONS, FeedExplorer, FeedExplorerError,
    SearchFilters, make_feed_detail_url, make_search_url,
)
from xhs_errors import CDPError
from xhs_constants import (
    XHS_HOME_URL, XHS_CREATOR_URL, PAGE_LOAD_WAIT, XHS_FEED_INACCESSIBLE_KEYWORDS, SELECTORS,
    XHS_SEARCH_RECOMMEND_API_PATH,
)
from xhs_util import (
    _build_search_filters_from_args, _format_post_time, _metric_or_dash,
)


class FeedMixin:
    def _prepare_search_input_keyword(self, keyword: str) -> dict[str, Any]:
        """Focus search input and type keyword without submitting."""
        keyword_literal = json.dumps(keyword, ensure_ascii=False)
        result = self._evaluate(f"""
            (async () => {{
                const keyword = {keyword_literal};
                const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

                const isVisible = (node) => {{
                    if (!(node instanceof HTMLElement)) {{
                        return false;
                    }}
                    if (node.offsetParent === null) {{
                        return false;
                    }}
                    const rect = node.getBoundingClientRect();
                    return rect.width >= 8 && rect.height >= 8;
                }};

                const selectors = [
                    "#search-input",
                    "input.search-input",
                    "input[type='search']",
                    "input[placeholder*='搜索']",
                    "[class*='search'] input",
                ];

                let inputEl = null;
                for (const selector of selectors) {{
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {{
                        if (!(node instanceof HTMLInputElement || node instanceof HTMLTextAreaElement)) {{
                            continue;
                        }}
                        if (node.disabled || !isVisible(node)) {{
                            continue;
                        }}
                        inputEl = node;
                        break;
                    }}
                    if (inputEl) {{
                        break;
                    }}
                }}

                if (!inputEl) {{
                    return {{ ok: false, reason: "search_input_not_found" }};
                }}

                const setValue = (value) => {{
                    const proto = inputEl instanceof HTMLTextAreaElement
                        ? HTMLTextAreaElement.prototype
                        : HTMLInputElement.prototype;
                    const descriptor = Object.getOwnPropertyDescriptor(proto, "value");
                    if (descriptor && typeof descriptor.set === "function") {{
                        descriptor.set.call(inputEl, value);
                    }} else {{
                        inputEl.value = value;
                    }}
                    inputEl.dispatchEvent(new Event("input", {{ bubbles: true }}));
                    inputEl.dispatchEvent(new Event("change", {{ bubbles: true }}));
                }};

                inputEl.focus();
                await sleep(120);
                setValue("");
                await sleep(80);

                let typed = "";
                for (const ch of Array.from(keyword)) {{
                    typed += ch;
                    setValue(typed);
                    await sleep(55 + Math.floor(Math.random() * 70));
                }}
                await sleep(220);
                return {{ ok: true, reason: "" }};
            }})()
        """)
        if not isinstance(result, dict):
            return {"ok": False, "reason": "unexpected_result"}
        reason = result.get("reason")
        return {
            "ok": bool(result.get("ok")),
            "reason": reason if isinstance(reason, str) else "unknown",
        }

    def _extract_recommend_keywords_from_payload(
        self,
        payload: dict[str, Any],
        keyword: str,
        max_suggestions: int,
    ) -> list[str]:
        """Extract recommendation keywords from search recommend API payload."""
        ignored_texts = {
            "历史记录",
            "猜你想搜",
            "相关搜索",
            "热门搜索",
            "大家都在搜",
            "清空历史",
            "删除历史",
        }

        def normalize_text(value: str) -> str:
            return " ".join(value.split()).strip()

        def push_text(output: list[str], seen: set[str], value: str):
            normalized = normalize_text(value)
            if not normalized or normalized == keyword:
                return
            if normalized in ignored_texts:
                return
            if len(normalized) < 2 or len(normalized) > 36:
                return
            if normalized in seen:
                return
            seen.add(normalized)
            output.append(normalized)

        ordered: list[str] = []
        seen: set[str] = set()
        stack: list[Any] = [payload]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for key, value in node.items():
                    if isinstance(value, str):
                        key_lc = key.lower()
                        if any(
                            hint in key_lc
                            for hint in (
                                "word",
                                "query",
                                "keyword",
                                "text",
                                "title",
                                "name",
                                "suggest",
                            )
                        ):
                            push_text(ordered, seen, value)
                        continue
                    if isinstance(value, (dict, list)):
                        stack.append(value)
            elif isinstance(node, list):
                for item in node:
                    if isinstance(item, str):
                        push_text(ordered, seen, item)
                        continue
                    if isinstance(item, (dict, list)):
                        stack.append(item)

        keyword_prefix = keyword[:2]
        ranked: list[tuple[int, int, str]] = []
        for idx, text in enumerate(ordered):
            score = 0
            if keyword and (keyword in text or text in keyword):
                score += 3
            elif keyword_prefix and keyword_prefix in text:
                score += 1
            ranked.append((score, idx, text))
        ranked.sort(key=lambda item: (-item[0], item[1]))
        return [item[2] for item in ranked[: max(1, max_suggestions)]]

    def _capture_search_recommendations_via_network(
        self,
        keyword: str,
        wait_seconds: float = 8.0,
        max_suggestions: int = 12,
    ) -> dict[str, Any]:
        """Capture recommend API response from real page traffic."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        self._send("Network.enable", {"maxPostDataSize": 65536})
        self._send("Network.setCacheDisabled", {"cacheDisabled": True})

        typed = self._prepare_search_input_keyword(keyword)
        if not typed.get("ok"):
            reason = typed.get("reason") or "type_keyword_failed"
            return {"ok": False, "reason": str(reason), "suggestions": []}

        deadline = time.time() + max(2.0, float(wait_seconds))
        request_meta_by_id: dict[str, dict[str, str]] = {}
        exact_match: tuple[str, str] | None = None
        fallback_match: tuple[str, str] | None = None

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

            if method != "Network.responseReceived":
                continue

            request_id = params.get("requestId")
            if not isinstance(request_id, str):
                continue

            request_meta = request_meta_by_id.get(request_id, {})
            request_url = request_meta.get("url", "")
            if XHS_SEARCH_RECOMMEND_API_PATH not in request_url:
                continue
            if request_meta.get("method") == "OPTIONS":
                continue

            status = int(params.get("response", {}).get("status") or 0)
            if status != 200:
                continue

            fallback_match = (request_id, request_url)
            try:
                query = parse_qs(urlparse(request_url).query)
                request_keyword = (query.get("keyword") or [""])[0].strip()
            except Exception:
                request_keyword = ""

            if request_keyword == keyword:
                exact_match = (request_id, request_url)
                break

        target = exact_match or fallback_match
        if not target:
            return {"ok": False, "reason": "recommend_request_timeout", "suggestions": []}

        request_id, request_url = target
        body_result = self._send("Network.getResponseBody", {"requestId": request_id})
        body_text = body_result.get("body", "")
        if body_result.get("base64Encoded"):
            body_text = base64.b64decode(body_text).decode("utf-8", errors="replace")

        try:
            payload = json.loads(body_text)
        except json.JSONDecodeError:
            return {"ok": False, "reason": "recommend_invalid_json", "suggestions": []}
        if not isinstance(payload, dict):
            return {"ok": False, "reason": "recommend_invalid_payload", "suggestions": []}

        suggestions = self._extract_recommend_keywords_from_payload(
            payload=payload,
            keyword=keyword,
            max_suggestions=max_suggestions,
        )
        return {
            "ok": True,
            "reason": "",
            "request_url": request_url,
            "suggestions": suggestions,
        }

    def _select_sort_newest(self) -> bool:
        """Open the filter panel on a search_result page and click 「最新」.

        Uses JS .click() to open the panel (which works for the toggle button),
        then uses real CDP Input.dispatchMouseEvent to click the 「最新」 tag
        (JS .click() alone does not trigger Vue's reactivity on filter tags).

        Returns True if 最新 was successfully selected, False otherwise.
        """
        # Step 1: open panel via JS click on 「筛选」
        clicked = self._evaluate("""
(function(){
    var btn = document.querySelector('.filter');
    if (!btn) return false;
    btn.click();
    return true;
})()
""")
        if not clicked:
            return False
        self._sleep(0.8, minimum_seconds=0.6)

        # Step 2: wait for panel to become visible
        for _ in range(8):
            visible = self._evaluate(
                "(function(){"
                "var p=document.querySelector('.filter-panel');"
                "return !!(p && window.getComputedStyle(p).display !== 'none');"
                "})()"
            )
            if visible:
                break
            self._sleep(0.3, minimum_seconds=0.2)
        else:
            return False

        # Step 3: get BCR coords of 「最新」 tag and do a real CDP mouse click
        coords = self._evaluate("""
(function(){
    var panel = document.querySelector('.filter-panel');
    if (!panel) return null;
    var target = Array.from(panel.querySelectorAll('.tags')).find(function(el){
        return (el.innerText || '').trim() === '最新';
    });
    if (!target) return null;
    var r = target.getBoundingClientRect();
    if (r.width === 0) return null;
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
})()
""")
        if not coords or not coords.get("x"):
            return False

        self._mouse_click(coords["x"], coords["y"])
        self._sleep(0.5, minimum_seconds=0.3)

        # Dismiss the filter panel: move mouse away (panel hides on mouse-out)
        self._send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": 50, "y": 300,
        })
        self._sleep(0.3, minimum_seconds=0.2)
        return True

    def _select_time_filter_1day(self) -> bool:
        """在筛选面板中点击「一天内」发布时间筛选。需要面板已打开。"""
        coords = self._evaluate("""
(function(){
    var panel = document.querySelector('.filter-panel');
    if (!panel) return null;
    var target = Array.from(panel.querySelectorAll('.tags')).find(function(el){
        return (el.innerText || '').trim() === '一天内';
    });
    if (!target) return null;
    var r = target.getBoundingClientRect();
    if (r.width === 0) return null;
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
})()
""")
        if not coords or not coords.get("x"):
            return False
        self._mouse_click(coords["x"], coords["y"])
        self._sleep(0.5, minimum_seconds=0.3)
        self._send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 50, "y": 300})
        self._sleep(0.3, minimum_seconds=0.2)
        return True

    def _open_filter_panel(self) -> bool:
        """打开搜索筛选面板，返回是否成功。"""
        clicked = self._evaluate("""
(function(){
    var btn = document.querySelector('.filter');
    if (!btn) return false;
    btn.click();
    return true;
})()
""")
        if not clicked:
            return False
        self._sleep(0.8, minimum_seconds=0.6)
        for _ in range(8):
            visible = self._evaluate(
                "(function(){"
                "var p=document.querySelector('.filter-panel');"
                "return !!(p && window.getComputedStyle(p).display !== 'none');"
                "})()"
            )
            if visible:
                return True
            self._sleep(0.3, minimum_seconds=0.2)
        return False

    def search_feeds(
        self,
        keyword: str,
        filters: SearchFilters | None = None,
        sort: str = "newest",
        time_filter: str = "",
    ) -> dict[str, Any]:
        """
        Search Xiaohongshu feeds by keyword and optional filters.

        Args:
            keyword: Search keyword.
            filters: Optional search filters.
            sort: Sort order — "newest" (default) or "general".
                  "newest" opens the filter panel and clicks 「最新」 after navigation.

        Returns:
            {
                "keyword": str,
                "recommended_keywords": list[str],
                "feeds": list[dict[str, Any]],
            }
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        keyword = keyword.strip()
        if not keyword:
            raise CDPError("Keyword cannot be empty.")

        explorer = FeedExplorer(
            self._evaluate,
            self._sleep,
            move_mouse=self._move_mouse,
            click_mouse=self._click_mouse,
        )

        # Capture recommendations in parallel with the keyword navigation
        recommendation_result = self._capture_search_recommendations_via_network(keyword=keyword)
        recommended_keywords = recommendation_result.get("suggestions", [])

        if not recommendation_result.get("ok"):
            reason = recommendation_result.get("reason") or "recommend_api_failed"
            print(
                "[cdp_publish] Warning: failed to capture search recommendations via API. "
                f"reason={reason}"
            )

        # Navigate straight to keyword URL — single navigate, no double-jump
        search_url = make_search_url(keyword)
        self._navigate(search_url)
        self._sleep(2, minimum_seconds=1.0)

        # 检测「安全验证/频率限制」弹窗
        rate_limited = self._evaluate("""
(function(){
    var texts = document.body?.innerText || '';
    return texts.includes('请勿频繁操作') || texts.includes('安全验证') || texts.includes('稍后重试');
})()
""")
        if rate_limited:
            raise XHSRateLimitError(
                f"search_feeds: 触发小红书频率限制（安全验证弹窗），keyword='{keyword}'"
            )

        # Select sort order and optional time filter via filter panel
        need_panel = (sort == "newest") or bool(time_filter)
        if need_panel:
            panel_ok = self._open_filter_panel()
            if panel_ok:
                if sort == "newest":
                    sort_ok = self._evaluate("""
(function(){
    var panel = document.querySelector('.filter-panel');
    if (!panel) return false;
    var target = Array.from(panel.querySelectorAll('.tags')).find(function(el){
        return (el.innerText || '').trim() === '最新';
    });
    if (!target) return null;
    var r = target.getBoundingClientRect();
    return {x: Math.round(r.x + r.width / 2), y: Math.round(r.y + r.height / 2)};
})()
""")
                    if sort_ok and sort_ok.get("x"):
                        self._mouse_click(sort_ok["x"], sort_ok["y"])
                        self._sleep(0.5, minimum_seconds=0.3)
                        print("[cdp_publish] Sort set to 最新.")
                    else:
                        print("[cdp_publish] Warning: failed to select 最新 sort, using default.")

                if time_filter == "1day":
                    tf_ok = self._select_time_filter_1day()
                    if tf_ok:
                        print("[cdp_publish] Time filter set to 一天内.")
                    else:
                        print("[cdp_publish] Warning: failed to select 一天内 time filter.")

                # Dismiss panel
                self._send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": 50, "y": 300})
                self._sleep(1.0, minimum_seconds=0.8)
            else:
                print("[cdp_publish] Warning: failed to open filter panel.")

        try:
            feeds = explorer.search_feeds(keyword=keyword, filters=filters)
        except FeedExplorerError as e:
            raise CDPError(str(e)) from e

        if not feeds:
            raise CDPError(
                f"search_feeds: keyword='{keyword}' 返回0条结果。"
                "可能原因：未登录小红书、关键词无内容、或页面加载超时。"
            )
        print(
            f"[cdp_publish] Search completed. keyword={keyword}, "
            f"recommended_keywords={len(recommended_keywords)}, feeds={len(feeds)}"
        )
        return {
            "keyword": keyword,
            "recommended_keywords": recommended_keywords,
            "feeds": feeds,
        }

    def list_feeds(self) -> dict[str, Any]:
        """Get home recommendation feed list from current logged-in home page."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        self._navigate(XHS_HOME_URL)
        self._sleep(2, minimum_seconds=1.0)

        explorer = FeedExplorer(self._evaluate, self._sleep)
        try:
            feeds = explorer.list_feeds()
        except FeedExplorerError as e:
            raise CDPError(str(e)) from e

        print(f"[cdp_publish] Home feeds loaded. count={len(feeds)}")
        return {
            "count": len(feeds),
            "feeds": feeds,
        }

    def fetch_note_stats(self, note_url: str) -> dict[str, Any]:
        """
        Get interaction stats for a published note via creator dashboard API.

        Navigates to creator.xiaohongshu.com data-center, intercepts the page's
        analyze/list API response (which includes X-s/X-t signing) via CDP Network.

        Returns {"views": int|None, "likes": int|None, "saves": int|None, "comments": int|None}.
        On failure returns {} without raising.
        """
        if not self.ws:
            print("[cdp_publish] Warning: not connected, cannot fetch note stats.")
            return {}
        try:
            import json as _json, time as _time, re as _re

            # Enable Network on the main connection
            self._send("Network.enable")

            # Send navigation (don't use _navigate — it consumes unsolicited events)
            self._send("Page.enable")
            nav_id = self._msg_id + 1
            self._msg_id = nav_id
            self.ws.send(_json.dumps({
                "id": nav_id, "method": "Page.navigate",
                "params": {"url": "https://creator.xiaohongshu.com/statistics/data-analysis?source=official"},
            }))

            # Collect all messages — both command responses and unsolicited Network events
            request_ids = []
            nav_done = False
            deadline = _time.time() + 25

            while _time.time() < deadline:
                try:
                    raw = self.ws.recv(timeout=2)
                    msg = _json.loads(raw)
                    msg_id = msg.get("id", 0)
                    m = msg.get("method", "")

                    # Track navigation completion
                    if msg_id == nav_id:
                        nav_done = True

                    # Track analyze/list request
                    if m == "Network.responseReceived":
                        url = msg.get("params", {}).get("response", {}).get("url", "")
                        if "analyze/list" in url:
                            request_ids.append(msg["params"]["requestId"])

                    # When analyze/list finishes loading, grab the body
                    if m == "Network.loadingFinished":
                        pid = msg.get("params", {}).get("requestId", "")
                        if pid in request_ids:
                            get_body_id = self._msg_id + 1
                            self._msg_id = get_body_id
                            self.ws.send(_json.dumps({
                                "id": get_body_id,
                                "method": "Network.getResponseBody",
                                "params": {"requestId": pid},
                            }))
                            # Read the body response
                            body_raw = _json.loads(self.ws.recv())
                            body = body_raw.get("result", {}).get("body", "")

                            if body and '"note_infos"' in body:
                                data = _json.loads(body)
                                notes = data.get("data", {}).get("note_infos", [])

                                note_id = ""
                                m = _re.search(r'/explore/([a-f0-9]+)', note_url)
                                if m:
                                    note_id = m.group(1)

                                for n in notes:
                                    if note_id and n.get("id") == note_id:
                                        return {
                                            "views": n.get("read_count"),
                                            "likes": n.get("like_count"),
                                            "saves": n.get("fav_count"),
                                            "comments": n.get("comment_count"),
                                        }
                                break  # Not the note we want? return empty
                except Exception:
                    continue

            # Drain remaining messages and reconnect if needed
            if nav_done:
                try:
                    self._reconnect()
                except Exception:
                    pass

            print("[cdp_publish] Could not capture analyze/list response")
            return {}
        except Exception as e:
            print(f"[cdp_publish] fetch_note_stats failed: {e}")
            return {}

    def _extract_feed_comments_state(self) -> dict[str, Any]:
        """Read current comment loading state from feed detail page DOM."""
        result = self._evaluate(r"""
            (() => {
                const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 6 &&
                    node.getBoundingClientRect().height > 6
                );

                const countVisible = (selector) => {
                    const nodes = document.querySelectorAll(selector);
                    let count = 0;
                    for (const node of nodes) {
                        if (visible(node)) {
                            count += 1;
                        }
                    }
                    return count;
                };

                let parentCommentCount = 0;
                const parentSelectors = [
                    ".parent-comment",
                    "[class*='parent-comment']",
                    ".comments-container [class*='comment-item']",
                ];
                for (const selector of parentSelectors) {
                    parentCommentCount = countVisible(selector);
                    if (parentCommentCount > 0) {
                        break;
                    }
                }

                let totalComments = 0;
                const totalSelectors = [
                    ".comments-container .total",
                    ".comments-container [class*='total']",
                ];
                for (const selector of totalSelectors) {
                    const node = document.querySelector(selector);
                    if (!(node instanceof HTMLElement)) {
                        continue;
                    }
                    const text = normalize(node.innerText || node.textContent);
                    const match = text.match(/共\s*(\d+)\s*条评论/);
                    if (match) {
                        totalComments = Number.parseInt(match[1], 10) || 0;
                        break;
                    }
                }

                const noCommentSelectors = [
                    ".no-comments-text",
                    "[class*='no-comments']",
                    "[class*='empty']",
                ];
                let noComments = false;
                for (const selector of noCommentSelectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!visible(node)) {
                            continue;
                        }
                        const text = normalize(node.innerText || node.textContent);
                        if (text.includes("这是一片荒地")) {
                            noComments = true;
                            break;
                        }
                    }
                    if (noComments) {
                        break;
                    }
                }

                const endSelectors = [
                    ".end-container",
                    "[class*='end-container']",
                ];
                let endDetected = false;
                let endText = "";
                for (const selector of endSelectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!visible(node)) {
                            continue;
                        }
                        const text = normalize(node.innerText || node.textContent).toUpperCase();
                        if (text.includes("THE END") || text.includes("THEEND")) {
                            endDetected = true;
                            endText = text;
                            break;
                        }
                    }
                    if (endDetected) {
                        break;
                    }
                }

                return {
                    parent_comment_count: parentCommentCount,
                    total_comments: totalComments,
                    no_comments: noComments,
                    end_detected: endDetected,
                    end_text: endText,
                    scroll_top: window.pageYOffset || document.documentElement.scrollTop || document.body.scrollTop || 0,
                };
            })()
        """)
        return result if isinstance(result, dict) else {
            "parent_comment_count": 0,
            "total_comments": 0,
            "no_comments": False,
            "end_detected": False,
            "end_text": "",
            "scroll_top": 0,
        }

    def _scroll_feed_comments_area(
        self,
        speed: str = "normal",
        large_mode: bool = False,
        push_count: int = 1,
    ):
        """Scroll feed detail comments area to trigger lazy loading."""
        speed_key = (speed or "normal").strip().lower()
        delta_map = {
            "slow": 260,
            "normal": 520,
            "fast": 860,
        }
        delta = delta_map.get(speed_key, delta_map["normal"])
        if large_mode:
            delta = int(delta * 1.9)
        push_count = max(1, int(push_count))

        for _ in range(push_count):
            self._evaluate(f"""
                (() => {{
                    const commentRoot = document.querySelector('.comments-container');
                    if (commentRoot instanceof HTMLElement) {{
                        try {{
                            commentRoot.scrollIntoView({{ behavior: 'instant', block: 'start' }});
                        }} catch (error) {{}}
                    }}

                    const parents = document.querySelectorAll('.parent-comment, [class*="parent-comment"]');
                    if (parents.length) {{
                        const last = parents[parents.length - 1];
                        if (last instanceof HTMLElement) {{
                            try {{
                                last.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                            }} catch (error) {{}}
                        }}
                    }}

                    const target = document.querySelector('.note-scroller')
                        || document.querySelector('.interaction-container')
                        || document.querySelector('.comments-container')
                        || document.documentElement;

                    const event = new WheelEvent('wheel', {{
                        deltaY: {delta},
                        deltaMode: 0,
                        bubbles: true,
                        cancelable: true,
                        view: window,
                    }});
                    target.dispatchEvent(event);
                    window.scrollBy(0, {delta});
                    return true;
                }})()
            """)
            self._sleep(0.45 if speed_key == "fast" else 0.75 if speed_key == "normal" else 1.05, minimum_seconds=0.15)

    def _click_more_reply_buttons(
        self,
        reply_limit: int = 10,
        max_clicks: int = 6,
    ) -> dict[str, int]:
        """Click visible 'more replies' buttons on feed detail page."""
        threshold = max(0, int(reply_limit))
        max_clicks = max(1, int(max_clicks))
        result = self._evaluate(rf"""
            (() => {{
                const normalize = (text) => (text || '').replace(/\s+/g, ' ').trim();
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 6 &&
                    node.getBoundingClientRect().height > 6
                );
                const root = document.querySelector('.comments-container') || document.body;
                const selectors = [
                    '.show-more',
                    '[class*="show-more"]',
                    'button',
                    '[role="button"]',
                    'span',
                    'div',
                    'a',
                ];
                const seen = new Set();
                let clicked = 0;
                let skipped = 0;
                const replyRegex = /展开\s*(\d+)\s*条回复/;

                for (const selector of selectors) {{
                    const nodes = root.querySelectorAll(selector);
                    for (const node of nodes) {{
                        if (clicked >= {max_clicks}) {{
                            return {{ clicked, skipped }};
                        }}
                        if (!visible(node)) {{
                            continue;
                        }}
                        const text = normalize(node.textContent || node.innerText);
                        if (!text) {{
                            continue;
                        }}
                        const looksLikeReplyExpand = (
                            text.includes('回复') &&
                            (text.includes('展开') || text.includes('更多') || text.includes('查看'))
                        );
                        if (!looksLikeReplyExpand) {{
                            continue;
                        }}
                        const rect = node.getBoundingClientRect();
                        const key = `${{Math.round(rect.x)}}:${{Math.round(rect.y)}}:${{text}}`;
                        if (seen.has(key)) {{
                            continue;
                        }}
                        seen.add(key);

                        const match = text.match(replyRegex);
                        if ({threshold} > 0 && match && Number.parseInt(match[1], 10) > {threshold}) {{
                            skipped += 1;
                            continue;
                        }}

                        try {{
                            node.scrollIntoView({{ behavior: 'instant', block: 'center' }});
                        }} catch (error) {{}}
                        node.click();
                        clicked += 1;
                    }}
                }}
                return {{ clicked, skipped }};
            }})()
        """)
        if not isinstance(result, dict):
            return {"clicked": 0, "skipped": 0}
        return {
            "clicked": int(result.get("clicked", 0) or 0),
            "skipped": int(result.get("skipped", 0) or 0),
        }

    def _load_feed_detail_comments(
        self,
        limit: int = 20,
        click_more_replies: bool = False,
        reply_limit: int = 10,
        scroll_speed: str = "normal",
    ) -> dict[str, Any]:
        """Scroll and optionally expand replies to load more comments into page state."""
        target_limit = max(1, int(limit))
        speed = (scroll_speed or "normal").strip().lower()
        if speed not in {"slow", "normal", "fast"}:
            speed = "normal"

        self._evaluate("""
            (() => {
                const root = document.querySelector('.comments-container');
                if (root instanceof HTMLElement) {
                    try {
                        root.scrollIntoView({ behavior: 'instant', block: 'start' });
                    } catch (error) {}
                }
                return true;
            })()
        """)
        self._sleep(0.8, minimum_seconds=0.25)

        initial_state = self._extract_feed_comments_state()
        if initial_state.get("no_comments"):
            return {
                "attempts": 0,
                "target_limit": target_limit,
                "loaded_parent_comments": 0,
                "total_comments": int(initial_state.get("total_comments", 0) or 0),
                "clicked_more_replies": 0,
                "skipped_more_replies": 0,
                "end_detected": bool(initial_state.get("end_detected")),
                "no_comments": True,
                "scroll_speed": speed,
            }

        last_count = int(initial_state.get("parent_comment_count", 0) or 0)
        stagnant_checks = 0
        clicked_total = 0
        skipped_total = 0
        attempts = 0
        max_attempts = max(10, target_limit * 3)

        while attempts < max_attempts:
            attempts += 1
            state = self._extract_feed_comments_state()
            current_count = int(state.get("parent_comment_count", 0) or 0)
            total_comments = int(state.get("total_comments", 0) or 0)
            end_detected = bool(state.get("end_detected"))
            if end_detected or current_count >= target_limit:
                break

            if click_more_replies and attempts % 2 == 1:
                click_result = self._click_more_reply_buttons(reply_limit=reply_limit)
                clicked_total += click_result["clicked"]
                skipped_total += click_result["skipped"]
                if click_result["clicked"] > 0:
                    self._sleep(0.9, minimum_seconds=0.25)
                    click_result_round2 = self._click_more_reply_buttons(reply_limit=reply_limit)
                    clicked_total += click_result_round2["clicked"]
                    skipped_total += click_result_round2["skipped"]
                    if click_result_round2["clicked"] > 0:
                        self._sleep(0.7, minimum_seconds=0.2)

            large_mode = stagnant_checks >= 3
            push_count = 3 if large_mode else 1
            self._scroll_feed_comments_area(speed=speed, large_mode=large_mode, push_count=push_count)
            state_after = self._extract_feed_comments_state()
            updated_count = int(state_after.get("parent_comment_count", 0) or 0)
            if updated_count > last_count:
                last_count = updated_count
                stagnant_checks = 0
            else:
                stagnant_checks += 1
                if stagnant_checks >= 6:
                    self._scroll_feed_comments_area(speed=speed, large_mode=True, push_count=6)
                    self._sleep(1.0, minimum_seconds=0.25)
                    stagnant_checks = 0

            if bool(state_after.get("end_detected")) or updated_count >= target_limit:
                state = state_after
                current_count = updated_count
                total_comments = int(state_after.get("total_comments", 0) or 0)
                end_detected = bool(state_after.get("end_detected"))
                break

        final_state = self._extract_feed_comments_state()
        return {
            "attempts": attempts,
            "target_limit": target_limit,
            "loaded_parent_comments": int(final_state.get("parent_comment_count", 0) or 0),
            "total_comments": int(final_state.get("total_comments", 0) or 0),
            "clicked_more_replies": clicked_total,
            "skipped_more_replies": skipped_total,
            "end_detected": bool(final_state.get("end_detected")),
            "no_comments": bool(final_state.get("no_comments")),
            "scroll_speed": speed,
        }

    def get_feed_detail(
        self,
        feed_id: str,
        xsec_token: str,
        load_all_comments: bool = False,
        limit: int = 20,
        click_more_replies: bool = False,
        reply_limit: int = 10,
        scroll_speed: str = "normal",
    ) -> dict[str, Any]:
        """
        Get feed detail from note page initial state.

        Returns a payload containing the detail object and optional comment loading summary.
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")

        self._open_feed_detail(feed_id, xsec_token)

        comment_loading = None
        if load_all_comments:
            comment_loading = self._load_feed_detail_comments(
                limit=limit,
                click_more_replies=click_more_replies,
                reply_limit=reply_limit,
                scroll_speed=scroll_speed,
            )

        explorer = FeedExplorer(self._evaluate, self._sleep)
        try:
            detail = explorer.get_feed_detail(feed_id=feed_id)
        except FeedExplorerError as e:
            raise CDPError(str(e)) from e

        print(f"[cdp_publish] Feed detail loaded. feed_id={feed_id}")
        return {
            "detail": detail,
            "comment_loading": comment_loading,
        }

    def _resolve_profile_url(
        self,
        profile_url: str | None = None,
        user_id: str | None = None,
    ) -> str:
        """Resolve user profile URL from explicit URL or user_id."""
        if isinstance(profile_url, str) and profile_url.strip():
            return profile_url.strip()
        if isinstance(user_id, str) and user_id.strip():
            return f"https://www.xiaohongshu.com/user/profile/{user_id.strip()}"
        raise CDPError("Either --profile-url or --user-id is required.")

    def get_profile_snapshot(
        self,
        profile_url: str | None = None,
        user_id: str | None = None,
    ) -> dict[str, Any]:
        """Get a user profile snapshot from profile page state + DOM."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        target_url = self._resolve_profile_url(profile_url=profile_url, user_id=user_id)
        self._navigate(target_url)
        self._sleep(2.0, minimum_seconds=0.8)

        snapshot = self._evaluate("""
            (() => {
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                const state = window.__INITIAL_STATE__ || {};

                const getByKeys = (obj, keys) => {
                    if (!obj || typeof obj !== "object") {
                        return null;
                    }
                    for (const key of keys) {
                        const value = obj[key];
                        if (value !== undefined && value !== null && String(value).trim()) {
                            return value;
                        }
                    }
                    return null;
                };

                const queue = [state];
                const seen = new Set();
                let userNode = null;
                let scanCount = 0;

                while (queue.length && scanCount < 2400) {
                    scanCount += 1;
                    const node = queue.shift();
                    if (!node || typeof node !== "object") {
                        continue;
                    }
                    if (seen.has(node)) {
                        continue;
                    }
                    seen.add(node);

                    if (!Array.isArray(node)) {
                        const idVal = getByKeys(node, [
                            "userId", "user_id", "userid", "uid", "redId", "red_id",
                        ]);
                        const nameVal = getByKeys(node, [
                            "nickname", "nickName", "name", "userName", "username",
                        ]);
                        const avatarVal = getByKeys(node, [
                            "avatar", "avatarUrl", "headUrl", "image", "images",
                        ]);
                        if (nameVal && (idVal || avatarVal)) {
                            userNode = node;
                            break;
                        }
                    }

                    if (Array.isArray(node)) {
                        for (const item of node) {
                            if (item && typeof item === "object") {
                                queue.push(item);
                            }
                        }
                        continue;
                    }

                    for (const key of Object.keys(node).slice(0, 120)) {
                        const value = node[key];
                        if (value && typeof value === "object") {
                            queue.push(value);
                        }
                    }
                }

                const nameNode = document.querySelector(
                    "h1, [class*='name'], [class*='nickname'], [class*='user-name']"
                );
                const bioNode = document.querySelector(
                    "[class*='desc'], [class*='bio'], [class*='signature'], [class*='intro']"
                );
                const avatarNode = document.querySelector(
                    "img[src*='avatar'], [class*='avatar'] img, img[alt*='头像']"
                );
                const statNodes = document.querySelectorAll(
                    "[class*='fans'], [class*='follow'], [class*='like'], [class*='count']"
                );
                const statTexts = [];
                for (const node of statNodes) {
                    if (!(node instanceof HTMLElement) || node.offsetParent === null) {
                        continue;
                    }
                    const text = normalize(node.innerText || node.textContent);
                    if (text && text.length <= 40) {
                        statTexts.push(text);
                    }
                }

                return {
                    url: window.location.href,
                    page_title: document.title || "",
                    profile: {
                        user_id: getByKeys(userNode, [
                            "userId", "user_id", "userid", "uid", "redId", "red_id",
                        ]),
                        nickname: getByKeys(userNode, [
                            "nickname", "nickName", "name", "userName", "username",
                        ]) || normalize(nameNode ? nameNode.textContent : ""),
                        avatar: getByKeys(userNode, [
                            "avatar", "avatarUrl", "headUrl", "image", "images",
                        ]) || (avatarNode instanceof HTMLImageElement ? avatarNode.src : ""),
                        desc: getByKeys(userNode, [
                            "desc", "description", "bio", "signature", "introduction",
                        ]) || normalize(bioNode ? bioNode.textContent : ""),
                        followers: getByKeys(userNode, [
                            "fans", "fansCount", "followerCount", "followers", "fans_count",
                        ]),
                        following: getByKeys(userNode, [
                            "follows", "followCount", "followingCount", "following",
                        ]),
                        liked: getByKeys(userNode, [
                            "likes", "likedCount", "totalLikes", "likeCount", "like_count",
                        ]),
                    },
                    dom_stat_texts: Array.from(new Set(statTexts)).slice(0, 12),
                };
            })()
        """)
        if not isinstance(snapshot, dict):
            raise CDPError("Could not extract profile snapshot from current page.")
        return snapshot

    def _extract_note_cards_from_profile_dom(self, limit: int) -> dict[str, Any]:
        """Extract note cards from current profile page DOM."""
        safe_limit = max(1, int(limit))
        script = """
            (() => {
                const limit = __LIMIT__;
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                const toAbs = (href) => {
                    try {
                        return new URL(href, window.location.href).href;
                    } catch (error) {
                        return "";
                    }
                };
                const parseLink = (href) => {
                    const abs = toAbs(href);
                    if (!abs) {
                        return null;
                    }
                    let parsed;
                    try {
                        parsed = new URL(abs);
                    } catch (error) {
                        return null;
                    }
                    const match = parsed.pathname.match(
                        /\\/(?:explore|discovery\\/item)\\/([0-9a-zA-Z]{24})/
                    );
                    if (!match) {
                        return null;
                    }
                    return {
                        id: match[1],
                        xsec_token: parsed.searchParams.get("xsec_token") || "",
                        url: parsed.toString(),
                    };
                };

                const selectorList = [
                    "a[href*='/explore/']",
                    "a[href*='/discovery/item/']",
                ];
                const links = document.querySelectorAll(selectorList.join(","));
                const seen = new Set();
                const notes = [];

                for (const link of links) {
                    if (!(link instanceof HTMLAnchorElement)) {
                        continue;
                    }
                    const parsed = parseLink(link.getAttribute("href") || link.href || "");
                    if (!parsed) {
                        continue;
                    }
                    if (seen.has(parsed.id)) {
                        continue;
                    }
                    seen.add(parsed.id);

                    const card = link.closest(
                        "[class*='note-item'], [class*='card'], [class*='cover'], li, article, div"
                    ) || link;
                    const titleNode = card.querySelector(
                        "[class*='title'], [class*='name'], h3, h2, img[alt]"
                    );
                    const coverNode = card.querySelector("img");
                    const titleText = normalize(
                        (titleNode && (titleNode.getAttribute("alt") || titleNode.textContent)) ||
                        link.getAttribute("title") ||
                        link.textContent
                    );
                    const cover = coverNode instanceof HTMLImageElement ? coverNode.src : "";

                    notes.push({
                        id: parsed.id,
                        xsec_token: parsed.xsec_token,
                        note_url: parsed.url,
                        title: titleText,
                        cover,
                    });
                    if (notes.length >= limit) {
                        break;
                    }
                }

                return {
                    ok: true,
                    notes,
                    count: notes.length,
                    page_url: window.location.href,
                };
            })()
        """
        result = self._evaluate(script.replace("__LIMIT__", str(safe_limit)))

        if not isinstance(result, dict):
            return {"ok": False, "reason": "invalid_dom_payload", "notes": []}
        return result

    def list_profile_notes(
        self,
        profile_url: str | None = None,
        user_id: str | None = None,
        limit: int = 20,
        max_scrolls: int = 3,
    ) -> dict[str, Any]:
        """List notes from a user profile page."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        safe_limit = max(1, min(100, int(limit)))
        safe_scrolls = max(0, min(12, int(max_scrolls)))
        target_url = self._resolve_profile_url(profile_url=profile_url, user_id=user_id)

        self._navigate(target_url)
        self._sleep(2.0, minimum_seconds=0.8)

        best_notes: list[dict[str, Any]] = []
        page_url = target_url

        for _ in range(safe_scrolls + 1):
            extracted = self._extract_note_cards_from_profile_dom(limit=safe_limit)
            notes = extracted.get("notes", []) if isinstance(extracted, dict) else []
            if isinstance(extracted, dict) and extracted.get("page_url"):
                page_url = str(extracted["page_url"])
            if isinstance(notes, list) and len(notes) > len(best_notes):
                best_notes = notes
            if len(best_notes) >= safe_limit:
                break
            self._evaluate("window.scrollTo(0, document.body.scrollHeight); true;")
            self._sleep(1.2, minimum_seconds=0.4)

        return {
            "profile_url": page_url,
            "count": len(best_notes),
            "limit": safe_limit,
            "notes": best_notes[:safe_limit],
        }

    def _activate_reply_target_for_comment(
        self,
        comment_id: str | None = None,
        comment_author: str | None = None,
        comment_snippet: str | None = None,
    ) -> dict[str, Any]:
        """Find a comment target and click its reply control."""
        id_literal = json.dumps((comment_id or "").strip(), ensure_ascii=False)
        author_literal = json.dumps((comment_author or "").strip(), ensure_ascii=False)
        snippet_literal = json.dumps((comment_snippet or "").strip(), ensure_ascii=False)

        script = """
            (() => {
                const targetId = __TARGET_ID__;
                const targetAuthor = __TARGET_AUTHOR__;
                const targetSnippet = __TARGET_SNIPPET__;
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width > 6 &&
                    node.getBoundingClientRect().height > 6
                );
                const extractCommentId = (node) => {
                    if (!(node instanceof HTMLElement)) {
                        return "";
                    }
                    const attrs = [
                        "data-comment-id",
                        "data-id",
                        "comment-id",
                        "id",
                    ];
                    for (const key of attrs) {
                        const value = node.getAttribute(key);
                        if (value && normalize(value)) {
                            return normalize(value);
                        }
                    }
                    if (node.dataset) {
                        const values = [
                            node.dataset.commentId,
                            node.dataset.id,
                            node.dataset.commentid,
                        ];
                        for (const value of values) {
                            if (value && normalize(value)) {
                                return normalize(value);
                            }
                        }
                    }
                    return "";
                };
                const findReplyControl = (container) => {
                    const selectors = [
                        "button",
                        "[role='button']",
                        "a",
                        "span",
                        "div",
                    ];
                    for (const selector of selectors) {
                        const nodes = container.querySelectorAll(selector);
                        for (const node of nodes) {
                            if (!visible(node)) {
                                continue;
                            }
                            const text = normalize(node.textContent || node.innerText);
                            if (!text) {
                                continue;
                            }
                            if (
                                text === "回复" ||
                                text.startsWith("回复") ||
                                text === "Reply" ||
                                text.startsWith("Reply")
                            ) {
                                return node;
                            }
                        }
                    }
                    return null;
                };

                const containers = [];
                const containerSelectors = [
                    "[class*='comment-item']",
                    "li[class*='comment']",
                    "div[class*='comment']",
                    "article[class*='comment']",
                ];
                for (const selector of containerSelectors) {
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {
                        if (!(node instanceof HTMLElement) || !visible(node)) {
                            continue;
                        }
                        containers.push(node);
                    }
                }
                if (!containers.length) {
                    return { ok: false, reason: "comment_not_found" };
                }

                let best = null;
                for (let idx = 0; idx < containers.length; idx++) {
                    const container = containers[idx];
                    const id = extractCommentId(container);
                    const authorNode = container.querySelector(
                        "[class*='author'], [class*='name'], [class*='user']"
                    );
                    const author = normalize(authorNode ? authorNode.textContent : "");
                    const text = normalize(container.innerText || container.textContent);
                    let score = 0;
                    if (!targetId && !targetAuthor && !targetSnippet) {
                        score = 1;
                    }
                    if (targetId && id && id === targetId) {
                        score += 100;
                    }
                    if (targetAuthor && author && author.includes(targetAuthor)) {
                        score += 30;
                    }
                    if (targetSnippet && text && text.includes(targetSnippet)) {
                        score += 20;
                    }
                    if (!best || score > best.score) {
                        best = {
                            score,
                            index: idx,
                            id,
                            author,
                            text_preview: text.slice(0, 160),
                            container,
                        };
                    }
                }

                if (!best || best.score <= 0) {
                    return { ok: false, reason: "target_comment_not_matched" };
                }

                const replyControl = findReplyControl(best.container);
                if (!replyControl) {
                    return {
                        ok: false,
                        reason: "reply_button_not_found",
                        matched_comment_id: best.id,
                        matched_author: best.author,
                    };
                }
                replyControl.click();
                return {
                    ok: true,
                    matched_comment_id: best.id,
                    matched_author: best.author,
                    matched_text_preview: best.text_preview,
                };
            })()
        """
        result = self._evaluate(
            script
            .replace("__TARGET_ID__", id_literal)
            .replace("__TARGET_AUTHOR__", author_literal)
            .replace("__TARGET_SNIPPET__", snippet_literal)
        )
        if not isinstance(result, dict):
            return {"ok": False, "reason": "unexpected_reply_target_result"}
        return result

    def respond_comment(
        self,
        feed_id: str,
        xsec_token: str,
        content: str,
        comment_id: str | None = None,
        comment_author: str | None = None,
        comment_snippet: str | None = None,
    ) -> dict[str, Any]:
        """Reply to an existing comment on a feed detail page."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        content = content.strip()
        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")
        if not content:
            raise CDPError("content cannot be empty.")

        self._open_feed_detail(feed_id, xsec_token)

        target_result = self._activate_reply_target_for_comment(
            comment_id=comment_id,
            comment_author=comment_author,
            comment_snippet=comment_snippet,
        )
        if not target_result.get("ok"):
            raise CDPError(
                "Failed to locate reply target comment: "
                f"{target_result.get('reason', 'unknown')}"
            )

        self._sleep(0.6, minimum_seconds=0.2)
        filled_len = self._fill_comment_content(content)
        self._sleep(0.6, minimum_seconds=0.2)

        submit_rect_js = """
            (function() {
                const selectors = [
                    "div.bottom button.submit",
                    "div.bottom button[class*='submit']",
                    "button.submit",
                    "button[class*='submit']",
                    "button[type='submit']",
                ];
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (!(el instanceof HTMLButtonElement) || el.offsetParent === null) {
                        continue;
                    }
                    if (el.disabled) {
                        continue;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                const fallbackTexts = new Set(["发送", "提交", "评论", "回复"]);
                const buttons = document.querySelectorAll("button");
                for (const button of buttons) {
                    if (!(button instanceof HTMLButtonElement) || button.offsetParent === null) {
                        continue;
                    }
                    if (button.disabled) {
                        continue;
                    }
                    const text = (button.textContent || "").replace(/\\s+/g, " ").trim();
                    if (!fallbackTexts.has(text)) {
                        continue;
                    }
                    const r = button.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                return null;
            })();
        """
        self._click_element_by_cdp("comment reply submit button", submit_rect_js)
        self._sleep(1.0, minimum_seconds=0.4)

        return {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "content_length": filled_len,
            "matched_comment_id": target_result.get("matched_comment_id", ""),
            "matched_author": target_result.get("matched_author", ""),
            "matched_text_preview": target_result.get("matched_text_preview", ""),
            "success": True,
        }

    def _get_element_center_cdp(self, selector: str) -> tuple[int, int] | None:
        """Return the viewport centre of a DOM element using CDP DOM.getBoxModel.

        Unlike getBoundingClientRect() this works correctly for background tabs
        because Chrome computes the box model regardless of tab visibility.

        Returns (x, y) integer viewport coordinates, or None if the element is
        not found or has zero dimensions.
        """
        try:
            doc = self._send("DOM.getDocument", {"depth": 0})
            root_id = doc["root"]["nodeId"]
            node = self._send("DOM.querySelector", {
                "nodeId": root_id,
                "selector": selector,
            })
            node_id = node.get("nodeId")
            if not node_id:
                return None
            box = self._send("DOM.getBoxModel", {"nodeId": node_id})
            content = box.get("model", {}).get("content", [])
            if len(content) == 8:
                cx = round((content[0] + content[2] + content[4] + content[6]) / 4)
                cy = round((content[1] + content[3] + content[5] + content[7]) / 4)
                if cx > 0 or cy > 0:
                    return (cx, cy)
        except Exception:
            pass
        return None

    def _mouse_click(self, x: int, y: int):
        """Dispatch a realistic mouse click sequence at the given viewport coords."""
        self._send("Input.dispatchMouseEvent",
                   {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
        self._sleep(0.08, minimum_seconds=0.05)
        self._send("Input.dispatchMouseEvent",
                   {"type": "mousePressed", "x": x, "y": y,
                    "button": "left", "clickCount": 1})
        self._sleep(0.1, minimum_seconds=0.06)
        self._send("Input.dispatchMouseEvent",
                   {"type": "mouseReleased", "x": x, "y": y,
                    "button": "left", "clickCount": 1})

    def _set_note_toggle_state(
        self,
        selectors: list[str],
        desired_active: bool,
        active_class_keywords: list[str],
        active_text_keywords: list[str],
    ) -> dict[str, Any]:
        """Toggle a note action button (like / bookmark) to the desired state.

        Uses CDP DOM.getBoxModel + Input.dispatchMouseEvent for the click so
        that the operation works correctly on background tabs (getBoundingClientRect
        returns zeros for background tabs, making JS-based isVisible checks fail).
        """
        # Build selector scoped to engage-bar to avoid matching comment-area buttons
        # The caller already passes scoped selectors like
        # ".engage-bar-container .like-wrapper".

        # --- Step 1: read current state via JS (no BCR needed) ---
        cls_kw_js  = json.dumps(active_class_keywords, ensure_ascii=False)
        text_kw_js = json.dumps(active_text_keywords,  ensure_ascii=False)
        selectors_js = json.dumps(selectors, ensure_ascii=False)

        state_script = """
(function() {
    const selectors = __SELECTORS__;
    const activeClassKeywords = __CLS_KW__;
    const activeTextKeywords  = __TEXT_KW__;
    const normalize = (t) => (t || "").replace(/\\s+/g, " ").trim().toLowerCase();
    const isActive = (node) => {
        const cls = normalize(node.className || "");
        if (activeClassKeywords.some((kw) => cls.includes(kw.toLowerCase()))) return true;
        if (normalize(node.getAttribute("aria-pressed")) === "true") return true;
        const ds = normalize(node.getAttribute("data-state") || "");
        if (["active","on","selected","checked","true"].includes(ds)) return true;
        const txt = normalize(node.innerText || node.textContent || "");
        if (activeTextKeywords.some((kw) => txt.includes(normalize(kw)))) return true;
        // SVG use[href] — XHS bookmark uses #collected (active) vs #collect (inactive)
        // XHS like uses like-active class, but check SVG href as fallback
        const use = node.querySelector && node.querySelector("svg use");
        if (use) {
            const href = (use.getAttribute("href") || use.getAttribute("xlink:href") || "").toLowerCase();
            if (href.includes("collected") || href.includes("liked") || href.includes("-active")) return true;
        }
        return false;
    };
    // isPresent: element exists in DOM and is not hidden via display:none
    // (offsetParent check is skipped — it fails on fixed/absolute elements
    //  that are positioned outside the viewport in background tabs)
    const isPresent = (node) => {
        if (!(node instanceof HTMLElement)) return false;
        const st = window.getComputedStyle(node);
        return st.display !== "none" && st.visibility !== "hidden";
    };

    for (const sel of selectors) {
        for (const node of document.querySelectorAll(sel)) {
            if (!isPresent(node)) continue;
            return { found: true, active: isActive(node) };
        }
    }
    return { found: false };
})()
""".replace("__SELECTORS__", selectors_js) \
   .replace("__CLS_KW__",   cls_kw_js) \
   .replace("__TEXT_KW__",  text_kw_js)

        # Retry state read — modal DOM may not be fully painted when
        # _wait_engage_bar returns (especially for multi-image/video notes)
        state = None
        for _retry in range(3):
            state = self._evaluate(state_script)
            if isinstance(state, dict) and state.get("found"):
                break
            if _retry < 2:
                self._sleep(0.5, minimum_seconds=0.3)
        if not isinstance(state, dict) or not state.get("found"):
            raise CDPError("Failed to set note action state: action_button_not_found")

        state_before = bool(state.get("active"))
        if state_before == desired_active:
            return {
                "ok": True,
                "changed": False,
                "state_before": state_before,
                "state_after":  state_before,
            }

        # --- Step 2: find click coordinates via DOM.getBoxModel ---
        coords = None
        for sel in selectors:
            coords = self._get_element_center_cdp(sel)
            if coords:
                break
        if not coords:
            raise CDPError("Failed to set note action state: action_button_not_found")

        # --- Step 3: real mouse click (retry once if unregistered) ---
        for _click_attempt in range(2):
            self._mouse_click(coords[0], coords[1])
            self._sleep(2.5, minimum_seconds=2.0)
            state_after_raw = self._evaluate(state_script)
            state_after = bool(state_after_raw.get("active")) if isinstance(state_after_raw, dict) else state_before
            if state_after != state_before:
                break

        return {
            "ok": True,
            "changed": state_after != state_before,
            "state_before": state_before,
            "state_after":  state_after,
        }

    def _wait_engage_bar(self, max_polls: int = 10) -> bool:
        """Poll until the note modal is open.

        Considers the modal open if EITHER:
          • .engage-bar-container is visible  (normal note)
          • the URL contains /explore/{feed-id}  (any overlay type incl. video)

        Returns True on success.
        """
        for _ in range(max_polls):
            try:
                ready = self._evaluate(
                    "(function(){"
                    "var eb=document.querySelector('.engage-bar-container');"
                    "if (eb && eb.offsetParent !== null) return true;"
                    "return /\\/explore\\/[a-f0-9]{16,}(?:\\?|$)/.test(location.href);"
                    "})()"
                )
            except Exception:
                try:
                    self._reconnect()
                except Exception:
                    pass
                self._sleep(0.8, minimum_seconds=0.6)
                continue
            if ready:
                return True
            self._sleep(0.8, minimum_seconds=0.6)
        return False

    def _bootstrap_explore_modal(self):
        """Ensure the SPA overlay context is active by clicking the first visible
        note card on the /explore feed list.

        XHS's /explore page uses a Vue Router SPA.  Page.navigate to
        /explore/{id} is intercepted by the navigation guard when no overlay is
        open, causing the URL to revert to /explore.  Opening *any* note via a
        real mouse-click establishes the overlay context so that subsequent
        Page.navigate calls can switch between notes normally.
        """
        # Make sure we are on the explore feed list first
        cur_url = self._evaluate("location.href") or ""
        if "xiaohongshu.com/explore" not in cur_url:
            self._navigate("https://www.xiaohongshu.com/explore")
            self._sleep(2, minimum_seconds=1.5)

        # Find the first note card and compute its centre using offset* properties
        # (getBoundingClientRect returns zeros for background tabs).
        rect = self._evaluate("""
(function() {
    var link = document.querySelector(
        ".note-item a[href*='/explore/'], a.note-item[href*='/explore/']"
    );
    if (!link) {
        // fallback: first note-item itself
        link = document.querySelector(".note-item");
    }
    if (!link) return null;
    // Walk up to find an element with non-zero offset dimensions
    var el = link;
    while (el && (el.offsetWidth === 0 || el.offsetHeight === 0)) {
        el = el.parentElement;
        if (!el || el === document.body) break;
    }
    if (!el || el.offsetWidth === 0) return null;
    // Accumulate offset position
    var top = 0, left = 0, cur = el;
    while (cur && cur !== document.body) {
        top  += cur.offsetTop  || 0;
        left += cur.offsetLeft || 0;
        cur   = cur.offsetParent;
    }
    return {
        x: Math.round(left + el.offsetWidth  / 2),
        y: Math.round(top  + el.offsetHeight / 2)
    };
})()
""")
        if not rect or not rect.get("x"):
            return  # can't find a card to click, proceed anyway

        x, y = rect["x"], rect["y"]
        self._send("Input.dispatchMouseEvent",
                   {"type": "mousePressed", "x": x, "y": y,
                    "button": "left", "clickCount": 1})
        self._sleep(0.1, minimum_seconds=0.05)
        self._send("Input.dispatchMouseEvent",
                   {"type": "mouseReleased", "x": x, "y": y,
                    "button": "left", "clickCount": 1})
        # Wait for the modal to open
        self._wait_engage_bar(max_polls=8)

    def _open_feed_detail(self, feed_id: str, xsec_token: str):
        """Navigate to feed detail and wait for the engage-bar to appear.

        XHS's note detail is an SPA overlay.  Page.navigate to /explore/{id}
        only works when an overlay is already open (the Vue Router is in the
        correct state).  When the tab is on the plain /explore feed list the
        navigation guard intercepts the request and the URL stays at /explore.

        Strategy:
          1. Attempt direct navigation.
          2. If engage-bar is absent (SPA guard fired), bootstrap the overlay
             context by physically clicking the first card on /explore, then
             navigate again.
          3. If still failing, open a fresh tab and navigate there — the fresh
             tab bypasses any SPA state completely.
        """
        detail_url = make_feed_detail_url(feed_id, xsec_token)

        # First attempt: direct navigation (works when overlay is already open)
        self._navigate(detail_url)
        self._check_feed_page_accessible()
        if self._wait_engage_bar(max_polls=6):
            return  # success

        # Second attempt: bootstrap the overlay by clicking a card, then retry
        print("[cdp_publish] engage-bar absent — bootstrapping overlay via card click")
        self._bootstrap_explore_modal()

        self._navigate(detail_url)
        self._check_feed_page_accessible()
        if self._wait_engage_bar(max_polls=8):
            return  # success

        # Third attempt: open a brand-new tab — bypasses any stuck SPA state
        print("[cdp_publish] engage-bar still absent — opening fresh tab")
        try:
            import requests as _req
            resp = _req.put(
                f"http://{self.host}:{self.port}/json/new?{detail_url}",
                timeout=5,
                proxies={"http": None, "https": None},
            )
            if resp.ok:
                new_ws = resp.json().get("webSocketDebuggerUrl", "")
                if new_ws:
                    try:
                        if self.ws:
                            self.ws.close()
                    except Exception:
                        pass
                    self._tab_ws_url = new_ws
                    self.ws = ws_client.connect(new_ws, max_size=None)
                    self._sleep(PAGE_LOAD_WAIT + 1, minimum_seconds=3.0)
                    self._check_feed_page_accessible()
                    self._wait_engage_bar(max_polls=10)
                    return
        except Exception as e:
            print(f"[cdp_publish] fresh-tab attempt failed: {e}")

        # Final: give up gracefully — _set_note_toggle_state will report not found


    # ------------------------------------------------------------------
    # Mouse-only wander helpers (no Page.navigate to /explore/{id})
    # ------------------------------------------------------------------

    def click_note_card_in_search(self, feed_id: str) -> bool:
        """Click the note card matching feed_id on the current search_result page.

        Uses the feeds-container transform layout to compute real viewport
        coordinates without relying on getBoundingClientRect (which returns
        zeros for background tabs).  Scrolls the card into the viewport via
        CDP Input.synthesizeScrollGesture if needed, then dispatches a real
        mouse click.

        Returns True if the engage-bar appeared (modal opened), False otherwise.
        """
        # Guard: if a modal is already open (engage-bar visible OR URL is /explore/{id}), close it first
        modal_open = self._evaluate(
            "(function(){"
            "var eb=document.querySelector('.engage-bar-container');"
            "if (eb && eb.offsetParent !== null) return true;"
            "return /\\/explore\\/[a-f0-9]{16,}(?:\\?|$)/.test(location.href);"
            "})()"
        )
        if modal_open:
            self.close_note_modal()
            self._sleep(0.5, minimum_seconds=0.4)

        # Guard: must be on search_result page (close_note_modal should have restored it)
        cur_url = self._evaluate("location.href") or ""
        if "search_result" not in cur_url:
            return False

        # Build card coordinate map from the DOM
        coords_js = r"""
(function(targetFeedId) {
    var container = document.querySelector('.feeds-container');
    if (!container) return null;
    var cLeft = container.offsetLeft;
    var cTop  = container.offsetTop;
    var sections = Array.from(document.querySelectorAll('section.note-item'));
    for (var i = 0; i < sections.length; i++) {
        var sec = sections[i];
        var a = sec.querySelector('a[href*="/explore/"]');
        if (!a) continue;
        var m = a.href.match(/\/explore\/([a-f0-9]{20,})/);
        if (!m || m[1] !== targetFeedId) continue;
        var st = sec.style.transform;
        var tm = st.match(/translate\(([\d.]+)px,\s*([\d.]+)px\)/);
        var tx = tm ? parseFloat(tm[1]) : 0;
        var ty = tm ? parseFloat(tm[2]) : 0;
        return {
            x: Math.round(cLeft + tx + sec.offsetWidth  / 2),
            y: Math.round(cTop  + ty + sec.offsetHeight / 2),
            scroll_y: window.scrollY,
            view_h: window.innerHeight,
        };
    }
    return null;
})(__FEED_ID__)
""".replace("__FEED_ID__", json.dumps(feed_id))

        rect = self._evaluate(coords_js)
        if not rect or not rect.get("x"):
            return False

        abs_x    = rect["x"]
        abs_y    = rect["y"]
        scroll_y = rect.get("scroll_y", 0)
        view_h   = rect.get("view_h", 963)
        vp_y     = abs_y - scroll_y  # y relative to viewport top

        # Scroll card into viewport if needed
        if not (50 < vp_y < view_h - 50):
            target_scroll = abs_y - view_h // 2
            delta = target_scroll - scroll_y
            self._send("Input.synthesizeScrollGesture", {
                "x": abs_x, "y": view_h // 2,
                "yDistance": -delta,
                "speed": 800,
            })
            self._sleep(0.6, minimum_seconds=0.4)
            scroll_y = self._evaluate("window.scrollY") or 0
            vp_y = abs_y - scroll_y

        self._mouse_click(abs_x, vp_y)
        return self._wait_engage_bar(max_polls=12)

    def get_dom_feed_ids_explore(self) -> list[str]:
        """Return feed_ids of note cards currently rendered on the /explore page."""
        result = self._evaluate(r"""
(function(){
    var sections = Array.from(document.querySelectorAll('section.note-item'));
    var ids = [];
    sections.forEach(function(sec){
        var a = sec.querySelector('a[href*="/explore/"]');
        if (!a) return;
        var m = a.href.match(/\/explore\/([a-f0-9]{16,})/);
        if (m) ids.push(m[1]);
    });
    return ids;
})()
""")
        return result if isinstance(result, list) else []

    def click_note_card_in_explore(self, feed_id: str) -> bool:
        """Click the note card matching feed_id on the /explore page.

        explore cards use normal document flow (no transform layout), so
        DOM.getBoxModel gives accurate coordinates directly.  Scrolls into
        viewport if needed, then dispatches a real mouse click.

        Returns True if the modal opened, False otherwise.
        """
        # Guard: close any already-open modal first
        import re as _re
        cur_url = self._evaluate("location.href") or ""
        if _re.search(r"/explore/[a-f0-9]{16,}(\?|$)", cur_url):
            self.close_note_modal()
            self._sleep(0.5, minimum_seconds=0.4)

        # Guard: must be on explore page
        cur_url = self._evaluate("location.href") or ""
        if "explore" not in cur_url or "search_result" in cur_url:
            return False

        # Find the index of the target section among all section.note-item
        idx_js = json.dumps(feed_id)
        section_index = self._evaluate(
            f"(function(){{"
            f"var secs=Array.from(document.querySelectorAll('section.note-item'));"
            f"for(var i=0;i<secs.length;i++){{"
            f"  var a=secs[i].querySelector('a[href*=\"/explore/\"]');"
            f"  if(a && a.href.indexOf({idx_js})>-1) return i;"
            f"}}"
            f"return -1;"
            f"}})()"
        )
        if section_index is None or section_index < 0:
            return False

        # Use DOM.querySelectorAll + DOM.getBoxModel to get accurate coords
        try:
            doc = self._send("DOM.getDocument", {"depth": 0})
            root_id = doc["root"]["nodeId"]
            nodes = self._send("DOM.querySelectorAll", {
                "nodeId": root_id,
                "selector": "section.note-item",
            })
            node_ids = nodes.get("nodeIds", [])
            if section_index >= len(node_ids):
                return False
            node_id = node_ids[section_index]
            box = self._send("DOM.getBoxModel", {"nodeId": node_id})
            content = box.get("model", {}).get("content", [])
            if len(content) < 8:
                return False
            abs_x = round((content[0] + content[2] + content[4] + content[6]) / 4)
            abs_y = round((content[1] + content[3] + content[5] + content[7]) / 4)
        except Exception:
            return False

        if abs_x == 0 and abs_y == 0:
            return False

        scroll_y = self._evaluate("window.scrollY") or 0
        view_h   = self._evaluate("window.innerHeight") or 963
        vp_y     = abs_y - scroll_y

        # Scroll into viewport if needed
        if not (50 < vp_y < view_h - 50):
            target_scroll = abs_y - view_h // 2
            delta = target_scroll - scroll_y
            self._send("Input.synthesizeScrollGesture", {
                "x": abs_x, "y": view_h // 2,
                "yDistance": -delta,
                "speed": 800,
            })
            self._sleep(0.6, minimum_seconds=0.4)
            scroll_y = self._evaluate("window.scrollY") or 0
            vp_y = abs_y - scroll_y

        self._mouse_click(abs_x, vp_y)
        return self._wait_engage_bar(max_polls=12)

    def close_note_modal(self):
        """Close the currently open note detail modal via ESC key.

        Sends ESC twice: the first ESC dismisses any open sub-panel (e.g. the
        comment input box if it is still focused), the second ESC closes the
        note overlay itself and restores the search_result URL.

        Guards against sending stray ESC when the modal is already closed.
        """
        def _esc():
            self._send("Input.dispatchKeyEvent", {
                "type": "keyDown", "key": "Escape", "code": "Escape",
                "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27,
            })
            self._sleep(0.08, minimum_seconds=0.05)
            self._send("Input.dispatchKeyEvent", {
                "type": "keyUp", "key": "Escape", "code": "Escape",
                "windowsVirtualKeyCode": 27, "nativeVirtualKeyCode": 27,
            })

        def _is_modal_open() -> bool:
            url = self._evaluate("location.href") or ""
            # URL is /explore/{hex-id}?... while modal is open
            # Use word-boundary to avoid matching "web_explore_feed" in search_result URLs
            import re
            return bool(re.search(r"/explore/[a-f0-9]{16,}(\?|$)", url))

        # Nothing to do if modal is already closed
        if not _is_modal_open():
            return

        # First ESC: dismiss comment input box / any focused sub-panel
        _esc()
        self._sleep(0.3, minimum_seconds=0.2)

        # Second ESC: close the note modal overlay (only if still open)
        if _is_modal_open():
            _esc()
            self._sleep(0.6, minimum_seconds=0.4)

        # One more retry if still open
        if _is_modal_open():
            _esc()
            self._sleep(0.6, minimum_seconds=0.4)

        # Wait for modal URL (/explore/{id}?...) to disappear (up to ~2s)
        import re as _re
        for _ in range(8):
            url = self._evaluate("location.href") or ""
            if not _re.search(r"/explore/[a-f0-9]{16,}(\?|$)", url):
                break
            self._sleep(0.25, minimum_seconds=0.2)

    def post_comment_in_modal(self, content: str) -> bool:
        """Post a top-level comment in the currently open note modal.

        Flow:
          1. Click comment input box (DOM.getBoxModel for background-tab coords)
          2. Wait for submit button to become active
          3. Fill text via Input.insertText
          4. Click submit button

        Returns True if the comment was submitted successfully.
        """
        content = content.strip()
        if not content:
            return False

        # Step 1: click input box to activate it
        input_coords = self._get_element_center_cdp(
            "div.input-box div.content-edit, div.input-box .content-input"
        )
        if not input_coords:
            return False
        self._mouse_click(input_coords[0], input_coords[1])
        self._sleep(0.5, minimum_seconds=0.3)

        # Step 2: fill text — use JS to set content then fire input events,
        # which is more reliable than key-by-key for CJK text
        content_js = json.dumps(content, ensure_ascii=False)
        filled = self._evaluate(f"""
(function(){{
    var sel = [
        "div.input-box div.content-edit p.content-input",
        "div.input-box div.content-edit [contenteditable='true']",
        "div.input-box .content-input",
        "p.content-input",
    ];
    var el = null;
    for (var i=0;i<sel.length;i++){{
        var n = document.querySelector(sel[i]);
        if (n && n.offsetParent !== null) {{ el=n; break; }}
    }}
    if (!el) return false;
    el.focus();
    if (el.tagName.toLowerCase()==='p' || el.isContentEditable){{
        el.textContent = {content_js};
    }} else {{
        el.value = {content_js};
    }}
    el.dispatchEvent(new Event('input', {{bubbles:true}}));
    el.dispatchEvent(new Event('change', {{bubbles:true}}));
    return el.textContent.trim().length > 0 || (el.value||'').trim().length > 0;
}})()
""")
        if not filled:
            return False
        self._sleep(0.4, minimum_seconds=0.2)

        # Step 3: click submit button — it may still be "gray" (disabled visually)
        # but XHS enables it once content is non-empty; use DOM.getBoxModel
        submit_coords = self._get_element_center_cdp(
            "div.bottom button.submit, button.btn.submit, button[class*='submit']"
        )
        if not submit_coords:
            # fallback: find by text
            btn_info = self._evaluate("""
(function(){
    var btns = Array.from(document.querySelectorAll('button'));
    for (var i=0;i<btns.length;i++){
        var b=btns[i];
        var txt=(b.innerText||'').trim();
        if (['发送','提交','评论'].includes(txt) && b.offsetParent!==null){
            var r=b.getBoundingClientRect();
            return {x:Math.round(r.x+r.width/2), y:Math.round(r.y+r.height/2)};
        }
    }
    return null;
})()
""")
            if isinstance(btn_info, dict) and btn_info.get("x"):
                submit_coords = (btn_info["x"], btn_info["y"])

        if not submit_coords:
            return False

        self._mouse_click(submit_coords[0], submit_coords[1])
        self._sleep(1.0, minimum_seconds=0.6)
        return True

    def toggle_like_in_modal(self, desired: bool) -> dict[str, Any]:
        """Toggle the like button inside the currently open note modal."""
        return self._set_note_toggle_state(
            selectors=[".engage-bar-container .like-wrapper"],
            desired_active=desired,
            active_class_keywords=[],  # "like-active" is always present on XHS — use SVG href instead
            active_text_keywords=["已赞", "取消赞"],
        )

    def toggle_bookmark_in_modal(self, desired: bool) -> dict[str, Any]:
        """Toggle the bookmark button inside the currently open note modal."""
        return self._set_note_toggle_state(
            selectors=[".engage-bar-container .collect-wrapper"],
            desired_active=desired,
            active_class_keywords=[],  # "collect-active" is not used by XHS — rely on SVG href
            active_text_keywords=["已收藏", "取消收藏"],
        )

    def set_note_upvote_state(
        self,
        feed_id: str,
        xsec_token: str,
        upvoted: bool,
    ) -> dict[str, Any]:
        """Set note upvote (like) state."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")

        self._open_feed_detail(feed_id, xsec_token)

        result = self._set_note_toggle_state(
            selectors=[
                # Scoped to the main engage-bar to avoid comment like buttons
                ".engage-bar-container .like-wrapper",
            ],
            desired_active=upvoted,
            active_class_keywords=[],  # "like-active" is always present — use SVG href
            active_text_keywords=["已赞", "取消赞"],
        )
        return {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "target_state": "upvoted" if upvoted else "not_upvoted",
            "changed": bool(result.get("changed")),
            "state_before": bool(result.get("state_before")),
            "state_after": bool(result.get("state_after")),
            "success": True,
        }

    def set_note_bookmark_state(
        self,
        feed_id: str,
        xsec_token: str,
        bookmarked: bool,
    ) -> dict[str, Any]:
        """Set note bookmark (favorite/collect) state."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")

        self._open_feed_detail(feed_id, xsec_token)

        result = self._set_note_toggle_state(
            selectors=[
                # Scoped to the main engage-bar to avoid any stray collect elements
                ".engage-bar-container .collect-wrapper",
            ],
            desired_active=bookmarked,
            active_class_keywords=[],  # XHS uses SVG href, not CSS class, for bookmark state
            active_text_keywords=["已收藏", "取消收藏"],
        )
        return {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "target_state": "bookmarked" if bookmarked else "not_bookmarked",
            "changed": bool(result.get("changed")),
            "state_before": bool(result.get("state_before")),
            "state_after": bool(result.get("state_after")),
            "success": True,
        }

    def _check_feed_page_accessible(self):
        """
        Check whether the currently opened feed detail page is accessible.

        Raises:
            CDPError: If page is inaccessible due to privacy/deletion/violation.
        """
        keyword_list_literal = json.dumps(
            list(XHS_FEED_INACCESSIBLE_KEYWORDS),
            ensure_ascii=False,
        )
        issue = self._evaluate(f"""
            (() => {{
                const wrappers = document.querySelectorAll(
                    ".access-wrapper, .error-wrapper, .not-found-wrapper, .blocked-wrapper"
                );
                if (!wrappers.length) {{
                    return "";
                }}

                let text = "";
                for (const el of wrappers) {{
                    const chunk = (el.innerText || el.textContent || "").trim();
                    if (chunk) {{
                        text += (text ? " " : "") + chunk;
                    }}
                }}
                const fullText = text.trim();
                if (!fullText) {{
                    return "";
                }}

                const keywords = {keyword_list_literal};
                for (const kw of keywords) {{
                    if (fullText.includes(kw)) {{
                        return kw;
                    }}
                }}
                return fullText.slice(0, 180);
            }})()
        """)
        if isinstance(issue, str) and issue.strip():
            raise CDPError(f"Feed page is not accessible: {issue.strip()}")

    def _fill_comment_content(self, content: str) -> int:
        """
        Fill comment content into feed detail page input.

        Returns:
            Filled character length.
        """
        content_literal = json.dumps(content, ensure_ascii=False)
        result = self._evaluate(f"""
            (() => {{
                const commentText = {content_literal};
                const candidates = [
                    "div.input-box div.content-edit p.content-input",
                    "div.input-box div.content-edit [contenteditable='true']",
                    "div.input-box .content-input",
                    "p.content-input",
                    "[class*='content-edit'] [contenteditable='true']",
                ];

                let inputEl = null;
                for (const selector of candidates) {{
                    const node = document.querySelector(selector);
                    if (!(node instanceof HTMLElement)) {{
                        continue;
                    }}
                    if (node.offsetParent === null) {{
                        continue;
                    }}
                    inputEl = node;
                    break;
                }}

                if (!inputEl) {{
                    return {{ ok: false, reason: "comment_input_not_found" }};
                }}

                inputEl.focus();

                if (inputEl instanceof HTMLInputElement || inputEl instanceof HTMLTextAreaElement) {{
                    inputEl.value = commentText;
                    inputEl.dispatchEvent(new Event("input", {{ bubbles: true }}));
                    inputEl.dispatchEvent(new Event("change", {{ bubbles: true }}));
                    return {{
                        ok: true,
                        length: inputEl.value.trim().length,
                    }};
                }}

                const asEditable = inputEl;
                if (!asEditable.isContentEditable && asEditable.tagName.toLowerCase() !== "p") {{
                    const nested = asEditable.querySelector("[contenteditable='true'], p.content-input");
                    if (nested instanceof HTMLElement) {{
                        nested.focus();
                        inputEl = nested;
                    }}
                }}

                if (inputEl.tagName.toLowerCase() === "p") {{
                    inputEl.textContent = commentText;
                }} else {{
                    const lines = commentText.split("\\n");
                    const escapeHtml = (text) => text
                        .replaceAll("&", "&amp;")
                        .replaceAll("<", "&lt;")
                        .replaceAll(">", "&gt;");
                    const html = lines.map((line) => {{
                        if (!line.trim()) {{
                            return "<p><br></p>";
                        }}
                        return "<p>" + escapeHtml(line) + "</p>";
                    }}).join("");
                    inputEl.innerHTML = html || "<p><br></p>";
                }}

                inputEl.dispatchEvent(new Event("input", {{ bubbles: true }}));
                inputEl.dispatchEvent(new Event("change", {{ bubbles: true }}));

                const finalText = (
                    inputEl.innerText ||
                    inputEl.textContent ||
                    ""
                ).trim();
                return {{
                    ok: true,
                    length: finalText.length,
                }};
            }})()
        """)
        if not isinstance(result, dict) or not result.get("ok"):
            reason = "unknown"
            if isinstance(result, dict):
                reason = str(result.get("reason", reason))
            raise CDPError(f"Failed to fill comment content: {reason}")

        return int(result.get("length", 0))

    def post_comment_to_feed(self, feed_id: str, xsec_token: str, content: str) -> dict[str, Any]:
        """
        Post a top-level comment to a feed detail page.
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        feed_id = feed_id.strip()
        xsec_token = xsec_token.strip()
        content = content.strip()

        if not feed_id:
            raise CDPError("feed_id cannot be empty.")
        if not xsec_token:
            raise CDPError("xsec_token cannot be empty.")
        if not content:
            raise CDPError("content cannot be empty.")

        self._open_feed_detail(feed_id, xsec_token)

        input_rect_js = """
            (function() {
                const selectors = [
                    "div.input-box div.content-edit span",
                    "div.input-box div.content-edit p.content-input",
                    "div.input-box div.content-edit",
                    "div.input-box",
                ];
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (!(el instanceof HTMLElement) || el.offsetParent === null) {
                        continue;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                return null;
            })();
        """
        try:
            self._click_element_by_cdp("comment input box", input_rect_js)
            self._sleep(0.4, minimum_seconds=0.15)
        except CDPError:
            print(
                "[cdp_publish] Warning: Could not click comment input via CDP. "
                "Falling back to direct focus."
            )

        filled_len = self._fill_comment_content(content)
        self._sleep(0.6, minimum_seconds=0.2)

        submit_rect_js = """
            (function() {
                const selectors = [
                    "div.bottom button.submit",
                    "div.bottom button[class*='submit']",
                    "button.submit",
                    "button[class*='submit']",
                    "button[type='submit']",
                ];
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (!(el instanceof HTMLButtonElement) || el.offsetParent === null) {
                        continue;
                    }
                    if (el.disabled) {
                        continue;
                    }
                    const r = el.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                const fallbackTexts = new Set(["发送", "提交", "评论"]);
                const buttons = document.querySelectorAll("button");
                for (const button of buttons) {
                    if (!(button instanceof HTMLButtonElement) || button.offsetParent === null) {
                        continue;
                    }
                    if (button.disabled) {
                        continue;
                    }
                    const text = (button.textContent || "").replace(/\\s+/g, " ").trim();
                    if (!fallbackTexts.has(text)) {
                        continue;
                    }
                    const r = button.getBoundingClientRect();
                    if (r.width < 8 || r.height < 8) {
                        continue;
                    }
                    return { x: r.x, y: r.y, width: r.width, height: r.height };
                }
                return null;
            })();
        """
        self._click_element_by_cdp("comment submit button", submit_rect_js)
        self._sleep(1.0, minimum_seconds=0.4)

        print(f"[cdp_publish] Comment posted. feed_id={feed_id}, length={filled_len}")
        return {
            "feed_id": feed_id,
            "xsec_token": xsec_token,
            "content_length": filled_len,
            "success": True,
        }

