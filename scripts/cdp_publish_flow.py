"""上传/填表/定时/发布 —— 从 cdp_publish.py 抽出的 mixin（P6 拆分第 4 步，最脆弱）。"""
import base64
import json
import os
import time
from pathlib import Path
from typing import Any

from xhs_errors import CDPError
from xhs_constants import (
    SELECTORS, XHS_CREATOR_URL, PAGE_LOAD_WAIT, TAB_CLICK_WAIT, UPLOAD_WAIT,
    VIDEO_PROCESS_TIMEOUT, VIDEO_PROCESS_POLL, ACTION_INTERVAL,
)
from xhs_util import validate_schedule_post_time, _is_local_host


class PublishFlowMixin:
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

