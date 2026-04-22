#!/usr/bin/env python3
"""
币安广场（Binance Square）CDP 发帖脚本

用法:
    python scripts/bn/square_publish.py --content "今日行情分析..."
    python scripts/bn/square_publish.py --content-file /tmp/post.txt --tags "DOGE" "BTC"
    python scripts/bn/square_publish.py --content-file /tmp/post.txt --dry-run
"""

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
except ImportError:
    pass

CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))
SQUARE_URL = "https://www.binance.com/zh-CN/square"


class SquarePublisher:
    def __init__(self, host: str = CDP_HOST, port: int = CDP_PORT):
        self.host = host
        self.port = port
        self._ws = None
        self._msg_id = 0

    def connect(self):
        import urllib.request
        import websocket

        with urllib.request.urlopen(f"http://{self.host}:{self.port}/json", timeout=5) as r:
            tabs = json.loads(r.read())

        tab = next(
            (t for t in tabs if "binance.com" in t.get("url", "") and t.get("type") == "page"),
            next((t for t in tabs if t.get("type") == "page"), None)
        )
        if not tab:
            raise RuntimeError("没有可用的 Chrome tab")

        import websocket
        self._ws = websocket.create_connection(tab["webSocketDebuggerUrl"], timeout=30)
        print(f"[square] 已连接: {tab.get('url','')[:70]}")

    def _send(self, method: str, params: dict = None) -> dict:
        self._msg_id += 1
        self._ws.send(json.dumps({"id": self._msg_id, "method": method, "params": params or {}}))
        while True:
            data = json.loads(self._ws.recv())
            if data.get("id") == self._msg_id:
                return data.get("result", {})

    def _eval(self, js: str):
        result = self._send("Runtime.evaluate", {"expression": js, "returnByValue": True, "awaitPromise": True})
        exc = result.get("exceptionDetails")
        if exc:
            raise RuntimeError(f"JS error: {exc.get('exception', {}).get('description', exc)}")
        return result.get("result", {}).get("value")

    def _navigate(self, url: str):
        self._send("Page.navigate", {"url": url})
        time.sleep(3)

    def _insert_text(self, text: str):
        """用 CDP Input.insertText 插入文字（正确触发 React 状态更新）"""
        self._send("Input.insertText", {"text": text})

    def _key(self, key: str, code: str = "", modifiers: int = 0):
        for t in ("keyDown", "keyUp"):
            self._send("Input.dispatchKeyEvent", {"type": t, "key": key, "code": code or key, "modifiers": modifiers})

    def check_login(self) -> bool:
        return bool(self._eval("""
            (function() {
                var loginBtn = document.querySelector('a[href*="/login"]');
                var userArea = document.querySelector('[class*="user-info"], [class*="avatar"], [class*="profile-icon"]');
                return !loginBtn || !!userArea;
            })()
        """))

    def _wait_editor(self, timeout: int = 10) -> bool:
        for _ in range(timeout):
            if self._eval("!!document.querySelector('.ProseMirror')"):
                return True
            time.sleep(1)
        return False

    def _clear_editor(self):
        self._eval("""
            (function() {
                var editor = document.querySelector('.ProseMirror');
                if (editor) { editor.focus(); document.execCommand('selectAll'); document.execCommand('delete'); }
            })()
        """)
        time.sleep(0.3)

    def _get_editor_text(self) -> str:
        return self._eval("(function(){ var e=document.querySelector('.ProseMirror'); return e?e.innerText.trim():''; })()") or ""

    def _get_counter(self) -> str:
        return self._eval("(function(){ var c=document.querySelector('[class*=\"count\"]'); return c?c.innerText:''; })()") or ""

    def _press_enter(self):
        self._send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Return", "code": "Enter", "windowsVirtualKeyCode": 13})
        self._send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "Return", "code": "Enter", "windowsVirtualKeyCode": 13})
        time.sleep(0.08)

    def fill_content(self, content: str) -> bool:
        """填入正文，保留段落空行"""
        self._clear_editor()
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip():
                self._insert_text(line)
            # 每行后都按 Enter（空行行 = 两次 Enter = 段落间距）
            if i < len(lines) - 1:
                self._press_enter()
        time.sleep(0.5)
        text = self._get_editor_text()
        counter = self._get_counter()
        print(f"[square] 内容已填入 {counter} 字，编辑器预览: {text[:50]}...")
        return len(text) > 0

    def insert_tag(self, keyword: str) -> bool:
        """
        在光标处插入 tag：
        1. 输入 #keyword 触发 suggestion dropdown
        2. 优先点选匹配话题；若无匹配则选「+新话题」
        """
        self._key("Return", "Enter")
        time.sleep(0.1)

        # 逐字输入 #keyword（只用 insertText，避免重复）
        for ch in f"#{keyword}":
            self._insert_text(ch)
            time.sleep(0.08)
        time.sleep(1.5)

        # 找 suggestion 列表
        result = self._eval("""
            (function() {
                var sug = document.querySelector('.editor-suggestion');
                if (!sug) return null;
                var items = Array.from(sug.querySelectorAll('.css-chc6cu'))
                    .map(function(el) { return (el.innerText||'').trim(); });
                return JSON.stringify(items);
            })()
        """)

        if not result:
            print(f"  ⚠️ #{keyword}: 未出现 suggestion，跳过")
            # 清掉已输入的 # 触发文字
            for _ in range(len(keyword) + 1):
                self._key("Backspace", "Backspace")
            return False

        items = json.loads(result)
        print(f"  候选话题: {items[:4]}")

        # 找最匹配的项（精确匹配 or 包含）
        kw_lower = keyword.lower()
        match_idx = None
        for i, item in enumerate(items):
            clean = item.lstrip("#").lower()
            if clean == kw_lower or kw_lower in clean:
                match_idx = i
                break

        # 如无精确匹配，用「+新话题」
        if match_idx is None:
            for i, item in enumerate(items):
                if "新话题" in item or "+" in item:
                    match_idx = i
                    break

        if match_idx is None:
            match_idx = 0

        chosen = items[match_idx] if match_idx < len(items) else ""
        print(f"  选择: {chosen}")

        # 点击对应项
        idx_js = json.dumps(match_idx)
        clicked = self._eval(f"""
            (function() {{
                var sug = document.querySelector('.editor-suggestion');
                if (!sug) return false;
                var items = sug.querySelectorAll('.css-chc6cu');
                var el = items[{idx_js}];
                if (el) {{ el.click(); return true; }}
                return false;
            }})()
        """)
        time.sleep(0.8)
        return bool(clicked)

    def _mouse_click(self, x: float, y: float):
        """用 CDP 真实鼠标事件点击坐标"""
        for event_type in ("mouseMoved", "mousePressed", "mouseReleased"):
            self._send("Input.dispatchMouseEvent", {
                "type": event_type, "x": x, "y": y,
                "button": "left", "clickCount": 1,
            })
            time.sleep(0.05)

    def click_publish(self, dry_run: bool = False) -> bool:
        # 找活跃的发文按钮（非 inactive，文字=发文）
        btn_info = self._eval("""
            (function() {
                var btns = Array.from(document.querySelectorAll('button')).filter(function(b) {
                    return (b.innerText||'').trim() === '发文' && !b.disabled
                        && !(b.className||'').includes('inactive');
                });
                if (!btns.length) return null;
                // 优先选黄色实心按钮（css-1nd2rhj 或 bn-button__primary）
                var btn = btns.find(function(b){ return (b.className||'').includes('css-1nd2rhj'); })
                       || btns.find(function(b){ return (b.className||'').includes('bn-button__primary'); })
                       || btns[0];
                var r = btn.getBoundingClientRect();
                return JSON.stringify({cls: btn.className, x: r.left + r.width/2, y: r.top + r.height/2});
            })()
        """)

        if not btn_info:
            print("  ❌ 未找到可用的发文按钮")
            return False

        info = json.loads(btn_info)
        print(f"  发文按钮: cls={info['cls'][:60]} 坐标=({info['x']:.0f},{info['y']:.0f})")

        if dry_run:
            print("  [dry-run] 跳过点击")
            return True

        self._mouse_click(info["x"], info["y"])
        time.sleep(2)

        # 等待编辑器清空（发布成功后编辑器会重置）
        for _ in range(8):
            time.sleep(1)
            text = self._get_editor_text()
            if not text:
                return True
        return True

    def publish(self, content: str, tags: list[str] = None, dry_run: bool = False) -> bool:
        print(f"[square] 导航到币安广场...")
        self._navigate(SQUARE_URL)

        if not self.check_login():
            print("❌ 未检测到登录状态，请先在浏览器中登录")
            return False
        print("[square] 登录: ✅")

        if not self._wait_editor():
            print("❌ 编辑器未加载")
            return False

        # 填正文
        print(f"[square] 填写正文 ({len(content)} 字)...")
        if not self.fill_content(content):
            print("❌ 正文填写失败")
            return False

        # 插入 tags
        if tags:
            print(f"[square] 插入 tag: {tags}")
            for tag in tags:
                ok = self.insert_tag(tag)
                if not ok:
                    print(f"  ⚠️ tag #{tag} 插入失败，继续")
                time.sleep(0.5)

        time.sleep(0.5)
        counter = self._get_counter()
        print(f"[square] 最终字数: {counter}")

        # 发布
        print("[square] 点击发文...")
        ok = self.click_publish(dry_run=dry_run)
        if ok:
            print("✅ 发文完成")
        else:
            print("❌ 发文失败")
        return ok

    def close(self):
        if self._ws:
            self._ws.close()


def main():
    parser = argparse.ArgumentParser(description="币安广场发帖")
    parser.add_argument("--content", default="", help="发帖正文")
    parser.add_argument("--content-file", default="", help="从文件读取正文")
    parser.add_argument("--tags", nargs="*", default=[], help="话题 tag（不含 #，自动匹配热门话题）")
    parser.add_argument("--host", default=CDP_HOST)
    parser.add_argument("--port", type=int, default=CDP_PORT)
    parser.add_argument("--dry-run", action="store_true", help="不实际点击发布")
    args = parser.parse_args()

    content = args.content
    if args.content_file:
        with open(args.content_file, "r", encoding="utf-8") as f:
            content = f.read().strip()
    if not content:
        print("❌ 请通过 --content 或 --content-file 提供内容")
        sys.exit(1)

    # 如果 --tags 已指定，剥掉内容末尾的 #tag 行（避免重复）
    if args.tags:
        lines = content.splitlines()
        while lines and all(w.startswith("#") for w in lines[-1].split() if w):
            lines.pop()
        content = "\n".join(lines).strip()

    pub = SquarePublisher(host=args.host, port=args.port)
    try:
        pub.connect()
        ok = pub.publish(content=content, tags=args.tags, dry_run=args.dry_run)
        sys.exit(0 if ok else 1)
    finally:
        pub.close()


if __name__ == "__main__":
    main()
