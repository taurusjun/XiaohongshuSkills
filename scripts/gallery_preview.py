#!/usr/bin/env python3
"""
本地 Flask 预览服务：展示 ~/.cache/xhs_images/ 下待选图片，
勾选后点「确认上传」写入 selected.json，然后运行 gallery_upload.py 上传。

用法:
    python scripts/gallery_preview.py
    python scripts/gallery_preview.py --port 5050
"""

import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path

import requests
from flask import Flask, jsonify, render_template_string, request, send_file

CACHE_DIR = Path.home() / ".cache" / "xhs_images"

app = Flask(__name__)
_shutdown_event = threading.Event()

HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>图集预览</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, sans-serif; background: #f5f5f5; padding: 20px; }
  h1 { font-size: 18px; margin-bottom: 20px; color: #333; }
  .article { background: #fff; border-radius: 8px; padding: 16px; margin-bottom: 24px;
             box-shadow: 0 1px 4px rgba(0,0,0,.1); }
  .article-title { font-size: 14px; font-weight: 600; color: #222; margin-bottom: 12px;
                   padding-bottom: 8px; border-bottom: 1px solid #eee; }
  .article-meta { font-size: 11px; color: #888; margin-bottom: 12px; }
  .controls { margin-bottom: 10px; }
  .controls button { font-size: 12px; padding: 4px 10px; margin-right: 6px; cursor: pointer;
                     border: 1px solid #ccc; border-radius: 4px; background: #fff; }
  .grid { display: flex; flex-wrap: wrap; gap: 8px; }
  .thumb { position: relative; width: 160px; }
  .thumb img { width: 160px; height: 120px; object-fit: cover; border-radius: 4px;
               border: 3px solid transparent; cursor: pointer; display: block; }
  .thumb input[type=checkbox] { position: absolute; top: 6px; left: 6px; width: 18px; height: 18px; cursor: pointer; }
  .thumb.selected img { border-color: #1677ff; }
  .count { font-size: 12px; color: #666; margin-top: 4px; text-align: center; }
  .sticky { position: sticky; bottom: 0; background: #fff; padding: 14px 20px;
            border-top: 1px solid #e0e0e0; display: flex; align-items: center; gap: 12px; z-index: 10; }
  #submit-btn { background: #1677ff; color: #fff; border: none; padding: 10px 28px;
                font-size: 15px; border-radius: 6px; cursor: pointer; }
  #submit-btn:hover { background: #0958d9; }
  #status { font-size: 13px; color: #52c41a; }
  .empty { color: #aaa; font-size: 13px; padding: 20px 0; }
</style>
</head>
<body>
<h1>📷 图集预览 — 勾选要上传的图片</h1>

{% if articles %}
{% for art in articles %}
<div class="article">
  <div class="article-title">{{ art.title }}</div>
  <div class="article-meta">key: {{ art.key }} &nbsp;|&nbsp; <a href="{{ art.gallery_url }}" target="_blank">图集来源</a></div>
  <div class="controls">
    <button onclick="selectAll('{{ art.key }}')">全选</button>
    <button onclick="deselectAll('{{ art.key }}')">取消全选</button>
  </div>
  <div class="grid">
    {% for img in art.images %}
    <div class="thumb" id="thumb-{{ art.key }}-{{ loop.index0 }}" onclick="toggle('{{ art.key }}', {{ loop.index0 }})">
      <img src="/img/{{ art.key }}/{{ img }}" alt="{{ img }}">
      <input type="checkbox" id="cb-{{ art.key }}-{{ loop.index0 }}" data-key="{{ art.key }}" data-img="{{ img }}"
             onclick="event.stopPropagation(); syncThumb('{{ art.key }}', {{ loop.index0 }})">
      <div class="count">{{ img }}</div>
    </div>
    {% endfor %}
  </div>
</div>
{% endfor %}
{% else %}
<div class="empty">没有待预览的图集。先运行 gallery_fetch.py 抓取图片。</div>
{% endif %}

<div class="sticky">
  <button id="submit-btn" onclick="submitSelection()">✅ 确认上传</button>
  <span id="status"></span>
</div>

<script>
function syncThumb(key, idx) {
  const cb = document.getElementById(`cb-${key}-${idx}`);
  const thumb = document.getElementById(`thumb-${key}-${idx}`);
  thumb.classList.toggle('selected', cb.checked);
}
function toggle(key, idx) {
  const cb = document.getElementById(`cb-${key}-${idx}`);
  cb.checked = !cb.checked;
  syncThumb(key, idx);
}
function selectAll(key) {
  document.querySelectorAll(`[data-key="${key}"]`).forEach((cb, i) => {
    cb.checked = true;
    document.getElementById(`thumb-${key}-${i}`)?.classList.add('selected');
  });
}
function deselectAll(key) {
  document.querySelectorAll(`[data-key="${key}"]`).forEach((cb, i) => {
    cb.checked = false;
    document.getElementById(`thumb-${key}-${i}`)?.classList.remove('selected');
  });
}
function submitSelection() {
  const selection = {};
  document.querySelectorAll('input[type=checkbox]').forEach(cb => {
    if (!selection[cb.dataset.key]) selection[cb.dataset.key] = [];
    if (cb.checked) selection[cb.dataset.key].push(cb.dataset.img);
  });
  const total = Object.values(selection).reduce((s, v) => s + v.length, 0);
  if (total === 0) { alert('请至少勾选一张图片'); return; }
  document.getElementById('status').textContent = '保存中...';
  fetch('/confirm', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify(selection)
  }).then(r => r.json()).then(d => {
    document.getElementById('status').textContent = `✅ 已保存 ${total} 张，窗口可关闭`;
    document.getElementById('submit-btn').disabled = true;
  });
}
</script>
</body>
</html>
"""


def load_pending_articles() -> list[dict]:
    """加载有 meta.json 但无 selected.json 的图集"""
    articles = []
    if not CACHE_DIR.exists():
        return articles
    for article_dir in sorted(CACHE_DIR.iterdir()):
        meta_path = article_dir / "meta.json"
        selected_path = article_dir / "selected.json"
        uploaded_flag = article_dir / "uploaded.flag"
        if not meta_path.exists() or uploaded_flag.exists():
            continue
        # 已选过也展示（允许重新选）
        with open(meta_path) as f:
            meta = json.load(f)
        images = [f for f in sorted(article_dir.iterdir())
                  if f.suffix in (".jpg", ".jpeg", ".png", ".webp")]
        meta["images"] = [img.name for img in images]
        articles.append(meta)
    return articles


@app.route("/")
def index():
    articles = load_pending_articles()
    return render_template_string(HTML, articles=articles)


@app.route("/img/<key>/<filename>")
def serve_image(key, filename):
    img_path = CACHE_DIR / key / filename
    if img_path.exists():
        resp = send_file(img_path)
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        return resp
    return "Not found", 404


@app.route("/confirm", methods=["POST"])
def confirm():
    selection: dict = request.get_json()
    for key, images in selection.items():
        article_dir = CACHE_DIR / key
        if article_dir.exists():
            with open(article_dir / "selected.json", "w") as f:
                json.dump({"selected": images}, f, ensure_ascii=False, indent=2)
            print(f"  ✅ {key}: 已选 {len(images)} 张 → selected.json")
    # 延迟关闭让响应先返回
    threading.Timer(1.0, _shutdown_event.set).start()
    return jsonify({"ok": True})


def cdp_navigate(url: str, cdp_host: str = "127.0.0.1", cdp_port: int = 9222) -> bool:
    """用 CDP 在已有 Chrome 中打开 url，成功返回 True"""
    import json as _json
    try:
        import websocket
    except ImportError:
        return False
    try:
        targets = requests.get(f"http://{cdp_host}:{cdp_port}/json", timeout=3).json()
        # 找第一个普通 page（排除 devtools、extension 页）
        page = next(
            (t for t in targets
             if t.get("type") == "page" and "devtools" not in t.get("url", "")),
            None,
        )
        if not page:
            return False
        ws = websocket.create_connection(page["webSocketDebuggerUrl"], timeout=5)
        ws.send(_json.dumps({"id": 1, "method": "Page.navigate", "params": {"url": url}}))
        ws.recv()
        ws.close()
        print(f"  ✅ CDP 已导航到: {url}")
        return True
    except Exception as e:
        print(f"  ⚠️ CDP 导航失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="图集本地预览服务")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--cdp-port", type=int, default=9222)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    url = f"http://127.0.0.1:{args.port}"
    print(f"🌐 预览地址: {url}")
    print("勾选图片后点「确认上传」，服务自动关闭")

    # 启动 Flask（先启动再导航，避免 Chrome 加载时 server 还没就绪）
    flask_thread = threading.Thread(
        target=lambda: app.run(host="127.0.0.1", port=args.port, debug=False, use_reloader=False),
        daemon=True,
    )
    flask_thread.start()

    if not args.no_browser:
        def open_browser():
            import time
            time.sleep(0.8)  # 等 Flask 就绪
            if not cdp_navigate(url, cdp_port=args.cdp_port):
                print("  回退：用系统浏览器打开")
                webbrowser.open(url)

        threading.Thread(target=open_browser, daemon=True).start()

    _shutdown_event.wait()
    print("\n✅ 选择已保存，开始上传...")

    import subprocess
    script_dir = Path(__file__).parent
    subprocess.run([sys.executable, str(script_dir / "gallery_upload.py")])


if __name__ == "__main__":
    main()
