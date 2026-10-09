"""login_views.py — 扫码登录 Blueprint（容器/Xvfb 下把二维码传回浏览器）

设计要点：
- 二维码由 cdp_publish.py 的 get_login_qrcode() 生成（CDP 截屏，headless/Xvfb 均可）。
- 轮询用 login-probe（probe_login_state，零导航查 web_session cookie），不会让正在扫的码失效。
- 绝不自动刷新二维码：过期由前端倒计时后手动刷新。
- 不持久化登录态（它本就在 Chrome profile 里）。
"""
import os
import sys
import json
import time
import uuid
import threading
import subprocess
from pathlib import Path

from flask import Blueprint, request, jsonify

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from account_manager import list_accounts, get_default_account, account_exists  # noqa: E402

login_bp = Blueprint("login", __name__)

PY = str(ROOT / ".venv" / "bin" / "python")
CDP_SCRIPT = str(ROOT / "scripts" / "cdp_publish.py")
TMP_DIR = ROOT / "tmp"
CACHE_FILE = TMP_DIR / "login_status_cache.json"
CURRENT_ACCOUNT_FILE = TMP_DIR / "current_account.txt"
CDP_HOST = os.environ.get("CDP_HOST", "127.0.0.1")
CDP_PORT = os.environ.get("CDP_PORT", "9222")

_qr_jobs: dict = {}
_jobs_lock = threading.Lock()


def _read_current_account():
    try:
        val = CURRENT_ACCOUNT_FILE.read_text(encoding="utf-8").strip()
        return val or None
    except Exception:
        return None


def _write_current_account(name: str):
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    CURRENT_ACCOUNT_FILE.write_text(name, encoding="utf-8")


def _env():
    env = os.environ.copy()
    env["ALL_PROXY"] = ""  # 避免脚本内 urllib 走 socks 代理
    env.setdefault("NO_PROXY", "127.0.0.1,localhost")
    return env


def _base_cmd(account=None, no_cache=True):
    cmd = [PY, CDP_SCRIPT, "--host", CDP_HOST, "--port", str(CDP_PORT)]
    if no_cache:
        cmd.append("--no-login-cache")
    if account:
        cmd += ["--account", account]
    return cmd


def _cache_status():
    """快路径：直读 login_status_cache.json，不导航。"""
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"logged_in": False, "age_seconds": None, "source": "cache-miss"}
    best = None
    for key, ent in (payload.get("entries") or {}).items():
        if not str(key).endswith(":creator") or not isinstance(ent, dict):
            continue
        if ent.get("logged_in") and isinstance(ent.get("checked_at"), (int, float)):
            if best is None or ent["checked_at"] > best["checked_at"]:
                best = ent
    if not best:
        return {"logged_in": False, "age_seconds": None, "source": "cache"}
    return {"logged_in": True, "age_seconds": int(time.time() - best["checked_at"]), "source": "cache"}


def _parse_marker_json(out: str, marker: str):
    idx = out.find(marker)
    if idx < 0:
        return None
    rest = out[idx + len(marker):].lstrip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(rest)
        return obj
    except Exception:
        return None


def _run_qr_job(job_id: str, account, wait_seconds: int):
    with _jobs_lock:
        _qr_jobs[job_id].update(status="running", started_at=time.time())
    cmd = _base_cmd(account) + ["get-login-qrcode", "--wait-seconds", str(wait_seconds)]
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), env=_env(),
                           capture_output=True, text=True, timeout=wait_seconds + 60)
        out = (p.stdout or "")
        data = _parse_marker_json(out, "GET_LOGIN_QRCODE_RESULT:")
        with _jobs_lock:
            if data is None:
                _qr_jobs[job_id].update(status="error",
                                        log=(out + "\n" + (p.stderr or ""))[-2500:])
            else:
                _qr_jobs[job_id].update(
                    status="done",
                    logged_in=bool(data.get("logged_in")),
                    qrcode_data_url=data.get("qrcode_data_url", ""),
                    hint_text=data.get("hint_text", ""),
                    log=out[-2500:],
                )
    except Exception as e:  # noqa: BLE001
        with _jobs_lock:
            _qr_jobs[job_id].update(status="error", log=f"{type(e).__name__}: {e}")


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------
_status_cache = {"t": 0, "value": None}


def _real_creator_status(ttl=30):
    """真实检测 creator 登录：导航 creator 页，按**内容**判断（登录框/扫一扫/网络异常=未登录）。
    仅按 URL 会被 SPA「网络异常但仍停留 /new/home」骗过。带 ttl 缓存。"""
    from websockets.sync.client import connect
    now = time.time()
    if _status_cache["value"] is not None and (now - _status_cache["t"]) < ttl:
        return _status_cache["value"]
    val = False
    try:
        import urllib.request
        tabs = json.loads(urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5).read())
        tab = next((t for t in tabs if t.get("type") == "page" and "creator.xiaohongshu.com" in t.get("url", "")), None)
        if not tab:
            req = urllib.request.Request(f"http://{CDP_HOST}:{CDP_PORT}/json/new?https://creator.xiaohongshu.com", method="PUT")
            tab = json.loads(urllib.request.urlopen(req, timeout=8).read())
        with connect(tab["webSocketDebuggerUrl"], max_size=None) as ws:
            mid = [0]

            def _send(method, params=None, to=15):
                my = mid[0]; mid[0] += 1
                ws.send(json.dumps({"id": my, "method": method, "params": params or {}}))
                while True:
                    mm = json.loads(ws.recv(timeout=to))
                    if mm.get("id") == my:
                        return mm

            _send("Page.enable")
            _send("Page.navigate", {"url": "https://creator.xiaohongshu.com/new/home"})
            time.sleep(5)
            r = _send("Runtime.evaluate", {"returnByValue": True, "expression": (
                "(function(){var h=location.href;"
                "var t=(document.body&&document.body.innerText)||'';"
                "var login=h.indexOf('/login')>=0||!!document.querySelector('.login-container')"
                "||t.indexOf('扫一扫登录')>=0||t.indexOf('网络异常')>=0||t.indexOf('返回重新扫描')>=0;"
                "return {href:h, login:login};})()")})
            v = (r.get("result") or {}).get("result", {}).get("value") or {}
            val = not bool(v.get("login"))
    except Exception:
        val = False
    _status_cache["t"] = now
    _status_cache["value"] = val
    return val


def _www_tab_ws():
    import urllib.request
    tabs = json.loads(urllib.request.urlopen(f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5).read())
    tab = next((t for t in tabs if t.get("type") == "page" and "www.xiaohongshu.com" in t.get("url", "")), None)
    if not tab:
        req = urllib.request.Request(f"http://{CDP_HOST}:{CDP_PORT}/json/new?https://www.xiaohongshu.com/explore", method="PUT")
        tab = json.loads(urllib.request.urlopen(req, timeout=8).read())
    return tab.get("webSocketDebuggerUrl")


def _www_logged_in():
    """浏览页登录态：**不导航**，仅在当前 www 标签上查是否出现登录弹窗(.login-container)。
    有弹窗=未登录。零导航以免刷新二维码使其失效（对齐 creator 的 login-probe）。"""
    from websockets.sync.client import connect
    ws_url = _www_tab_ws()
    if not ws_url:
        return False
    with connect(ws_url, max_size=None) as ws:
        ws.send(json.dumps({"id": 1, "method": "Runtime.evaluate",
                            "params": {"returnByValue": True,
                                       "expression": "!!document.querySelector('.login-container')"}}))
        while True:
            mm = json.loads(ws.recv(timeout=10))
            if mm.get("id") == 1:
                popup = bool((mm.get("result") or {}).get("result", {}).get("value"))
                return not popup



def _www_login_screenshot(wait=6):
    """导航 explore → 优先直接读 .qrcode 的 img(canvas) 为 data URL；拿不到再整页截屏兜底。"""
    import base64
    from websockets.sync.client import connect
    ws_url = _www_tab_ws()
    if not ws_url:
        return None
    with connect(ws_url, max_size=None) as ws:
        mid = [0]

        def _send(method, params=None, to=15):
            my = mid[0]; mid[0] += 1
            ws.send(json.dumps({"id": my, "method": method, "params": params or {}}))
            while True:
                m2 = json.loads(ws.recv(timeout=to))
                if m2.get("id") == my:
                    return m2

        _send("Page.enable")
        _send("Page.navigate", {"url": "https://www.xiaohongshu.com/explore"})
        time.sleep(wait)
        # ① 直接读二维码 img/canvas 的 data URL（等它渲染出来）
        for _ in range(8):
            r = _send("Runtime.evaluate", {"returnByValue": True, "awaitPromise": True,
                                           "expression": _WWW_QR_JS}, to=15)
            url = (r.get("result") or {}).get("result", {}).get("value") or ""
            if isinstance(url, str) and url.startswith("data:image") and 2000 < len(url) < 150000:
                return url          # 合理大小的二维码 data URL（img/canvas）
            time.sleep(1)
        # ② 兜底：截屏 .qrcode 区域
        clip = None
        try:
            rr = _send("Runtime.evaluate", {"returnByValue": True, "expression": (
                "(function(){var e=document.querySelector('.login-container .left .code-area .qrcode')"
                "||document.querySelector('.login-container .left')||document.querySelector('.login-container');"
                "if(!e)return null;var r=e.getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height};})()")})
            clip = (rr.get("result") or {}).get("result", {}).get("value")
        except Exception:
            clip = None
        params = {"format": "png"}
        if clip and clip.get("width", 0) > 50:
            params["clip"] = {"x": clip["x"], "y": clip["y"], "width": clip["width"], "height": clip["height"], "scale": 2}
        r = _send("Page.captureScreenshot", params, to=20)
        data = (r.get("result") or {}).get("data", "")
        return ("data:image/png;base64," + data) if data else None

@login_bp.get("/api/creator/screenshot")
def api_creator_screenshot():
    """返回容器里 Chrome 当前 creator 标签的截图（PNG），供 admin UI 预览。"""
    from flask import Response
    import base64
    import urllib.request
    try:
        from websockets.sync.client import connect
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": f"websockets 不可用: {e}"}), 500
    try:
        targets = json.loads(urllib.request.urlopen(
            f"http://{CDP_HOST}:{CDP_PORT}/json", timeout=5).read())
        tabs = [t for t in targets if t.get("type") == "page" and t.get("webSocketDebuggerUrl")]
        tab = next((t for t in tabs if "creator.xiaohongshu.com" in t.get("url", "")), tabs[0] if tabs else None)
        if not tab:
            return jsonify({"ok": False, "error": "没有可截图的 page 标签"}), 404
        data = ""
        with connect(tab["webSocketDebuggerUrl"], max_size=None) as ws:
            ws.send(json.dumps({"id": 1, "method": "Page.captureScreenshot",
                                "params": {"format": "png"}}))
            while True:
                m = json.loads(ws.recv())
                if m.get("id") == 1:
                    data = (m.get("result") or {}).get("data", "")
                    break
        if not data:
            return jsonify({"ok": False, "error": "captureScreenshot 返回空"}), 502
        return Response(base64.b64decode(data), mimetype="image/png",
                        headers={"Cache-Control": "no-store"})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@login_bp.post("/api/login/tabs/cleanup")
def api_tabs_cleanup():
    """关闭残留 page 标签，保留一个 creator 标签（优先 /new/home）。"""
    import urllib.request
    base = f"http://{CDP_HOST}:{CDP_PORT}"
    try:
        targets = json.loads(urllib.request.urlopen(base + "/json", timeout=5).read())
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500
    pages = [t for t in targets if t.get("type") == "page"]
    keep = next((t for t in pages if "creator.xiaohongshu.com/new/home" in t.get("url", "")), None)
    if not keep:
        keep = next((t for t in pages if "creator.xiaohongshu.com" in t.get("url", "")), None)
    closed = 0
    for t in pages:
        if keep and t.get("id") == keep.get("id"):
            continue
        try:
            urllib.request.urlopen(base + "/json/close/" + t["id"], timeout=5)
            closed += 1
        except Exception:
            pass
    return jsonify({"ok": True, "closed": closed, "kept": (keep or {}).get("url", ""),
                    "before": len(pages)})


@login_bp.get("/api/login/www/status")
def api_www_status():
    try:
        return jsonify({"ok": True, "logged_in": _www_logged_in()})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e), "logged_in": False})


@login_bp.post("/api/login/www/qrcode")
def api_www_qrcode():
    """浏览页登录：截屏 explore 弹窗（含二维码）。"""
    try:
        url = _www_login_screenshot()
        if not url:
            return jsonify({"ok": False, "error": "截图失败"}), 502
        return jsonify({"ok": True, "qrcode_data_url": url, "kind": "screenshot"})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@login_bp.post("/api/login/account")
def api_set_account():
    body = request.get_json(silent=True) or {}
    acc = body.get("account")
    if not acc or not account_exists(acc):
        return jsonify({"ok": False, "error": "invalid account"}), 400
    _write_current_account(acc)
    # 账号切换 = 重启 headed Chrome（异步，避免阻塞）
    def _restart():
        try:
            from chrome_launcher import restart_chrome
            restart_chrome(port=int(CDP_PORT), headless=False, account=acc)
        except Exception:
            pass
    threading.Thread(target=_restart, daemon=True).start()
    return jsonify({"ok": True, "account": acc, "restarting": True})


def _run_login_cmd(command: str, account):
    cmd = _base_cmd(account, no_cache=True) + [command]
    try:
        p = subprocess.run(cmd, cwd=str(ROOT), env=_env(),
                           capture_output=True, text=True, timeout=60)
        return jsonify({"ok": p.returncode == 0, "log": (p.stdout or "")[-1500:]})
    except Exception as e:  # noqa: BLE001
        return jsonify({"ok": False, "error": str(e)}), 500


@login_bp.post("/api/login/relogin")
def api_relogin():
    body = request.get_json(silent=True) or {}
    return _run_login_cmd("re-login", body.get("account") or _read_current_account())


@login_bp.post("/api/login/switch-account")
def api_switch():
    body = request.get_json(silent=True) or {}
    return _run_login_cmd("switch-account", body.get("account") or _read_current_account())


# --------------------------------------------------------------------------
# 自带扫码页（POC 用；正式接入 admin UI 可后续做）
# --------------------------------------------------------------------------
LOGIN_PAGE = r"""<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>扫码登录 — 小红书</title>
<style>
:root{--bg:#0f0f10;--bg2:#16161a;--br:#2a2a2e;--t:#e8e8e8;--t2:#8a8a8a;--ac:#ff2442;--gn:#07c160}
*{margin:0;padding:0;box-sizing:border-box}
body{font:14px/1.5 -apple-system,"PingFang SC",sans-serif;background:var(--bg);color:var(--t);
display:flex;justify-content:center;align-items:center;min-height:100vh}
.card{width:360px;padding:24px;background:var(--bg2);border:1px solid var(--br);border-radius:12px;text-align:center}
h1{font-size:16px;margin-bottom:4px}
.sub{color:var(--t2);font-size:12px;margin-bottom:16px}
.dot{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:#666}
.dot.on{background:var(--gn)}.dot.off{background:#ffc53d}
#qr{width:280px;height:280px;margin:0 auto;background:#111;border:1px solid var(--br);border-radius:8px;
display:flex;align-items:center;justify-content:center;color:var(--t2)}
#qr img{width:100%;height:100%;border-radius:8px}
select,button{width:100%;padding:10px;margin-top:10px;border-radius:8px;border:1px solid var(--br);
background:#1c1c20;color:var(--t);font-size:14px;cursor:pointer}
button.primary{background:var(--ac);border-color:var(--ac);color:#fff;font-weight:600}
button:disabled{opacity:.5;cursor:not-allowed}
#hint,#log{margin-top:10px;font-size:12px;color:var(--t2);white-space:pre-wrap;word-break:break-all}
#log{max-height:120px;overflow:auto;text-align:left;font-family:ui-monospace,monospace;display:none}
</style></head><body>
<div class="card">
  <h1><span id="dot" class="dot off"></span>小红书扫码登录</h1>
  <div class="sub" id="stat">加载中…</div>
  <select id="acc"></select>
  <select id="target"><option value="creator">创作号 (creator.xiaohongshu.com)</option><option value="www">浏览页 (www.xiaohongshu.com)</option></select>
  <div id="qr">二维码未加载</div>
  <div id="hint"></div>
  <button class="primary" id="btnGet">获取二维码</button>
  <button id="btnCheck">我已扫码，检查</button>
  <button id="btnRe">强制重新登录（清 Cookie）</button>
  <div id="log"></div>
</div>
<script>
let jobId=null, timer=null, expireAt=0;
const $=id=>document.getElementById(id);
async function j(url,opt){const r=await fetch(url,opt);return r.json();}
async function loadAccounts(){
  const d=await j('/api/login/accounts');
  const s=$('acc'); s.innerHTML='';
  (d.accounts||[]).forEach(a=>{const o=document.createElement('option');o.value=a.name;
    o.textContent=a.name+(a.alias?' ('+a.alias+')':'')+(a.is_default?' *':'');s.appendChild(o);});
  if(d.current)s.value=d.current;
}
async function refreshStatus(){
  const d=await j('/api/login/status');
  const on=d.logged_in;
  $('dot').className='dot '+(on?'on':'off');
  $('stat').textContent=on?('已登录'+(d.age_seconds!=null?('（缓存 '+d.age_seconds+'s）'):'')):'未登录 / 无缓存';
}
async function getQR(){
  clearInterval(timer);
  $('btnGet').disabled=true; $('qr').innerHTML='获取中…'; $('hint').textContent=''; $('log').style.display='none';
  if($('target').value==='www'){
    const d=await j('/api/login/www/qrcode',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'});
    if(d.ok&&d.qrcode_data_url){$('qr').innerHTML='<img src="'+d.qrcode_data_url+'" style="width:100%">';$('hint').textContent='请用小红书 App 扫图中二维码（浏览页登录）';}
    else{$('qr').textContent='失败: '+(d.error||'');}
    $('btnGet').disabled=false; timer=setInterval(pollWww,3000); return;
  }
  const d=await j('/api/login/qrcode',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({account:$('acc').value})});
  if(!d.ok){$('qr').textContent='失败: '+(d.error||'');$('btnGet').disabled=false;return;}
  jobId=d.job_id; expireAt=Date.now()+120000;
  timer=setInterval(poll,2000);
}
async function pollWww(){
  const d=await j('/api/login/www/status');
  if(d.logged_in){clearInterval(timer);$('dot').className='dot on';$('stat').textContent='浏览页已登录 ✓';}
}
async function poll(){
  if(!jobId)return;
  if(Date.now()>expireAt){clearInterval(timer);$('hint').textContent='二维码已过期，请重新获取';$('btnGet').disabled=false;return;}
  const d=await j('/api/login/qrcode/'+jobId);
  if(d.status==='done'){
    if(d.logged_in){gotLoggedIn();return;}
    if(d.qrcode_data_url){$('qr').innerHTML='<img src="'+d.qrcode_data_url+'">';$('hint').textContent=d.hint_text||'';}
    $('btnGet').disabled=false;
  } else if(d.status==='error'){
    clearInterval(timer);$('qr').textContent='生成失败';$('log').style.display='block';$('log').textContent=d.log||'';$('btnGet').disabled=false;
    return;
  }
  // 同时打零导航探针
  const p=await j('/api/login/qrcode/'+jobId+'/probe',{method:'POST'});
  if(p.logged_in){gotLoggedIn();}
}
function gotLoggedIn(){clearInterval(timer);$('dot').className='dot on';$('stat').textContent='已登录 ✓';
  $('qr').innerHTML='登录成功';$('hint').textContent='';}
$('btnGet').onclick=getQR;
$('btnCheck').onclick=async()=>{const d=await j('/api/login/status/refresh',{method:'POST',
  headers:{'Content-Type':'application/json'},body:JSON.stringify({account:$('acc').value})});
  $('log').style.display='block';$('log').textContent=d.log||'';await refreshStatus();};
$('btnRe').onclick=async()=>{if(!confirm('清除 Cookie 并重新登录？'))return;
  const d=await j('/api/login/relogin',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({account:$('acc').value})});$('log').style.display='block';$('log').textContent=d.log||'';};
loadAccounts();refreshStatus();
</script></body></html>"""


@login_bp.get("/login")
def login_page():
    from flask import Response
    return Response(LOGIN_PAGE, mimetype="text/html")
