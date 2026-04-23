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

    def upload_image(self, image_path: str) -> bool:
        """通过 DOM.setFileInputFiles 上传图片，等待 images-box-item 出现确认成功"""
        abs_path = os.path.abspath(image_path)
        if not os.path.exists(abs_path):
            print(f"  ❌ 图片不存在: {abs_path}")
            return False

        doc = self._send("DOM.getDocument")
        root_id = doc.get("root", {}).get("nodeId", 1)
        node = self._send("DOM.querySelector", {
            "nodeId": root_id, "selector": 'input[type="file"]'
        })
        node_id = node.get("nodeId")
        if not node_id:
            print("  ❌ 未找到 file input")
            return False

        self._send("DOM.setFileInputFiles", {"nodeId": node_id, "files": [abs_path]})
        print(f"  📎 已提交: {os.path.basename(abs_path)}")

        # 等待编辑器内出现 blob URL 的预览图（最多 15s）
        for i in range(15):
            time.sleep(1)
            count = self._eval("""
                (function(){
                    var imgs = Array.from(document.querySelectorAll('img.images-box-item'))
                        .filter(function(img){ return img.src.startsWith('blob:'); });
                    return imgs.length;
                })()
            """) or 0
            if count > 0:
                print(f"  ✅ 图片上传完成（blob预览数: {count}）")
                return True
        print("  ⚠️ 等待超时，图片可能未上传成功")
        return False

    def _press_enter(self):
        self._send("Input.dispatchKeyEvent", {"type": "keyDown", "key": "Return", "code": "Enter", "windowsVirtualKeyCode": 13})
        self._send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": "Return", "code": "Enter", "windowsVirtualKeyCode": 13})
        time.sleep(0.08)

    def _insert_mention(self, prefix: str, keyword: str) -> bool:
        """输入 prefix+keyword（如 $BTC 或 #DOGE），从弹窗点选第一个匹配项"""
        for ch in f"{prefix}{keyword}":
            self._send("Input.insertText", {"text": ch})
            time.sleep(0.08)
        time.sleep(1.5)

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
            # $TOKEN 类型：编辑器自动识别，直接包成 suggestion span，无需点选
            auto_recognized = self._eval(f"""
                (function() {{
                    var spans = Array.from(document.querySelectorAll('span.suggestion'));
                    return spans.some(function(s){{
                        return (s.innerText||'').includes({json.dumps(keyword)});
                    }});
                }})()
            """)
            return bool(auto_recognized)

        items = json.loads(result)
        kw_lower = keyword.lower()
        match_idx = next(
            (i for i, t in enumerate(items) if kw_lower in t.lstrip(prefix).lower()),
            0
        )
        idx_js = json.dumps(match_idx)
        clicked = self._eval(f"""
            (function() {{
                var sug = document.querySelector('.editor-suggestion');
                if (!sug) return false;
                var el = sug.querySelectorAll('.css-chc6cu')[{idx_js}];
                if (el) {{ el.click(); return true; }}
                return false;
            }})()
        """)
        time.sleep(0.5)
        return bool(clicked)

    def _insert_line(self, line: str):
        """插入一行文字，自动处理 $TOKEN 和 #TAG 触发弹窗"""
        import re
        # 按 $TOKEN 和 #TAG 拆分
        parts = re.split(r'(\$[A-Za-z]+|#[^\s#$]+)', line)
        for part in parts:
            if not part:
                continue
            if part.startswith('$'):
                self._insert_mention('$', part[1:])
            elif part.startswith('#'):
                self._insert_mention('#', part[1:])
            else:
                self._send("Input.insertText", {"text": part})
                time.sleep(0.05)

    def fill_content(self, content: str) -> bool:
        """填入正文，保留段落空行，自动处理 $TOKEN / #TAG"""
        self._clear_editor()
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip():
                self._insert_line(line)
            if i < len(lines) - 1:
                self._press_enter()
        time.sleep(0.5)
        text = self._get_editor_text()
        counter = self._get_counter()
        print(f"[square] 内容已填入 {counter} 字，编辑器预览: {text[:50]}...")
        return len(text) > 0

    def attach_coin_card(self, symbol: str) -> bool:
        """点击工具栏「币种信息」按钮，搜索并选择指定 symbol（如 BTC/ETH）"""
        # 点击 trade-widget-icon 按钮
        clicked = self._eval("""
            (function(){
                var btn = document.querySelector('.trade-widget-icon.icon-box');
                if (!btn) return false;
                btn.click();
                return true;
            })()
        """)
        if not clicked:
            print(f"  ⚠️ 未找到币种信息按钮")
            return False
        time.sleep(1.5)

        # 聚焦搜索框并输入 symbol
        self._eval("(function(){ var i=document.querySelector('input.bn-textField-input'); if(i) i.focus(); })()")
        time.sleep(0.3)
        self._send("Input.insertText", {"text": symbol})
        time.sleep(1.5)

        # 点第一个精确匹配的 item（symbol 不含 USDT/永续）
        result = self._eval(f"""
            (function(){{
                var target = {json.dumps(symbol)};
                var spans = Array.from(document.querySelectorAll('*')).filter(function(el){{
                    return el.children.length === 0 && (el.innerText||'').trim() === target && el.offsetParent;
                }});
                if (spans.length > 0) {{
                    var p = spans[0].closest('[class*="cursor"]') || spans[0].parentElement;
                    if (p) {{ p.click(); return true; }}
                }}
                return false;
            }})()
        """)
        time.sleep(1)

        # 确认卡片出现
        has_card = self._eval("""
            (function(){
                return !!document.querySelector('.coinpair-kline-card');
            })()
        """)
        if has_card:
            print(f"  ✅ 币种卡: {symbol}")
        else:
            print(f"  ⚠️ 币种卡 {symbol} 未确认")
        return bool(has_card)

    def insert_tag(self, keyword: str) -> bool:
        """在正文末尾换行插入 #话题 tag"""
        self._press_enter()
        time.sleep(0.1)
        ok = self._insert_mention('#', keyword)
        if not ok:
            print(f"  ⚠️ #{keyword}: suggestion 未出现")
        return ok

    def _mouse_click(self, x: float, y: float):
        """用 CDP 真实鼠标事件点击坐标"""
        for event_type in ("mouseMoved", "mousePressed", "mouseReleased"):
            self._send("Input.dispatchMouseEvent", {
                "type": event_type, "x": x, "y": y,
                "button": "left", "clickCount": 1,
            })
            time.sleep(0.05)

    def click_publish(self, dry_run: bool = False) -> bool:
        # 找活跃的发文按钮（非 inactive，文字=发文）并直接派发点击事件
        btn_cls = self._eval("""
            (function() {
                var btns = Array.from(document.querySelectorAll('button')).filter(function(b) {
                    return (b.innerText||'').trim() === '发文' && !b.disabled
                        && !(b.className||'').includes('inactive');
                });
                if (!btns.length) return null;
                var btn = btns.find(function(b){ return (b.className||'').includes('css-1nd2rhj'); })
                       || btns.find(function(b){ return (b.className||'').includes('bn-button__primary'); })
                       || btns[0];
                return (btn.className||'').slice(0, 60);
            })()
        """)

        if not btn_cls:
            print("  ❌ 未找到可用的发文按钮")
            return False

        print(f"  发文按钮: {btn_cls}")

        if dry_run:
            print("  [dry-run] 跳过点击")
            return True

        # 直接对 DOM 元素派发 MouseEvent，不依赖坐标
        self._eval("""
            (function() {
                var btns = Array.from(document.querySelectorAll('button')).filter(function(b) {
                    return (b.innerText||'').trim() === '发文' && !b.disabled
                        && !(b.className||'').includes('inactive');
                });
                var btn = btns.find(function(b){ return (b.className||'').includes('css-1nd2rhj'); })
                       || btns.find(function(b){ return (b.className||'').includes('bn-button__primary'); })
                       || btns[0];
                if (btn) {
                    btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
                }
            })()
        """)
        time.sleep(2)

        # 等待编辑器清空（发布成功后编辑器会重置）
        for _ in range(8):
            time.sleep(1)
            text = self._get_editor_text()
            if not text:
                return True
        return True

    def publish_article(self, title: str, content: str, token_tags: list[str] = None, image_path: str = "", dry_run: bool = False) -> bool:
        """文章模式发布：有独立封面图、标题、富文本正文，最多 100,000 字"""
        print(f"[square] 导航到币安广场（文章模式）...")
        self._navigate(SQUARE_URL)

        if not self.check_login():
            print("❌ 未检测到登录状态")
            return False
        print("[square] 登录: ✅")

        if not self._wait_editor():
            print("❌ 编辑器未加载")
            return False

        # 点击「文章」按钮进入文章模式
        self._eval("(function(){ var btn=document.querySelector('.article-icon.cursor-pointer'); if(btn) btn.click(); })()")
        time.sleep(2)

        # 等待文章编辑器出现（两个 ProseMirror）
        for _ in range(8):
            count = self._eval("document.querySelectorAll('.ProseMirror').length") or 0
            if count >= 2:
                break
            time.sleep(1)
        else:
            print("❌ 文章编辑器未加载")
            return False
        print("[square] 文章编辑器: ✅")

        # 上传封面图（专用 input：accept="image/png, image/jpg, image/jpeg"）
        if image_path:
            print(f"[square] 上传封面图: {os.path.basename(image_path)}")
            if not dry_run:
                doc = self._send("DOM.getDocument")
                root_id = doc.get("root", {}).get("nodeId", 1)
                node = self._send("DOM.querySelector", {
                    "nodeId": root_id,
                    "selector": 'input[accept="image/png, image/jpg, image/jpeg"]'
                })
                node_id = node.get("nodeId")
                if node_id:
                    self._send("DOM.setFileInputFiles", {
                        "nodeId": node_id,
                        "files": [os.path.abspath(image_path)]
                    })
                    print("  📎 封面图已提交，等待上传...")
                    # 等待封面区域「上传封面」文字消失，或出现 img 标签
                    for i in range(20):
                        time.sleep(1)
                        done = self._eval("""
                            (function(){
                                // 封面已上传：img 出现 或 「上传封面」文字消失
                                var coverImg = document.querySelector('.article-editor-main img');
                                var uploadText = Array.from(document.querySelectorAll('*')).find(function(el){
                                    return el.offsetParent && (el.innerText||'').trim() === '上传封面';
                                });
                                return !!coverImg || !uploadText;
                            })()
                        """)
                        if done:
                            print(f"  ✅ 封面图上传完成（{i+1}s）")
                            break
                    else:
                        print("  ⚠️ 封面图上传超时，继续发布")
                else:
                    print("  ⚠️ 未找到封面图 input")
            else:
                print("  [dry-run] 跳过封面图")

        # 填标题（article-editor-main 内第一个 ProseMirror，即「添加标题」输入框）
        print(f"[square] 填写标题: {title[:40]}")
        self._eval("""
            (function(){
                var main = document.querySelector('.article-editor-main');
                if (!main) return;
                var editor = main.querySelector('.ProseMirror');
                if (editor) { editor.focus(); document.execCommand('selectAll'); document.execCommand('delete'); }
            })()
        """)
        time.sleep(0.3)
        self._eval("(function(){ var m=document.querySelector('.article-editor-main'); if(m){ var e=m.querySelector('.ProseMirror'); if(e) e.focus(); } })()")
        time.sleep(0.2)
        self._send("Input.insertText", {"text": title})
        time.sleep(0.5)

        # 填正文（article-editor 内第二个 ProseMirror，即正文大编辑框）
        print(f"[square] 填写正文 ({len(content)} 字)...")
        self._eval("""
            (function(){
                var editors = document.querySelectorAll('.article-editor .ProseMirror, .article-editor-main .ProseMirror');
                var body = editors[editors.length - 1];
                if (body) { body.focus(); document.execCommand('selectAll'); document.execCommand('delete'); }
            })()
        """)
        time.sleep(0.3)
        self._eval("""
            (function(){
                var editors = document.querySelectorAll('.article-editor .ProseMirror, .article-editor-main .ProseMirror');
                var body = editors[editors.length - 1];
                if (body) body.focus();
            })()
        """)
        time.sleep(0.2)
        lines = content.split("\n")
        for i, line in enumerate(lines):
            if line.strip():
                self._insert_line(line)
            if i < len(lines) - 1:
                self._press_enter()
        time.sleep(0.5)

        # 插入 $TOKEN
        if token_tags:
            print(f"[square] 插入 $token: {token_tags}")
            self._press_enter()
            for token in token_tags:
                ok = self._insert_mention('$', token)
                if not ok:
                    print(f"  ⚠️ ${token} 插入失败")
                self._send("Input.insertText", {"text": " "})
                time.sleep(0.3)

        if dry_run:
            print("  [dry-run] 跳过发布")
            return True

        # 点击发布按钮（文章模式的发布按钮 class 不同）
        print("[square] 点击发布...")
        self._eval("""
            (function(){
                var btn = Array.from(document.querySelectorAll('button')).find(function(b){
                    return (b.innerText||'').trim() === '发布' && b.offsetParent && !b.disabled;
                });
                if (btn) btn.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true}));
            })()
        """)
        time.sleep(3)
        print("✅ 文章发布完成")
        return True

    def publish(self, content: str, tags: list[str] = None, token_tags: list[str] = None, image_path: str = "", dry_run: bool = False) -> bool:
        print(f"[square] 导航到币安广场...")
        self._navigate(SQUARE_URL)

        if not self.check_login():
            print("❌ 未检测到登录状态，请先在浏览器中登录")
            return False
        print("[square] 登录: ✅")

        if not self._wait_editor():
            print("❌ 编辑器未加载")
            return False

        # 上传图片（先传图，再填文字）
        if image_path:
            print(f"[square] 上传图片: {os.path.basename(image_path)}")
            if not dry_run:
                self.upload_image(image_path)
            else:
                print("  [dry-run] 跳过图片上传")

        # 填正文
        print(f"[square] 填写正文 ({len(content)} 字)...")
        if not self.fill_content(content):
            print("❌ 正文填写失败")
            return False

        # 插入 #话题 tags
        if tags:
            print(f"[square] 插入 #tag: {tags}")
            for tag in tags:
                ok = self.insert_tag(tag)
                if not ok:
                    print(f"  ⚠️ tag #{tag} 插入失败，继续")
                time.sleep(0.5)

        # 插入 $TOKEN tags
        if token_tags:
            print(f"[square] 插入 $token: {token_tags}")
            self._press_enter()
            for token in token_tags:
                ok = self._insert_mention('$', token)
                if not ok:
                    print(f"  ⚠️ token ${token} 插入失败，继续")
                # 两个 token 之间加空格
                self._send("Input.insertText", {"text": " "})
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
    parser.add_argument("--image", default="", help="图片本地路径")
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
        ok = pub.publish(content=content, tags=args.tags, image_path=args.image, dry_run=args.dry_run)
        sys.exit(0 if ok else 1)
    finally:
        pub.close()


if __name__ == "__main__":
    main()
