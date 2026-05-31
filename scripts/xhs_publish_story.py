#!/usr/bin/env python3
"""将数据库中的 story 长文填入小红书创作服务平台，或导出为 markdown 文件。
用法:
  python xhs_publish_story.py <news_key>           # CDP 自动填入 XHS 编辑器
  python xhs_publish_story.py <news_key> --export  # 导出 md 文件到 ~/.cache/xhs_exports/

功能:
1. CDP 模式: 自动填入标题、正文、图片到 XHS 创作页
2. 导出模式: 生成带 base64 图片的 markdown，手动导入 XHS
"""
import sys, os, time, json, re
import requests
import websocket

CDP = "http://127.0.0.1:9222"
GALLERY_BASE = os.path.expanduser("~/.cache/xhs_images")

def find_xhs_tab():
    """找到小红书创作页的 CDP tab"""
    tabs = requests.get(f"{CDP}/json").json()
    for t in tabs:
        url = t.get('url', '')
        if 'creator.xiaohongshu.com/publish' in url:
            return t
    return None

def cdp_eval(ws, expression, await_promise=False, timeout=10):
    """执行 JS 并返回结果"""
    import threading
    result = [None]

    def _do():
        nonlocal_result = None
        try:
            ws.send(json.dumps({"id": 99, "method": "Runtime.evaluate",
                "params": {"expression": expression, "returnByValue": True,
                          "awaitPromise": await_promise}}))
            end = time.time() + timeout
            while time.time() < end:
                try:
                    ws.settimeout(1)
                    msg = json.loads(ws.recv())
                    if msg.get('id') == 99:
                        val = msg.get('result', {}).get('result', {}).get('value', '')
                        result[0] = val
                        return
                except:
                    pass
        except Exception as e:
            result[0] = f"Error: {e}"

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout + 2)
    return result[0]

def fill_title(ws, title: str):
    """填入标题"""
    title = title.replace('"', '\"').replace('\n', ' ')
    js = f"""
    (() => {{
        const ta = document.querySelector('textarea[placeholder*="标题"]');
        if (!ta) return 'no title textarea';
        const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value').set;
        setter.call(ta, {json.dumps(title)});
        ta.dispatchEvent(new Event('input', {{bubbles: true}}));
        ta.dispatchEvent(new Event('change', {{bubbles: true}}));
        return 'ok: ' + ta.value.substring(0, 40);
    }})()
    """
    return cdp_eval(ws, js)

def fill_body(ws, intro: str, body: str, outro: str = ""):
    """填入正文到 ProseMirror 编辑器。图片占位符用带 id 的 span 标记，后续可定位。"""
    parts = []
    if intro:
        parts.append(f'<blockquote class="tt-block-quote"><p>{intro}</p></blockquote><p></p>')

    img_idx = 0
    paragraphs = body.split('\n\n')
    for p in paragraphs:
        p = p.strip()
        if not p:
            continue
        if p.startswith('## '):
            parts.append(f'<h2>{p[3:]}</h2>')
        elif re.match(r'【图片\d+[：:]', p):
            img_idx += 1
            desc = re.sub(r'【图片\d+[：:]\s*', '', p).strip('】')
            # 用文字标记图片位置，上传后删除
            parts.append(f'<p>📷</p>')
        else:
            parts.append(f'<p>{p}</p>')

    if outro:
        parts.append(f'<p></p><p><mark>{outro}</mark></p>')

    html = ''.join(parts)
    js = f"""
    (() => {{
        const pm = document.querySelector('.ProseMirror');
        if (!pm) return 'no editor';
        pm.focus();
        pm.innerHTML = {json.dumps(html)};
        pm.dispatchEvent(new Event('input', {{bubbles: true}}));
        pm.dispatchEvent(new Event('change', {{bubbles: true}}));
        return JSON.stringify({{chars: pm.textContent.length, placeholders: {img_idx}}});
    }})()
    """
    return cdp_eval(ws, js)

def upload_images(ws, image_paths: list[str]):
    """逐张上传图片到 📷 占位符位置。
    每张图：找 📷 → 点击定位光标 → 点图片按钮 → CDP拦截注入 → 删 📷。
    """
    if not image_paths:
        return "no images"

    valid = [p for p in image_paths if os.path.exists(p)]
    if not valid:
        return f"no valid files"

    # Enable interception
    ws.send(json.dumps({"id": 30, "method": "Page.enable"}))
    time.sleep(0.2)
    ws.send(json.dumps({"id": 31, "method": "Page.setInterceptFileChooserDialog",
        "params": {"enabled": True}}))
    time.sleep(0.2)

    # Get image button position once
    pos_data = cdp_eval(ws, """
    (() => {
        const btns = document.querySelectorAll('.menu-items-container .menu-item');
        const r = btns[8].getBoundingClientRect();
        return JSON.stringify({x: Math.round(r.x + r.width/2), y: Math.round(r.y + r.height/2)});
    })()
    """)
    if not pos_data or 'NO_BTN' in str(pos_data):
        ws.send(json.dumps({"id": 32, "method": "Page.setInterceptFileChooserDialog", "params": {"enabled": False}}))
        return "image button not found"
    btn_pos = json.loads(pos_data)

    for i, path in enumerate(valid):
        fname = os.path.basename(path)
        print(f"  [{i+1}/{len(valid)}] {fname}")

        # Step 1: Find the first 📷 and get its position
        ph_pos_data = cdp_eval(ws, """
        (() => {
            const allP = document.querySelectorAll('.ProseMirror p');
            for (const p of allP) {
                if (p.textContent.trim() === '📷') {
                    // Focus the editor and set range at this paragraph
                    const pm = document.querySelector('.ProseMirror');
                    pm.focus();
                    const sel = window.getSelection();
                    const range = document.createRange();
                    range.setStartBefore(p);
                    range.collapse(true);
                    sel.removeAllRanges();
                    sel.addRange(range);

                    const r = p.getBoundingClientRect();
                    return JSON.stringify({x: Math.round(r.x + 5), y: Math.round(r.y + r.height/2)});
                }
            }
            return 'NO_MARKER';
        })()
        """)
        if not ph_pos_data or 'NO_MARKER' in str(ph_pos_data):
            print(f"    no 📷 marker found")
            break

        ph_pos = json.loads(ph_pos_data)
        time.sleep(0.3)

        # Step 2: CDP click on the 📷 position to set cursor there
        ws.send(json.dumps({"id": 100+i*10, "method": "Input.dispatchMouseEvent",
            "params": {"type": "mousePressed", "x": ph_pos['x'], "y": ph_pos['y'],
                       "button": "left", "clickCount": 1}}))
        time.sleep(0.1)
        ws.send(json.dumps({"id": 101+i*10, "method": "Input.dispatchMouseEvent",
            "params": {"type": "mouseReleased", "x": ph_pos['x'], "y": ph_pos['y'],
                       "button": "left", "clickCount": 1}}))
        time.sleep(0.5)

        # Step 3: CDP click the image toolbar button
        ws.send(json.dumps({"id": 102+i*10, "method": "Input.dispatchMouseEvent",
            "params": {"type": "mousePressed", "x": btn_pos['x'], "y": btn_pos['y'],
                       "button": "left", "clickCount": 1}}))
        time.sleep(0.1)
        ws.send(json.dumps({"id": 103+i*10, "method": "Input.dispatchMouseEvent",
            "params": {"type": "mouseReleased", "x": btn_pos['x'], "y": btn_pos['y'],
                       "button": "left", "clickCount": 1}}))

        # Step 4: Wait for file chooser
        backend_id = None
        end = time.time() + 8
        while time.time() < end:
            try:
                ws.settimeout(1)
                msg = json.loads(ws.recv())
                if msg.get('method') == 'Page.fileChooserOpened':
                    backend_id = msg['params']['backendNodeId']
                    break
            except: pass

        if not backend_id:
            print(f"    file chooser timeout")
            continue

        # Step 5: Inject file
        ws.send(json.dumps({"id": 104+i*10, "method": "DOM.setFileInputFiles",
            "params": {"backendNodeId": backend_id, "files": [path]}}))
        time.sleep(3)

        # Step 6: Delete the 📷 marker
        cdp_eval(ws, """
        (() => {
            const allP = document.querySelectorAll('.ProseMirror p');
            for (const p of allP) {
                if (p.textContent.trim() === '📷') { p.remove(); return 'removed'; }
            }
            return 'none';
        })()
        """)
        time.sleep(0.5)

        print(f"    ✅ done")

    # Disable interception
    ws.send(json.dumps({"id": 200, "method": "Page.setInterceptFileChooserDialog", "params": {"enabled": False}}))

    final = cdp_eval(ws, """
    (async () => {
        await new Promise(r => setTimeout(r, 2000));
        return JSON.stringify({
            imgNodes: document.querySelectorAll('.ProseMirror [data-dom-type="image"]').length,
            markersLeft: [...document.querySelectorAll('.ProseMirror p')].filter(p => p.textContent.trim() === '📷').length
        });
    })()
    """, await_promise=True, timeout=8)
    print(f"  完成: {final}")

    return f"uploaded {len(valid)}, {final}"

XHS_PUBLISH_URL = "https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=article"
EXPORT_DIR = os.path.expanduser("~/.cache/xhs_exports")

def export_to_md(news_key: str):
    """导出文章为 markdown 文件（base64 内嵌图片），返回文件路径"""
    os.makedirs(EXPORT_DIR, exist_ok=True)

    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)
    sys.path.insert(0, _script_dir)
    sys.path.insert(0, _project_root)
    from scripts.sqlite_db import get_by_key

    row = get_by_key(news_key)
    if not row:
        print(f"❌ 文章不存在: {news_key}")
        return None

    title = row.get('title', '')
    summary = row.get('summary', '')
    comment = row.get('comment', '')
    gallery = row.get('gallery_images', [])
    if isinstance(gallery, str):
        gallery = json.loads(gallery)
    publish_mode = row.get('publish_mode', 'normal')
    rewritten = row.get('rewritten_content', '') or ''

    # 改写长文模式：用 rewritten_content 作为正文，rewritten_title 作为标题
    if publish_mode == 'rewritten' and rewritten.strip():
        content = rewritten
        rwt = row.get('rewritten_title', '') or ''
        if rwt.strip():
            title = rwt
        summary = ''
        comment = ''
    else:
        content = row.get('content', '')

    # Build markdown
    md = f"# {title}\n\n"
    if summary:
        md += f"> {summary}\n\n"

    paragraphs = content.split('\n\n') if content else []
    img_idx = 0
    for p in paragraphs:
        p = p.strip()
        if not p: continue
        if p.startswith('## '):
            md += f"## {p[3:]}\n\n"
        elif (p.startswith('【图片') or p.startswith('【推文')) and ('：' in p or ':' in p):
            if img_idx < len(gallery):
                img_path = gallery[img_idx]
                if os.path.exists(img_path):
                    import base64 as _b64
                    with open(img_path, 'rb') as f:
                        b64 = _b64.b64encode(f.read()).decode()
                    ext = os.path.splitext(img_path)[1].lower().replace('.jpg','jpeg')
                    md += f"![图片{img_idx+1}](data:image/{ext};base64,{b64})\n\n"
                img_idx += 1
        else:
            md += f"{p}\n\n"

    if comment:
        md += "---\n\n"
        md += f"<mark>{comment}</mark>\n"

    # 文件名: 标题前10字_key前8位.md，去掉文件名不合法字符
    safe_title = re.sub(r'[\\/:*?"<>|]', '', title)[:10]
    fname = f"{safe_title}_{news_key[:8]}.md"
    out_path = os.path.join(EXPORT_DIR, fname)
    with open(out_path, 'w', encoding='utf-8') as f:
        f.write(md)

    size_kb = os.path.getsize(out_path) // 1024
    print(f"✅ {out_path} ({size_kb}KB, {img_idx} 张图片)")
    return out_path

def ensure_editor_page(ws):
    """确保页面在长文编辑器中。如果不是，导航过去并点击"新的创作"。"""
    # 检查当前是否已在编辑器中
    check = cdp_eval(ws, """
    (() => {
        const hasEditor = !!document.querySelector('.ProseMirror');
        const hasTitle = !!document.querySelector('textarea[placeholder*="标题"]');
        const url = location.href;
        return JSON.stringify({hasEditor, hasTitle, url});
    })()
    """)
    print(f"  当前状态: {check}")

    if check and '"hasEditor":true' in str(check) and '"hasTitle":true' in str(check):
        return True  # already in editor

    # Navigate to the correct URL
    print(f"  导航到: {XHS_PUBLISH_URL}")
    ws.send(json.dumps({"id": 50, "method": "Page.enable"}))
    time.sleep(0.3)
    ws.send(json.dumps({"id": 51, "method": "Page.navigate",
        "params": {"url": XHS_PUBLISH_URL}}))
    time.sleep(6)

    # Check if we need to click "新的创作" — look for any button with 创作 or 新建
    btn_result = cdp_eval(ws, """
    (() => {
        const btns = document.querySelectorAll('button, [role="button"], a');
        for (const b of btns) {
            const text = (b.textContent || '').trim();
            if (text.includes('创作') || text.includes('新建') || text.includes('写文章') || text.includes('发布')) {
                b.click();
                return 'clicked: ' + text;
            }
        }
        // Check if we're already in the editor
        if (document.querySelector('.ProseMirror')) return 'already in editor';
        return 'no button found, body text: ' + (document.body?.innerText || '').substring(0, 200);
    })()
    """)
    print(f"  {btn_result}")
    time.sleep(4)

    # Verify editor loaded
    verify = cdp_eval(ws, """
    (() => {
        return JSON.stringify({
            hasEditor: !!document.querySelector('.ProseMirror'),
            hasTitle: !!document.querySelector('textarea[placeholder*="标题"]'),
            bodyLen: document.body?.innerText?.length || 0
        });
    })()
    """)
    print(f"  验证: {verify}")
    return verify and '"hasEditor":true' in str(verify)

def main():
    if len(sys.argv) < 2:
        print("用法: python xhs_publish_story.py <news_key> [--export]")
        sys.exit(1)

    news_key = sys.argv[1]
    export_only = '--export' in sys.argv

    if export_only:
        export_to_md(news_key)
        return

    # 从 DB 读取文章
    _script_dir = os.path.dirname(os.path.abspath(__file__))
    _project_root = os.path.dirname(_script_dir)
    sys.path.insert(0, _script_dir)
    sys.path.insert(0, _project_root)
    from scripts.sqlite_db import get_by_key
    row = get_by_key(news_key)
    if not row:
        print(f"❌ 文章不存在: {news_key}")
        sys.exit(1)

    title = row.get('title', '')
    content = row.get('content', '')
    summary = row.get('summary', '')
    comment = row.get('comment', '')
    gallery = row.get('gallery_images', [])
    if isinstance(gallery, str):
        try: gallery = json.loads(gallery)
        except: gallery = []

    print(f"📄 {title[:60]}")
    print(f"   正文: {len(content or '')} 字")
    print(f"   图集: {len(gallery)} 张")

    # 找到或创建 CDP tab
    tab = find_xhs_tab()
    if not tab:
        print(f"  创建新 tab...")
        resp = requests.put(f"{CDP}/json/new?url={XHS_PUBLISH_URL}", timeout=5)
        tab = resp.json()
        time.sleep(5)

    print(f"✅ tab: {tab['url'][:80]}")

    # 连接 WebSocket
    ws = websocket.create_connection(tab['webSocketDebuggerUrl'], timeout=30)
    ws.send(json.dumps({"id": 0, "method": "Runtime.enable"}))
    time.sleep(0.5)

    # 确保在编辑器中
    print("🔍 检查编辑器状态...")
    if not ensure_editor_page(ws):
        print('❌ 无法进入编辑器，请手动打开 https://creator.xiaohongshu.com/publish/publish?source=official&from=menu&target=article 并点击"新的创作"')
        ws.close()
        sys.exit(1)

    # 填入标题
    print("📝 填入标题...")
    r = fill_title(ws, title)
    print(f"   {r}")

    # 填入正文
    print("📝 填入正文...")
    r = fill_body(ws, summary or '', content or '', comment or '')
    print(f"   {r}")

    # 上传图片
    print("🖼️ 上传图片...")
    r = upload_images(ws, gallery)
    print(f"   {r}")

    ws.close()
    print("\n✅ 完成！请在浏览器中微调后手动发布。")

if __name__ == '__main__':
    main()
