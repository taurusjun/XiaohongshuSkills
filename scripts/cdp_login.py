"""登录/二维码/cookie 缓存 —— 从 cdp_publish.py 抽出的 mixin（P6 拆分第 2 步）。

XiaohongshuPublisher 通过多继承获得这些方法；self 上的 _send/_evaluate/_navigate/
_sleep/ws/login_cache_* 由基类提供。
"""
import json
import os
import time
from typing import Any

from xhs_errors import CDPError
from xhs_constants import (
    XHS_CREATOR_LOGIN_CHECK_URL,
    XHS_HOME_URL,
    XHS_HOME_LOGIN_MODAL_KEYWORD,
)


class LoginMixin:
    def _login_cache_key(self, scope: str) -> str:
        """Build a unique cache key for one login scope."""
        return f"{self.host}:{self.port}:{self.account_name}:{scope}"

    def _load_login_cache(self) -> dict[str, Any]:
        """Load login cache payload from local JSON file."""
        if not os.path.exists(self.login_cache_file):
            return {"entries": {}}

        try:
            with open(self.login_cache_file, "r", encoding="utf-8") as cache_file:
                payload = json.load(cache_file)
        except Exception:
            return {"entries": {}}

        if not isinstance(payload, dict):
            return {"entries": {}}
        entries = payload.get("entries")
        if not isinstance(entries, dict):
            payload["entries"] = {}
        return payload

    def _save_login_cache(self, payload: dict[str, Any]):
        """Persist login cache payload to local JSON file."""
        parent = os.path.dirname(self.login_cache_file)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(self.login_cache_file, "w", encoding="utf-8") as cache_file:
            json.dump(payload, cache_file, ensure_ascii=False, indent=2)

    def _get_cached_login_status(self, scope: str) -> bool | None:
        """Return cached login status when cache is still fresh."""
        if self.login_cache_ttl_seconds <= 0:
            return None

        payload = self._load_login_cache()
        entries = payload.get("entries", {})
        entry = entries.get(self._login_cache_key(scope))
        if not isinstance(entry, dict):
            return None

        checked_at = entry.get("checked_at")
        logged_in = entry.get("logged_in")
        if not isinstance(checked_at, (int, float)) or not isinstance(logged_in, bool):
            return None

        age_seconds = time.time() - float(checked_at)
        if age_seconds < 0 or age_seconds > self.login_cache_ttl_seconds:
            return None

        if not logged_in:
            return None

        age_minutes = int(age_seconds // 60)
        print(
            "[cdp_publish] Using cached login status "
            f"({scope}, age={age_minutes}m, ttl={self.login_cache_ttl_hours:g}h)."
        )
        return logged_in

    def _set_login_cache(self, scope: str, logged_in: bool):
        """Save positive login status cache for a specific scope."""
        if not logged_in:
            self._clear_login_cache(scope=scope)
            return

        payload = self._load_login_cache()
        entries = payload.setdefault("entries", {})
        entries[self._login_cache_key(scope)] = {
            "logged_in": True,
            "checked_at": int(time.time()),
        }
        self._save_login_cache(payload)

    def _clear_login_cache(self, scope: str | None = None):
        """Clear login cache entries for current host/port/account."""
        payload = self._load_login_cache()
        entries = payload.get("entries", {})
        if not isinstance(entries, dict) or not entries:
            return

        changed = False
        if scope:
            key = self._login_cache_key(scope)
            if key in entries:
                entries.pop(key, None)
                changed = True
        else:
            prefix = self._login_cache_key("")
            for key in list(entries.keys()):
                if key.startswith(prefix):
                    entries.pop(key, None)
                    changed = True

        if changed:
            payload["entries"] = entries
            self._save_login_cache(payload)

    def probe_login_state(self) -> bool:
        """零导航登录探测：直接查 .xiaohongshu.com 的 web_session cookie。

        用于展示二维码期间的高频轮询（~50ms），不会导航，因此不会让用户
        正在扫的二维码失效。check_login() 会导航，不能用于此场景。
        """
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")
        self._send("Network.enable")
        result = self._send(
            "Network.getCookies",
            {"urls": ["https://creator.xiaohongshu.com", "https://www.xiaohongshu.com"]},
        )
        cookies = result.get("cookies", []) if isinstance(result, dict) else []
        # 登录后不一定有 web_session；创作平台会话标识见实测集合
        session_names = {
            "access-token-creator.xiaohongshu.com",
            "x-user-id-creator.xiaohongshu.com",
            "web_session",
            "customer-sso-sid",
        }
        ok = any(c.get("name") in session_names and c.get("value") for c in cookies)
        if ok:
            self._set_login_cache("creator", True)
            self._set_login_cache("home", True)
        return ok

    def check_login(self) -> bool:
        """
        Navigate to Xiaohongshu creator center and check if the user is logged in.

        Returns True if logged in. If not logged in, prints instructions
        and returns False.
        """
        scope = "creator"
        cached_status = self._get_cached_login_status(scope)
        if cached_status is not None:
            if cached_status:
                print("[cdp_publish] Login confirmed (cached).")
            return cached_status

        self._navigate(XHS_CREATOR_LOGIN_CHECK_URL)
        self._sleep(2, minimum_seconds=1.0)

        # Check if we got redirected to a login page
        current_url = self._evaluate("window.location.href")
        print(f"[cdp_publish] Current URL: {current_url}")

        if "login" in current_url.lower():
            self._set_login_cache(scope, logged_in=False)
            print(
                "\n[cdp_publish] NOT LOGGED IN.\n"
                "  Please scan the QR code in the Chrome window to log in,\n"
                "  then run this script again.\n"
            )
            return False

        self._set_login_cache(scope, logged_in=True)
        print("[cdp_publish] Login confirmed.")
        return True

    def _home_login_prompt_visible(self, keyword: str) -> bool:
        """Return True when home page login prompt modal is visible."""
        keyword_literal = json.dumps(keyword)
        visible = self._evaluate(f"""
            (() => {{
                const keyword = {keyword_literal};
                const normalize = (text) => (text || "").replace(/\\s+/g, " ").trim();
                const containsKeyword = (text) => normalize(text).includes(keyword);

                const modalSelectors = [
                    "[class*='login']",
                    "[class*='modal']",
                    "[class*='popup']",
                    "[class*='dialog']",
                    "[class*='mask']",
                ];

                for (const selector of modalSelectors) {{
                    const nodes = document.querySelectorAll(selector);
                    for (const node of nodes) {{
                        if (!(node instanceof HTMLElement)) {{
                            continue;
                        }}
                        if (node.offsetParent === null) {{
                            continue;
                        }}
                        if (containsKeyword(node.textContent) || containsKeyword(node.innerText)) {{
                            return true;
                        }}
                    }}
                }}

                if (document.body && containsKeyword(document.body.innerText)) {{
                    return true;
                }}
                return false;
            }})()
        """)
        return bool(visible)

    def check_home_login(
        self,
        keyword: str = XHS_HOME_LOGIN_MODAL_KEYWORD,
        wait_seconds: float = 8.0,
    ) -> bool:
        """
        Check login state on Xiaohongshu home page.

        Login prompt modal keyword (default: "登录后推荐更懂你的笔记") indicates
        unauthenticated state for the xiaohongshu.com home/feed domain.
        """
        scope = "home"
        cached_status = self._get_cached_login_status(scope)
        if cached_status is not None:
            if cached_status:
                print("[cdp_publish] Home login confirmed (cached).")
            return cached_status

        self._navigate(XHS_HOME_URL)
        self._sleep(2, minimum_seconds=1.0)

        current_url = self._evaluate("window.location.href")
        print(f"[cdp_publish] Home URL: {current_url}")
        if isinstance(current_url, str) and "login" in current_url.lower():
            self._set_login_cache(scope, logged_in=False)
            print(
                "\n[cdp_publish] NOT LOGGED IN (HOME).\n"
                "  Please log in on xiaohongshu.com and run this command again.\n"
            )
            return False

        deadline = time.time() + max(1.0, wait_seconds)
        while time.time() < deadline:
            if self._home_login_prompt_visible(keyword):
                self._set_login_cache(scope, logged_in=False)
                print(
                    "\n[cdp_publish] NOT LOGGED IN (HOME).\n"
                    f"  Detected login prompt keyword: {keyword}\n"
                    "  Please log in on xiaohongshu.com and run this command again.\n"
                )
                return False
            self._sleep(0.7, minimum_seconds=0.2)

        self._set_login_cache(scope, logged_in=True)
        print("[cdp_publish] Home login confirmed.")
        return True

    def clear_cookies(self, domain: str = ".xiaohongshu.com"):
        """
        Clear all cookies for the given domain to force re-login.

        Used when switching accounts.
        """
        print(f"[cdp_publish] Clearing cookies for {domain}...")
        self._send("Network.enable")
        self._send("Network.clearBrowserCookies")
        # Also clear storage
        self._send("Storage.clearDataForOrigin", {
            "origin": "https://www.xiaohongshu.com",
            "storageTypes": "cookies,local_storage,session_storage",
        })
        self._send("Storage.clearDataForOrigin", {
            "origin": "https://creator.xiaohongshu.com",
            "storageTypes": "cookies,local_storage,session_storage",
        })
        self._clear_login_cache()
        print("[cdp_publish] Cookies and storage cleared.")

    def open_login_page(self):
        """
        Navigate to the Xiaohongshu login page for QR code scanning.

        Used for initial login or after clearing cookies for account switch.
        """
        self._navigate(XHS_CREATOR_LOGIN_CHECK_URL)
        self._sleep(2, minimum_seconds=1.0)
        current_url = self._evaluate("window.location.href")
        if "login" not in current_url.lower():
            # Already logged in, navigate to login page explicitly
            self._navigate("https://creator.xiaohongshu.com/login")
            self._sleep(2, minimum_seconds=1.0)
        self._clear_login_cache()
        print(
            "\n[cdp_publish] Login page is open.\n"
            "  Please scan the QR code in the Chrome window to log in.\n"
        )

    def _capture_clip_png_base64(self, rect: dict[str, Any], padding: int = 8) -> str:
        """Capture a clipped PNG screenshot and return base64 payload."""
        x = max(0.0, float(rect.get("x", 0.0)) - padding)
        y = max(0.0, float(rect.get("y", 0.0)) - padding)
        width = max(1.0, float(rect.get("width", 0.0)) + padding * 2)
        height = max(1.0, float(rect.get("height", 0.0)) + padding * 2)

        self._send("Page.enable")
        result = self._send(
            "Page.captureScreenshot",
            {
                "format": "png",
                "clip": {
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                    "scale": 1,
                },
                "captureBeyondViewport": True,
            },
        )
        image_base64 = result.get("data", "")
        if not isinstance(image_base64, str) or not image_base64:
            raise CDPError("Failed to capture QR code screenshot.")
        return image_base64

    def _ensure_qrcode_login_mode(self) -> bool:
        """创作平台登录页默认短信登录；点切换图标 .css-wemwzq 切到二维码视图。

        切到二维码后页面会新增一张 ~160x160、src 为 data:image/png;base64 的 img。
        """
        result = self._evaluate(r"""
            (() => {
                const bodyText = (document.body && document.body.innerText) || "";
                if (bodyText.includes("扫一扫") || bodyText.includes("二维码")) {
                    return { ok: true, already: true };
                }
                const toggle = document.querySelector(".css-wemwzq")
                    || document.querySelector(".login-container img");
                if (!toggle) {
                    return { ok: false, reason: "toggle_not_found" };
                }
                const r = toggle.getBoundingClientRect();
                const opts = {
                    bubbles: true, cancelable: true,
                    clientX: r.x + r.width / 2, clientY: r.y + r.height / 2,
                };
                toggle.dispatchEvent(new MouseEvent("click", opts));
                if (toggle.parentElement) {
                    toggle.parentElement.dispatchEvent(new MouseEvent("click", opts));
                }
                return { ok: true, clicked: true };
            })()
        """)
        return bool(result and result.get("ok"))

    def _locate_login_qrcode(self) -> dict[str, Any]:
        """返回当前登录页里二维码的元数据。

        创作平台登录页默认短信登录；二维码视图里那张 img 是页面里**面积最大**的
        img/canvas（~160x160，自带 data:image/png;base64 src），切换图标只有 64x64。
        因此：优先二维码专属选择器，否则取面积最大的可见 img/canvas。
        """
        result = self._evaluate(r"""
            (() => {
                const normalize = (text) => (text || "").replace(/\s+/g, " ").trim();
                const visible = (node) => (
                    node instanceof HTMLElement &&
                    node.offsetParent !== null &&
                    node.getBoundingClientRect().width >= 24 &&
                    node.getBoundingClientRect().height >= 24
                );
                const preferred = [
                    ".login-container .qrcode-img",
                    "img.qrcode-img",
                    "img[src*='qrcode']",
                    "[class*='qrcode'] img",
                    "[class*='qr'] img",
                    "[class*='qrcode'] canvas",
                    "[class*='qr'] canvas",
                ];
                const nodes = Array.from(document.querySelectorAll("img,canvas")).filter(visible);
                const build = (node, selector) => {
                    const rect = node.getBoundingClientRect();
                    const src = node instanceof HTMLImageElement ? (node.currentSrc || node.src || "") : "";
                    const dataUrl = node instanceof HTMLCanvasElement ? node.toDataURL("image/png") : "";
                    const parentText = normalize(
                        node.parentElement ? (node.parentElement.innerText || node.parentElement.textContent) : ""
                    );
                    return {
                        ok: true,
                        tag_name: String(node.tagName || "").toLowerCase(),
                        selector,
                        src,
                        data_url: dataUrl,
                        rect: { x: rect.x, y: rect.y, width: rect.width, height: rect.height },
                        hint_text: parentText,
                    };
                };
                for (const selector of preferred) {
                    const node = nodes.find((n) => n.matches && n.matches(selector));
                    if (node) return build(node, selector);
                }
                let pick = null, area = 0;
                for (const node of nodes) {
                    const r = node.getBoundingClientRect();
                    const a = r.width * r.height;
                    if (a > area) { area = a; pick = node; }
                }
                if (pick) return build(pick, "largest");
                return { ok: false, reason: "qrcode_not_found" };
            })()
        """)
        return result if isinstance(result, dict) else {"ok": False, "reason": "unexpected_result"}

    def get_login_qrcode(self, wait_seconds: float = 20.0) -> dict[str, Any]:
        """Open login page and return QR code image payload for remote display."""
        if not self.ws:
            raise CDPError("Not connected. Call connect() first.")

        self._navigate(XHS_CREATOR_LOGIN_CHECK_URL)
        self._sleep(1.5, minimum_seconds=0.6)
        current_url = self._evaluate("window.location.href")
        if isinstance(current_url, str) and "login" not in current_url.lower():
            self._navigate("https://creator.xiaohongshu.com/login")
            self._sleep(1.5, minimum_seconds=0.6)
            current_url = self._evaluate("window.location.href")

        if isinstance(current_url, str) and "login" not in current_url.lower():
            return {
                "logged_in": True,
                "current_url": current_url,
                "qrcode_base64": "",
                "qrcode_data_url": "",
                "mime_type": "image/png",
                "message": "Already logged in.",
            }

        # 创作平台登录页默认短信登录：先点击切换图标进入二维码视图
        self._ensure_qrcode_login_mode()
        self._sleep(2.0, minimum_seconds=1.0)

        deadline = time.time() + max(3.0, float(wait_seconds))
        qrcode_meta: dict[str, Any] | None = None
        while time.time() < deadline:
            qrcode_meta = self._locate_login_qrcode()
            if qrcode_meta.get("ok"):
                break
            self._sleep(0.6, minimum_seconds=0.2)

        if not qrcode_meta or not qrcode_meta.get("ok"):
            reason = qrcode_meta.get("reason", "qrcode_not_found") if isinstance(qrcode_meta, dict) else "qrcode_not_found"
            raise CDPError(f"Failed to locate login QR code: {reason}")

        data_url = qrcode_meta.get("data_url")
        src = qrcode_meta.get("src")
        # 二维码可能是 canvas(toDataURL) 或 img(自带 base64 src)；img 直接用，最清晰
        inline = data_url if (isinstance(data_url, str) and data_url.startswith("data:image/")) else (
            src if (isinstance(src, str) and src.startswith("data:image/")) else "")
        if inline:
            header, _, encoded = inline.partition(",")
            mime_type = header[5:].split(";", 1)[0] if header.startswith("data:") else "image/png"
            image_base64 = encoded
            qrcode_data_url = inline
        else:
            rect = qrcode_meta.get("rect")
            if not isinstance(rect, dict):
                raise CDPError("QR code rect is missing.")
            image_base64 = self._capture_clip_png_base64(rect)
            mime_type = "image/png"
            qrcode_data_url = f"data:{mime_type};base64,{image_base64}"

        return {
            "logged_in": False,
            "current_url": current_url,
            "qrcode_base64": image_base64,
            "qrcode_data_url": qrcode_data_url,
            "mime_type": mime_type,
            "selector": qrcode_meta.get("selector", ""),
            "tag_name": qrcode_meta.get("tag_name", ""),
            "hint_text": qrcode_meta.get("hint_text", ""),
        }

    # ------------------------------------------------------------------
    # Feed discovery actions
    # ------------------------------------------------------------------

