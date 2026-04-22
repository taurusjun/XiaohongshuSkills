#!/usr/bin/env python3
"""
币安广场（Binance Square）CDP 发帖脚本

用法:
    python scripts/bn/square_publish.py --content "今日行情分析..."
    python scripts/bn/square_publish.py --content "内容" --image /path/to/img.jpg
    python scripts/bn/square_publish.py --content "内容" --dry-run
"""

import argparse
import json
import os
import sys
import time

# 复用父目录的 CDP 连接工具
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(__file__), "../.env"))
except ImportError:
    pass

CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = int(os.environ.get("CDP_PORT", "9222"))

SQUARE_URL = "https://www.binance.com/zh-CN/square"
EDITOR_SELECTOR = ".ProseMirror"
PUBLISH_BTN_SELECTOR = "button.bn-button__primary"


# ============ CDP 基础操作 ============

class SquarePublisher:
    def __init__(self, host: str = CDP_HOST, port: int = CDP_PORT):
        self.host = host
        self.port = port
        self._ws = None
        self._msg_id = 0
        self._session_id = None

    def connect(self):
        import urllib.request
        import websocket

        tabs_url = f"http://{self.host}:{self.port}/json"
        with urllib.request.urlopen(tabs_url, timeout=5) as r:
            tabs = json.loads(r.read())

        # 优先复用已有 Square 标签，否则取第一个
        tab = next(
            (t for t in tabs if "binance.com" in t.get("url", "") and t.get("type") == "page"),
            next((t for t in tabs if t.get("type") == "page"), None)
        )
        if not tab:
            raise RuntimeError("没有可用的 Chrome tab，请先启动浏览器")

        ws_url = tab["webSocketDebuggerUrl"]
        self._ws = websocket.create_connection(ws_url, timeout=30)
        print(f"[square] 已连接到: {tab.get('url', '')[:60]}")

    def _send(self, method: str, params: dict = None) -> dict:
        self._msg_id += 1
        msg = {"id": self._msg_id, "method": method, "params": params or {}}
        self._ws.send(json.dumps(msg))
        while True:
            raw = self._ws.recv()
            data = json.loads(raw)
            if data.get("id") == self._msg_id:
                return data.get("result", {})

    def _evaluate(self, js: str):
        result = self._send("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
            "awaitPromise": True,
        })
        return result.get("result", {}).get("value")

    def _navigate(self, url: str):
        self._send("Page.navigate", {"url": url})
        time.sleep(3)

    def _sleep(self, secs: float):
        time.sleep(secs)

    # ============ 发帖操作 ============

    def check_login(self) -> bool:
        result = self._evaluate("""
            (() => {
                // 有用户头像或账户菜单则已登录
                const avatar = document.querySelector('[class*="avatar"], [class*="user-icon"], img[class*="profile"]');
                const loginBtn = document.querySelector('a[href*="/login"], button[class*="login"]');
                return !loginBtn && document.cookie.includes('logined') || !!avatar;
            })()
        """)
        return bool(result)

    def fill_content(self, content: str) -> bool:
        """向 ProseMirror 编辑器注入内容"""
        content_json = json.dumps(content, ensure_ascii=False)
        result = self._evaluate(f"""
            (() => {{
                const editor = document.querySelector('{EDITOR_SELECTOR}');
                if (!editor) return false;
                editor.focus();
                // 清空已有内容
                document.execCommand('selectAll', false, null);
                document.execCommand('delete', false, null);
                // 插入文本（ProseMirror 响应 insertText）
                document.execCommand('insertText', false, {content_json});
                return editor.innerText.trim().length > 0;
            }})()
        """)
        return bool(result)

    def upload_image(self, image_path: str) -> bool:
        """通过 file input 上传图片"""
        import subprocess
        abs_path = os.path.abspath(image_path)
        if not os.path.exists(abs_path):
            print(f"  ❌ 图片不存在: {abs_path}")
            return False

        # 找到文件 input
        node_id = self._evaluate("""
            (() => {
                const btn = Array.from(document.querySelectorAll('button')).find(b =>
                    (b.innerText || '').includes('Upload') || (b.innerText || '').includes('图片')
                );
                if (btn) btn.click();
                return true;
            })()
        """)
        self._sleep(1)

        # 通过 CDP DOM.setFileInputFiles 设置文件
        file_input = self._evaluate("""
            document.querySelector('input[type="file"]') ? 'found' : 'not found'
        """)
        if file_input != "found":
            print("  ⚠️ 未找到文件输入框")
            return False

        # 获取 file input 的 nodeId
        doc = self._send("DOM.getDocument")
        root_id = doc.get("root", {}).get("nodeId", 1)
        node = self._send("DOM.querySelector", {
            "nodeId": root_id,
            "selector": 'input[type="file"]'
        })
        node_id = node.get("nodeId")
        if not node_id:
            return False

        self._send("DOM.setFileInputFiles", {
            "nodeId": node_id,
            "files": [abs_path]
        })
        self._sleep(2)
        print(f"  📎 已上传: {os.path.basename(abs_path)}")
        return True

    def click_publish(self, dry_run: bool = False) -> bool:
        """点击发文按钮"""
        if dry_run:
            print("  [dry-run] 跳过点击发文按钮")
            return True

        result = self._evaluate(f"""
            (() => {{
                const btn = document.querySelector('{PUBLISH_BTN_SELECTOR}');
                if (!btn) return 'not_found';
                if (btn.disabled) return 'disabled';
                btn.click();
                return 'clicked';
            }})()
        """)
        return result == "clicked"

    def publish(self, content: str, image_path: str = "", dry_run: bool = False) -> bool:
        print(f"[square] 导航到币安广场...")
        self._navigate(SQUARE_URL)

        if not self.check_login():
            print("❌ 未检测到登录状态，请先登录")
            return False
        print("[square] 登录状态: ✅")

        # 等待编辑器加载
        for _ in range(10):
            found = self._evaluate(f"!!document.querySelector('{EDITOR_SELECTOR}')")
            if found:
                break
            self._sleep(1)
        else:
            print("❌ 编辑器未加载")
            return False

        # 填写内容
        print(f"[square] 填写内容 ({len(content)} 字)...")
        if not self.fill_content(content):
            print("❌ 内容填写失败")
            return False

        # 上传图片
        if image_path:
            print("[square] 上传图片...")
            self.upload_image(image_path)

        self._sleep(1)

        # 发布
        print("[square] 点击发文...")
        ok = self.click_publish(dry_run=dry_run)
        if ok:
            self._sleep(2)
            print("✅ 发文成功")
        else:
            print("❌ 发文按钮未找到或点击失败")
        return ok

    def close(self):
        if self._ws:
            self._ws.close()


# ============ CLI ============

def main():
    parser = argparse.ArgumentParser(description="币安广场发帖")
    parser.add_argument("--content", required=True, help="发帖内容")
    parser.add_argument("--image", default="", help="图片本地路径（可选）")
    parser.add_argument("--host", default=CDP_HOST)
    parser.add_argument("--port", type=int, default=CDP_PORT)
    parser.add_argument("--dry-run", action="store_true", help="不实际点击发布")
    args = parser.parse_args()

    pub = SquarePublisher(host=args.host, port=args.port)
    try:
        pub.connect()
        ok = pub.publish(
            content=args.content,
            image_path=args.image,
            dry_run=args.dry_run,
        )
        sys.exit(0 if ok else 1)
    finally:
        pub.close()


if __name__ == "__main__":
    main()
