#!/usr/bin/env python3
"""新闻管理 Web UI — SQLite 版 Notion 替代"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))  # project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from flask import Flask, jsonify, render_template_string, request, send_file
from config.yahoo_conf import STORAGE_BACKEND
from sqlite_db import init_db, query_news, get_by_key, update_news, get_scores, upsert_scores, stats
from web.gallery_downloader import trigger_download, get_status as gstatus, upload_selected

import subprocess, json, glob, threading, time, shutil
from datetime import datetime as _dt_ad2
from pathlib import Path as _Path_ad2

# Task log persistence
TASK_LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'logs')
def _init_log_dir():
    p = _Path_ad2(TASK_LOG_DIR); p.mkdir(parents=True, exist_ok=True)
    # Cleanup logs older than 1 day
    cutoff = _dt_ad2.now().timestamp() - 86400
    for f in p.glob('task_*.log'):
        if f.stat().st_mtime < cutoff:
            f.unlink()
_init_log_dir()

def _save_task_log(task_id: str, log_text: str):
    try:
        today = _dt_ad2.now().strftime('%Y-%m-%d')
        log_path = os.path.join(TASK_LOG_DIR, f'task_{today}.log')
        with open(log_path, 'a') as f:
            f.write(f"\n=== {task_id} {_dt_ad2.now().strftime('%H:%M:%S')} ===\n{log_text}\n")
    except Exception:
        pass

# Notion 模式提示页
NOTION_ONLY = """<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<style>body{{font:16px -apple-system,sans-serif;display:flex;justify-content:center;
align-items:center;height:100vh;background:#f8f8f8;color:#888}}
p{{text-align:center;font-size:18px}}</style></head>
<body><p>当前是 Notion 配置，请使用命令行操作<br>
<small>STORAGE_BACKEND=notion</small></p></body></html>"""

app = Flask(__name__)
init_db()

@app.before_request
def check_backend():
    if STORAGE_BACKEND == "notion":
        return NOTION_ONLY, 200

# Background task tracking {task_id: status}
_tasks = {}
_task_counter = 0
_publish_lock = threading.Lock()
_publish_running = False
_fetch_lock = threading.Lock()
_fetch_running = False
_regen_lock = threading.Lock()
_regen_keys = set()

def _run_task(cmd, task_id, env=None, on_done=None):
    log_lines = []
    if env is None:
        env = {}
    env.setdefault('PYTHONUNBUFFERED', '1')
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1,
                                cwd=os.path.join(os.path.dirname(__file__), '..', 'scripts'),
                                env=env)
        # Open log file for incremental writes
        today = _dt_ad2.now().strftime('%Y-%m-%d')
        log_path = os.path.join(TASK_LOG_DIR, f'task_{today}.log')
        with open(log_path, 'a', buffering=1) as log_fh:
            log_fh.write(f"\n=== {task_id} {_dt_ad2.now().strftime('%H:%M:%S')} ===\n")
            for line in proc.stdout:
                log_lines.append(line.rstrip())
                log_fh.write(line)
                _tasks[task_id] = {'status': 'running', 'log': '\n'.join(log_lines)}
        proc.wait(timeout=7200)
        log = '\n'.join(log_lines).strip()
        _tasks[task_id] = {'status': 'done', 'log': log}
    except Exception as e:
        _tasks[task_id] = {'status': f'error: {e}', 'log': '\n'.join(log_lines)}
        _save_task_log(task_id, '\n'.join(log_lines) + f'\nERROR: {e}')
    finally:
        if on_done:
            on_done()

@app.route('/api/gallery-download/<key>', methods=['POST'])
def api_gallery_download(key):
    # Per-key lock: check if already running
    from web.gallery_downloader import _tasks as _gtasks
    t = _gtasks.get(key, {})
    if isinstance(t, dict) and t.get('status') == 'running':
        return jsonify({"locked": True, "msg": "该图集正在下载中"})
    trigger_download(key)
    return jsonify({"status": "started"})

@app.route('/api/trigger-fetch', methods=['POST'])
def api_trigger_fetch():
    global _task_counter, _fetch_running
    if _fetch_running:
        return jsonify({"locked": True, "msg": "已有抓取任务在运行，请等待完成"})
    with _fetch_lock:
        if _fetch_running:
            return jsonify({"locked": True, "msg": "已有抓取任务在运行，请等待完成"})
        _fetch_running = True
    data = request.json or {}
    tid = str(_task_counter); _task_counter += 1
    _tasks[tid] = {'status': 'running', 'log': ''}
    if data.get('mode') == 'keywords':
        kws = data.get('keywords', []) or [{"keyword": data.get('keyword','AKB'), "max": data.get('max',5)}]
        py = sys.executable
        scripts_dir = os.path.join(os.path.dirname(__file__), '..', 'scripts')
        sub_env = {**os.environ, 'STORAGE_BACKEND': STORAGE_BACKEND, 'PATH': os.environ.get('PATH','')}
        scripts_dir_abs = os.path.abspath(scripts_dir)
        sub_env['PYTHONPATH'] = scripts_dir_abs + ':' + os.path.abspath(os.path.join(scripts_dir_abs, '..')) + ':' + sub_env.get('PYTHONPATH','')
        sub_env['PYTHONUNBUFFERED'] = '1'
        import json as _json
        cmd = [py, 'yahoo_news_auto_sqlite.py', '--keywords', _json.dumps(kws), '--push']
        def on_fetch_done():
            global _fetch_running
            with _fetch_lock: _fetch_running = False
        threading.Thread(target=_run_task, args=(cmd, tid, sub_env, on_fetch_done), daemon=True).start()
        return jsonify({"task_id": tid})
    else:
        cmd = [sys.executable, 'yahoo_recommendations_sqlite.py',
               '--max', str(data.get('max',10)), '--push']
    sub_env = {**os.environ, 'STORAGE_BACKEND': STORAGE_BACKEND}
    def on_fetch_done():
        global _fetch_running
        with _fetch_lock:
            _fetch_running = False
    threading.Thread(target=_run_task, args=(cmd, tid, sub_env, on_fetch_done), daemon=True).start()
    return jsonify({"task_id": tid})

@app.route('/api/trigger-publish', methods=['POST'])
def api_trigger_publish():
    global _task_counter, _publish_running
    if _publish_running:
        return jsonify({"locked": True, "msg": "已有发布任务在运行，请等待完成"})
    with _publish_lock:
        if _publish_running:
            return jsonify({"locked": True, "msg": "已有发布任务在运行，请等待完成"})
        _publish_running = True
    tid = str(_task_counter); _task_counter += 1
    _tasks[tid] = {'status': 'running', 'log': ''}
    cmd = [sys.executable, 'yahoo_news_publish.py', '--auto', '--force', '--reuse-existing-tab']
    post_time = (request.json or {}).get('post_time', '')
    if post_time:
        cmd += ['--post-time', post_time]
    sub_env = {**os.environ, 'STORAGE_BACKEND': STORAGE_BACKEND}
    def on_publish_done():
        global _publish_running
        with _publish_lock:
            _publish_running = False
    threading.Thread(target=_run_task, args=(cmd, tid, sub_env, on_publish_done), daemon=True).start()
    return jsonify({"task_id": tid})

@app.route('/api/task/<tid>')
def api_task(tid):
    t = _tasks.get(tid, 'unknown')
    if isinstance(t, dict):
        # Sanitize log to ensure valid JSON
        if 'log' in t and t['log']:
            t['log'] = t['log'].replace('\x00','').replace('\x1b','')
        return jsonify(t)
    return jsonify({"status": t, "log": ""})

@app.route('/api/task-logs')
def api_task_logs():
    today = _dt_ad2.now().strftime('%Y-%m-%d')
    log_path = os.path.join(TASK_LOG_DIR, f'task_{today}.log')
    if os.path.exists(log_path):
        with open(log_path) as f:
            return jsonify({"logs": f.read()[-20000:]})
    return jsonify({"logs": ""})

@app.route('/api/keywords')
def api_keywords():
    try:
        from yahoo_news_auto import DEFAULT_KEYWORDS
        kws = [{"keyword": kw, "max": mx, "china_filter": cf} for kw, mx, cf in DEFAULT_KEYWORDS]
    except Exception:
        kws = [{"keyword": "AKB", "max": 10, "china_filter": False}]
    return jsonify({"keywords": kws})

@app.route('/api/active-tasks')
def api_active_tasks():
    """返回所有运行中的任务"""
    active = []
    for tid, t in _tasks.items():
        if isinstance(t, dict) and t.get('status') == 'running':
            active.append({'task_id': tid, 'status': 'running', 'log': t.get('log', '')})
    return jsonify({"active": active, "fetch_running": _fetch_running, "publish_running": _publish_running})

@app.route('/api/regenerate/<key>', methods=['POST'])
def api_regenerate(key):
    global _regen_keys
    if key in _regen_keys:
        return jsonify({"locked": True, "msg": "该新闻正在重新生成中"})
    with _regen_lock:
        if key in _regen_keys: return jsonify({"locked": True, "msg": "该新闻正在重新生成中"})
        _regen_keys.add(key)
    from sqlite_db import get_by_key
    row = get_by_key(key)
    if not row:
        with _regen_lock: _regen_keys.discard(key)
        return jsonify({"error": "not found"}), 404
    def do_regenerate():
        _tasks['regen_'+key] = {'status': 'running', 'log': ''}
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
            from yahoo_common import translate_title, generate_content_and_comment, evaluate_quality, generate_video_caption
            import json as _json
            log = []
            log.append('翻译标题...')
            _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
            title_zh = translate_title(row.get('title_ja', row.get('title','')))
            log.append(f'标题: {title_zh[:50]}')
            _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
            log.append('生成内容...')
            _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
            gen = generate_content_and_comment(row.get('title_ja',''), title_zh, body_text=row.get('content',''))
            if gen:
                seo_title, summary, content, comment, _, topic_tags = gen
                log.append('生成短配文...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                video_caption = generate_video_caption(seo_title, summary, content, list(topic_tags) if topic_tags else [])
                log.append('评估质量...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                quality = evaluate_quality(seo_title, content, comment, row.get('title_ja',''), row.get('content',''))
                updates = {'title': seo_title, 'summary': summary, 'content': content, 'comment': comment,
                           'video_caption': video_caption,
                           'title_score': quality['title_score'], 'content_score': quality['content_score']}
                update_news(key, updates)
                if quality.get('scores'):
                    try:
                        from sqlite_db import upsert_scores
                        upsert_scores(key, quality['scores'])
                    except: pass
                ts = quality.get('title_score', 0)
                cs = quality.get('content_score', 0)
                log.append('✅ 完成 标题' + str(ts) + ' 内容' + str(cs))
            else:
                log.append('❌ LLM生成失败')
            _tasks['regen_'+key] = {'status': 'done', 'log': '\n'.join(log)}
            _save_task_log('regen_'+key, '\n'.join(log))
        except Exception as e:
            _tasks['regen_'+key] = {'status': f'error: {e}', 'log': '\n'.join(log) if log else ''}
            _save_task_log('regen_'+key, '\n'.join(log) + f'\nERROR: {e}' if log else str(e))
        finally:
            global _regen_keys
            with _regen_lock: _regen_keys.discard(key)
    threading.Thread(target=do_regenerate, daemon=True).start()
    return jsonify({"status": "started", "task_id": "regen_"+key})

@app.route('/api/archive-bulk', methods=['POST'])
def api_archive_bulk():
    data = request.json or {}
    keys = data.get('keys', [])
    if keys:
        for key in keys:
            update_news(key, {'status': 'archived'})
    return jsonify({"ok": True, "count": len(keys)})

@app.route('/api/gallery-upload/<key>', methods=['POST'])
def api_gallery_upload(key):
    data = request.json or {}
    selected = data.get('selected', [])
    urls = upload_selected(key, selected)
    return jsonify({"ok": True, "urls": urls})

@app.route('/api/gallery-status/<key>')
def api_gallery_status(key):
    return jsonify(gstatus(key))

@app.route('/local-image')
def local_image():
    """代理本地图片文件"""
    path = request.args.get('path', '')
    if not path or not os.path.exists(path):
        return '', 404
    return send_file(path)

INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>新闻管理</title>
<style>
:root{--bg:#f0f2f5;--card-bg:#fff;--text:#333;--text2:#888;--text3:#bbb;--border:#eef0f4;--red:#ff2442;--orange:#ff6b35;--shadow:0 1px 3px rgba(0,0,0,.06);--radius:10px}
*{margin:0;padding:0;box-sizing:border-box}
body{font:13px -apple-system,ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.page{padding:20px;max-width:1440px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
.card{background:var(--card-bg);border-radius:var(--radius);box-shadow:var(--shadow);padding:14px 20px}
.card-header{display:flex;align-items:center;gap:16px}
.card-header h1{font-size:18px;font-weight:700}
.stats{display:flex;gap:16px;font-size:12px;color:var(--text2)}
.stats b{color:var(--text)}
.btn{display:inline-flex;align-items:center;gap:4px;padding:6px 14px;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:500;transition:all .15s;white-space:nowrap;line-height:1.4}
.btn:hover{filter:brightness(.95)}
.btn:disabled{opacity:.4;pointer-events:none}
.btn-red{background:var(--red);color:#fff}
.btn-orange{background:var(--orange);color:#fff}
.btn-gray{background:#eef0f2;color:#555}
.btn-dark{background:#6b7280;color:#fff}
input,select,textarea{font:inherit;outline:none;transition:border-color .15s}
input:focus,select:focus,textarea:focus{border-color:var(--red)!important}
.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.toolbar-row{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:6px 0}
.toolbar-row+.toolbar-row{border-top:1px solid var(--border);padding-top:8px;margin-top:2px}
.toolbar-row .label{font-size:11px;font-weight:600;color:var(--text3);width:36px;flex-shrink:0}
.toolbar-row input[type=text]{width:80px;padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px}
.toolbar-row input[type=number]{width:44px;padding:5px 4px;border:1px solid #ddd;border-radius:5px;font-size:12px;text-align:center}
.toolbar-row input[type=date],.toolbar-row input[type=datetime-local]{padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px;width:130px}
.toolbar-row select{padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px;background:#fff}
.sep{width:1px;height:22px;background:var(--border);margin:0 4px;flex-shrink:0}
.table-wrap{overflow-x:auto}
.table{width:100%;border-collapse:collapse}
.table th{background:#f7f8fa;padding:10px 12px;text-align:left;font-size:11px;font-weight:600;color:var(--text2);border-bottom:1px solid var(--border);cursor:pointer;user-select:none;white-space:nowrap}
.table th:hover{color:var(--text)}
.table td{padding:8px 12px;border-bottom:1px solid var(--border);font-size:13px;vertical-align:middle}
.table tbody tr{transition:background .1s}
.table tbody tr:hover{background:#f5f7ff}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:500}
.badge-green{background:#e6f7e9;color:#1a7d2e}
.badge-gray{background:#f0f0f0;color:#888}
.badge-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0}
.badge-dot-active{background:#22c55e}
.badge-dot-archived{background:#bbb}
.score{display:inline-flex;align-items:center;justify-content:center;min-width:36px;padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600}
.score-hi{background:#dcfce7;color:#15803d}
.score-mid{background:#fef3c7;color:#a16207}
.score-lo{background:#fee2e2;color:#b91c1c}
.tag{display:inline-block;background:#eef2ff;color:#4f46e5;padding:2px 8px;border-radius:10px;font-size:11px;margin:1px 3px}
.link{color:var(--text);text-decoration:none}
.link:hover{color:var(--red)}
.pagination{display:flex;justify-content:center;gap:4px;padding:4px 0}
.pagination button{min-width:32px;height:30px}
.pagination button.current{background:var(--red);color:#fff}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:200;justify-content:center;align-items:center}
.modal.active{display:flex}
.modal-card{background:var(--card-bg);border-radius:12px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;padding:24px;box-shadow:0 8px 30px rgba(0,0,0,.15)}
.modal img.preview-img{max-width:100%;max-height:300px;border-radius:8px;margin-bottom:12px}
.modal h2{font-size:18px;margin-bottom:8px}
.modal .meta{color:var(--text2);font-size:12px;margin-bottom:12px}
.modal .section{margin:10px 0;padding:8px 0;border-top:1px solid var(--border)}
.modal .section h4{font-size:12px;color:var(--text2);margin-bottom:4px}
</style>
</head>
<body>
<div class="page">
  <!-- Header -->
  <div class="card card-header">
    <h1>📰 新闻管理</h1>
    <div class="stats" id="stats"></div>
    <span id="taskBar" style="display:none;font-size:12px;cursor:pointer;color:var(--orange);font-weight:600" onclick="showTaskModal()"></span>
    <div style="flex:1"></div>
    <button class="btn btn-dark btn-sm" onclick="location.reload()">🔄 刷新</button>
  </div>

  <!-- Fetch -->
  <div class="card">
    <h3 style="margin-bottom:10px">🔍 关键词抓取</h3>
    <div id="kwGrid" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px"></div>
    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap">
      <button class="btn btn-gray btn-sm" onclick="addKeyword()">+ 自定义</button>
      <button class="btn btn-gray btn-sm" onclick="resetKeywords()">重置为预置</button>
      <span style="flex:1"></span>
      <span style="font-size:11px;color:var(--text2)" id="kwSummary"></span>
      <button class="btn btn-red btn-sm" onclick="triggerFetch('keywords')" id="kwBtn">🔍 开始抓取</button>
    </div>
    <hr style="border:none;border-top:1px solid var(--border);margin:10px 0">
    <div style="display:flex;align-items:center;gap:8px">
      <h3 style="margin:0">📰 推荐抓取</h3>
      <span style="font-size:11px;color:var(--text2)">抓取 Yahoo 首页推荐流</span>
      <span style="flex:1"></span>
      <span style="font-size:11px;color:var(--text2)">条数</span>
      <input type="number" id="recomMax" value="10" min="1" max="50" style="width:50px;padding:5px 6px;border:1px solid #ddd;border-radius:5px;font-size:12px">
      <button class="btn btn-red btn-sm" onclick="triggerFetch('recom')" id="recomBtn">📰 开始抓取</button>
    </div>
  </div>

  <!-- Filters -->
  <div class="card">
    <div class="toolbar">
      <input type="text" id="search" placeholder="搜索标题/正文..." style="width:180px;padding:6px 10px;border:1px solid #ddd;border-radius:5px;font-size:12px">
      <div class="sep"></div>
      <input type="date" id="dateFrom" title="开始日期" style="padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px;width:130px">
      <input type="date" id="dateTo" title="结束日期" style="padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px;width:130px">
      <div class="sep"></div>
      <select id="category" style="padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px;background:#fff"><option value="">全部分类</option></select>
      <select id="status" style="padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px;background:#fff"><option value="active">活跃</option><option value="archived">已归档</option></select>
      <select id="publishXhs" style="padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px;background:#fff"><option value="">发布小红书</option><option value="published">已发布</option><option value="pending">待发布</option><option value="unpublished">未发布</option></select>
      <button class="btn btn-red" onclick="loadList()">筛选</button>
    </div>
  </div>

  <!-- Table -->
  <div class="card" style="padding:0;overflow:hidden">
    <div id="archiveBar" style="display:none;padding:10px 14px;border-bottom:1px solid var(--border);background:#fafbfc;justify-content:space-between;align-items:center">
      <span style="font-size:12px;color:var(--text2)" id="archiveCount">已选 0 条</span>
      <button class="btn btn-dark btn-sm" onclick="archiveSelected()">📦 归档选中</button>
    </div>
    <div id="publishBar" style="display:none;padding:10px 14px;border-bottom:1px solid var(--border);background:#fff7f5;justify-content:space-between;align-items:center">
      <span style="font-size:12px;color:var(--text2)"><b id="pendingCount">0</b> 条待发布</span>
      <div style="display:flex;gap:8px;align-items:center">
        <input type="datetime-local" id="postTime" title="定时发布" style="padding:4px 6px;border:1px solid #ddd;border-radius:5px;font-size:11px;width:130px">
        <button class="btn btn-orange btn-sm" onclick="triggerPublish()" id="pubBtn">📤 发布到小红书</button>
      </div>
    </div>
    <div class="table-wrap">
    <table class="table">
      <thead><tr>
        <th style="width:30px"><input type="checkbox" onclick="selectAllRows(this.checked)" title="全选"></th>
        <th style="width:36px">#</th>
        <th onclick="setSort('pub_time')" style="width:90px">新闻时间</th>
        <th onclick="setSort('created_at')" style="width:85px">入库时间</th>
        <th>标题</th>
        <th style="width:55px">发布XHS</th>
        <th style="width:85px">发布时间</th>
        <th style="width:52px">状态</th>
        <th>分类</th>
        <th onclick="setSort('title_score')" style="width:60px">评分</th>
        <th>标签</th>
      </tr></thead>
      <tbody id="tbody"></tbody>
    </table>
    </div>
    <div class="pagination" id="pager" style="padding:12px"></div>
  </div>
</div>

<!-- Preview modal -->
<div class="modal" id="modal" onclick="if(event.target===this)closeModal()"><div class="modal-card" id="modalContent"></div></div>

<!-- Terminal modal -->
<div class="modal" id="taskModal" onclick="if(event.target===this)closeTaskModal()">
  <div class="modal-card" style="max-width:750px;background:#1e1e1e;color:#0f0">
    <h3 id="taskModalTitle" style="color:#fff;margin-bottom:12px">🖥️ 终端</h3>
    <pre id="taskLog" style="font:12px Menlo,monospace;white-space:pre-wrap;min-height:300px;max-height:60vh;overflow-y:auto;margin:0">等待中...</pre>
    <div style="margin-top:12px;text-align:right"><button class="btn" style="background:#555;color:#fff" onclick="closeTaskModal()">关闭</button></div>
  </div>
</div>

<script>
let sortBy='created_at',sortDir='DESC',page=0;
let activeTaskId=null,activeTaskLabel='';
const S=id=>document.getElementById(id);

async function checkActiveTasks(){
  const r=await fetch('/api/active-tasks');const d=await r.json();
  if(d.active.length>0||d.fetch_running){
    S('taskBar').style.display='';S('taskBar').textContent='⏳ 抓取任务运行中...点击查看';
    activeTaskId=d.active.length>0?d.active[0].task_id:localStorage.getItem('lastTaskId');
    ['kwBtn','recomBtn'].forEach(id=>{S(id).disabled=true;S(id).style.opacity='0.5'});
    S('pubBtn').disabled=false;S('pubBtn').style.opacity='1';
  }else if(d.publish_running){
    S('taskBar').style.display='';S('taskBar').textContent='⏳ 发布任务运行中...点击查看';
    activeTaskId=localStorage.getItem('lastTaskId');
    S('pubBtn').disabled=true;S('pubBtn').style.opacity='0.5';
    ['kwBtn','recomBtn'].forEach(id=>{S(id).disabled=false;S(id).style.opacity='1'});
  }else{
    S('taskBar').style.display='none';activeTaskId=null;localStorage.removeItem('lastTaskId');
    ['kwBtn','recomBtn','pubBtn'].forEach(id=>{S(id).disabled=false;S(id).style.opacity='1'});
  }
}
function showTaskModal(){if(!activeTaskId)return;S('taskModal').classList.add('active');pollTaskLog(activeTaskId)}
async function pollTaskLog(tid){
  try{var sr=await fetch('/api/task/'+tid);var sd=await sr.json()}catch(e){setTimeout(()=>pollTaskLog(tid),3000);return}
  if(sd.log)S('taskLog').textContent=sd.log;
  if(sd.status==='running'){setTimeout(()=>pollTaskLog(tid),3000)}else{checkActiveTasks()}
}

async function loadList(){
  const p=new URLSearchParams({sort_by:sortBy,sort_dir:sortDir,limit:100,offset:page*100,
    search:S('search').value,date_from:S('dateFrom').value,date_to:S('dateTo').value,
    category:S('category').value,status:S('status').value,publish_xhs:S('publishXhs').value});
  const r=await fetch('/api/news?'+p);const d=await r.json();
  S('tbody').innerHTML=d.rows.map((n,i)=>`<tr>
    <td><input type="checkbox" class="rowSel" value="${n.key}" onclick="event.stopPropagation()" onchange="updateArchiveBar()"></td>
    <td style="color:var(--text3);font-size:11px">${page*100+i+1}</td>
    <td style="white-space:nowrap;font-size:12px;color:var(--text2)">${n.pub_time||''}</td>
    <td style="white-space:nowrap;font-size:11px;color:var(--text3)">${(n.created_at||'').substring(0,16)}</td>
    <td><a href="/detail/${n.key}" class="link" onclick="event.stopPropagation()">${n.fetch_by?`<span class="tag">${esc(n.fetch_by)}</span> `:''}<b>${esc(n.title||'')}</b></a><br>
        <span style="color:var(--text3);font-size:11px">${esc((n.content||'').substring(0,50))}</span></td>
    <td><input type="checkbox" ${n.publish_xhs?'checked':''} onchange="togglePublish('${n.key}',this.checked)" onclick="event.stopPropagation()"></td>
    <td style="font-size:11px;color:var(--text2)">${n.publish_time||'-'}</td>
    <td><span class="badge ${n.status==='archived'?'badge-gray':'badge-green'}"><span class="badge-dot ${n.status==='archived'?'badge-dot-archived':'badge-dot-active'}"></span>${n.status==='archived'?'归档':'活跃'}</span></td>
    <td>${n.category||'-'}</td>
    <td><span class="score ${n.title_score>3?'score-hi':n.title_score>1?'score-mid':'score-lo'}">${(n.title_score||0).toFixed(1)}</span></td>
    <td>${(n.tags||[]).slice(0,3).map(t=>`<span class="tag">${esc(t)}</span>`).join('')}</td>
  </tr>`).join('');
  S('stats').innerHTML=`<b>${d.total}</b> 条 · 今日 <b>${d.today}</b> · 待发 <b>${d.pending}</b> · 已发 <b>${d.published||0}</b>`;
  // Show/hide publish bar
  const pendingBar=document.getElementById('publishBar');
  if(d.pending>0){pendingBar.style.display='flex';document.getElementById('pendingCount').textContent=d.pending}
  else pendingBar.style.display='none';
  const totalPages=Math.ceil(d.total/100);let pager='';
  if(totalPages>1){
    pager+=`<button class="btn btn-gray" onclick="goPage(${page-1})" ${page<=0?'disabled':''}>‹</button>`;
    for(let i=0;i<totalPages;i++){
      if(i===page)pager+=`<button class="btn btn-red current">${i+1}</button>`;
      else pager+=`<button class="btn btn-gray" onclick="goPage(${i})">${i+1}</button>`;
    }
    pager+=`<button class="btn btn-gray" onclick="goPage(${page+1})" ${page>=totalPages-1?'disabled':''}>›</button>`;
  }
  S('pager').innerHTML=pager;
}
function goPage(n){page=n;loadList();window.scrollTo(0,0)}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function setSort(col){if(sortBy===col){sortDir=sortDir==='DESC'?'ASC':'DESC'}else{sortBy=col;sortDir='DESC'}loadList()}

async function preview(key){
  const r=await fetch('/api/news/'+key);const n=await r.json();
  let imgs='';
  if(n.image_url)imgs+=`<img class="preview-img" src="${esc(n.image_url)}">`;
  if(n.gallery_images){try{
    const g=typeof n.gallery_images==='string'?JSON.parse(n.gallery_images):n.gallery_images;
    g.forEach(p=>{imgs+=`<img class="preview-img" src="/local-image?path=${encodeURIComponent(p)}">`});
  }catch(e){}}
  S('modalContent').innerHTML=`
    ${imgs}
    <h2>${esc(n.title)}</h2>
    <div class="meta">${n.pub_time} | ${n.source} | ${n.category} | 📊标题${(n.title_score||0).toFixed(1)} 内容${(n.content_score||0).toFixed(1)}</div>
    ${n.summary?`<p style="color:#555;margin:8px 0">${esc(n.summary)}</p>`:''}
    <div class="section"><h4>新闻要点</h4><p>${esc(n.content||'').replace(/\\n/g,'<br>')}</p></div>
    <div class="section"><h4>我的解读</h4><p>${esc(n.comment||'').replace(/\\n/g,'<br>')}</p></div>
    ${n.video_caption?`<div class="section"><h4>🎬 短配文</h4><p>${esc(n.video_caption||'')}</p></div>`:''}
    <div class="section"><h4>标签</h4>${(n.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ')}</div>
    <div style="margin-top:16px"><a href="/detail/${n.key}" class="btn btn-red">编辑详情</a> <button class="btn btn-gray" onclick="closeModal()">关闭</button></div>`;
  S('modal').classList.add('active');
}
function closeModal(){S('modal').classList.remove('active')}
function closeTaskModal(){S('taskModal').classList.remove('active')}

// Keyword management
let keywords=[];
async function loadKeywords(){
  try{const r=await fetch('/api/keywords');const d=await r.json();keywords=d.keywords}catch(e){keywords=[]}
  renderKeywords();
}
function renderKeywords(){
  const grid=document.getElementById('kwGrid');
  grid.innerHTML=keywords.map((k,i)=>`<div class="kw-chip" style="display:flex;align-items:center;gap:4px;background:#fff;border:1px solid var(--border);border-radius:6px;padding:4px 8px;font-size:12px">
    <input type="checkbox" checked onchange="updateKwSummary()" style="width:14px;height:14px;accent-color:var(--red)">
    <input value="${esc(k.keyword)}" onchange="keywords[${i}].keyword=this.value" style="border:none;background:transparent;width:${Math.max(40,k.keyword.length*14)}px;font-size:12px;font-weight:500;outline:none;padding:2px">
    <span style="color:var(--text3)">×</span>
    <input type="number" value="${k.max}" min="1" max="50" onchange="keywords[${i}].max=parseInt(this.value)||5;updateKwSummary()" style="width:38px;padding:2px;border:1px solid #eee;border-radius:4px;font-size:11px;text-align:center">
    <span style="cursor:pointer;color:var(--text3);font-size:14px" onclick="deleteKeyword(${i})" title="删除">×</span>
  </div>`).join('');
  updateKwSummary();
}
function addKeyword(){keywords.push({keyword:'新词',max:5,china_filter:false});renderKeywords()}
function deleteKeyword(i){keywords.splice(i,1);renderKeywords()}
function resetKeywords(){loadKeywords()}
function updateKwSummary(){
  const chips=document.querySelectorAll('#kwGrid .kw-chip');
  let total=0,sel=0;
  chips.forEach(c=>{const cb=c.querySelector('input[type=checkbox]');const mx=c.querySelector('input[type=number]');if(cb.checked){sel++;total+=parseInt(mx.value)||5}});
  S('kwSummary').textContent=`已选 ${sel} 个 · 共 ${total} 条`;
}
loadKeywords();

async function runTask(opts){
  const {title, apiUrl, apiBody, btn, origText, onDone, taskLabel} = opts;
  btn.disabled=true;btn.style.opacity='0.6';btn.textContent='⏳ 运行中...';btn.style.background='var(--red)';btn.style.color='#fff';
  S('taskModalTitle').textContent=title;
  S('taskLog').textContent='⏳ 启动中...';
  S('taskModal').classList.add('active');
  var r=await fetch(apiUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(apiBody)});
  var d=await r.json();
  if(d.locked){S('taskLog').textContent='🔒 '+d.msg;btn.textContent=origText;btn.style.opacity='1';btn.style.background='';btn.style.color='';btn.disabled=false;return}
  var tid=d.task_id;
  activeTaskId=tid;localStorage.setItem('lastTaskId',tid);
  S('taskBar').style.display='';S('taskBar').textContent='⏳ '+taskLabel+'运行中...点击查看';
  var lastLen=0;
  for(var i=0;i<2400;i++){
    await new Promise(r=>setTimeout(r,3000));
    try{var sr=await fetch('/api/task/'+tid);var sd=await sr.json()}catch(e){continue}
    if(sd.log){
      if(sd.log.length>lastLen){S('taskLog').textContent=sd.log;S('taskLog').scrollTop=S('taskLog').scrollHeight;lastLen=sd.log.length}
    }
    if(sd.status==='done'){btn.textContent='✅ 完成';btn.style.opacity='1';btn.style.background='';btn.style.color='';activeTaskId=null;localStorage.removeItem('lastTaskId');S('taskBar').style.display='none';setTimeout(()=>{btn.disabled=false;btn.textContent=origText;if(onDone)onDone()},2000);return}
    if(sd.status&&sd.status.startsWith('error')){btn.textContent='❌ 失败';btn.style.opacity='1';btn.style.background='';btn.style.color='';btn.disabled=false;activeTaskId=null;localStorage.removeItem('lastTaskId');S('taskBar').style.display='none';return}
  }
  btn.textContent='⏰ 超时';btn.style.opacity='1';btn.style.background='';btn.style.color='';btn.disabled=false;activeTaskId=null;localStorage.removeItem('lastTaskId');S('taskBar').style.display='none';
}

function disableFetchBtns(){['kwBtn','recomBtn'].forEach(id=>{var b=S(id);if(b){b.disabled=true;b.style.opacity='0.5'}})}

async function triggerFetch(mode){
  var b=document.getElementById(mode==='keywords'?'kwBtn':'recomBtn');
  var orig=b.textContent;disableFetchBtns();
  var body={mode:mode};
  if(mode==='keywords'){
    var kws=[];document.querySelectorAll('#kwGrid .kw-chip').forEach(c=>{
      var cb=c.querySelector('input[type=checkbox]');if(!cb.checked)return;
      var ins=c.querySelectorAll('input');kws.push({keyword:ins[1].value,max:parseInt(ins[2].value)||5});
    });
    if(!kws.length){alert('请至少勾选一个关键词');b.textContent=orig;b.style.opacity='1';b.disabled=false;return}
    body.keywords=kws;
  }else{body.max=parseInt(document.getElementById('recomMax').value)||10}
  runTask({title:mode==='keywords'?'🔍 抓取关键词':'📰 推荐新闻',apiUrl:'/api/trigger-fetch',apiBody:body,btn:b,origText:orig,taskLabel:mode==='keywords'?'关键词抓取':'推荐抓取',onDone:()=>loadList()});
}

async function triggerPublish(){
  var b=document.getElementById('pubBtn');var orig=b.textContent;
  var pt=S('postTime').value;pt=pt?pt.replace('T',' '):'';
  runTask({title:'📤 发布到小红书',apiUrl:'/api/trigger-publish',apiBody:{post_time:pt},btn:b,origText:orig,taskLabel:'发布',onDone:null});
}
async function togglePublish(key,val){
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_xhs:val?1:0})});
  loadList();
}
function selectAllRows(val){document.querySelectorAll('.rowSel').forEach(cb=>{cb.checked=val});updateArchiveBar()}
function updateArchiveBar(){
  const n=document.querySelectorAll('.rowSel:checked').length;
  const bar=document.getElementById('archiveBar');
  if(n>0){bar.style.display='flex';document.getElementById('archiveCount').textContent='已选 '+n+' 条'}
  else bar.style.display='none';
}
async function archiveSelected(){
  var keys=[];document.querySelectorAll('.rowSel:checked').forEach(cb=>{keys.push(cb.value)});
  if(!keys.length){alert('请先勾选新闻');return}
  if(!confirm('确定归档 '+keys.length+' 条新闻？'))return;
  await fetch('/api/archive-bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keys:keys})});
  document.getElementById('archiveBar').style.display='none';
  loadList();
}
async function loadCategories(){
  const cats=[...new Set((await(await fetch('/api/news?limit=500')).json()).rows.map(r=>r.category).filter(Boolean))];
  S('category').innerHTML='<option value="">全部分类</option>'+cats.map(c=>`<option>${esc(c)}</option>`).join('');
}
loadList();loadCategories();checkActiveTasks();
</script>
</body></html>"""

DETAIL_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{news.title}}</title>
<style>
:root{--bg:#f0f2f5;--card-bg:#fff;--text:#333;--text2:#888;--text3:#bbb;--border:#eef0f4;--red:#ff2442;--orange:#ff6b35;--shadow:0 1px 3px rgba(0,0,0,.06);--radius:10px}
*{margin:0;padding:0;box-sizing:border-box}
body{font:13px -apple-system,ui-sans-serif,system-ui,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.topbar{background:var(--card-bg);padding:10px 24px;display:flex;align-items:center;gap:12px;box-shadow:var(--shadow);position:sticky;top:0;z-index:100}
.topbar a{color:var(--red);text-decoration:none;font-size:13px;font-weight:500;flex-shrink:0}
.topbar a:hover{opacity:.8}
.topbar .title{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}
.page-detail{padding:20px;max-width:900px;margin:0 auto;display:flex;flex-direction:column;gap:12px}
.card{background:var(--card-bg);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px 22px}
.card h3{font-size:14px;font-weight:600;margin-bottom:10px;color:var(--text);display:flex;align-items:center;gap:6px}
.btn{display:inline-flex;align-items:center;gap:4px;padding:6px 14px;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:500;transition:all .15s;white-space:nowrap;line-height:1.4;text-decoration:none}
.btn:hover{filter:brightness(.95)}
.btn:disabled{opacity:.4;pointer-events:none}
.btn-red{background:var(--red);color:#fff}
.btn-orange{background:var(--orange);color:#fff}
.btn-gray{background:#eef0f2;color:#555}
.btn-sm{padding:3px 10px;font-size:11px}
.meta-grid{display:flex;flex-wrap:wrap;gap:6px 16px;margin-bottom:10px}
.meta-item{font-size:12px;color:var(--text2);display:flex;align-items:center;gap:4px}
.meta-item b{color:var(--text)}
.badge{display:inline-flex;align-items:center;gap:4px;padding:2px 10px;border-radius:10px;font-size:11px;font-weight:500}
.badge-green{background:#e6f7e9;color:#1a7d2e}
.badge-gray{background:#f0f0f0;color:#888}
.inline-input,.inline-textarea{border:none;border-bottom:2px dashed transparent;background:transparent;padding:6px 0;font:inherit;width:100%;outline:none;transition:border-color .15s;border-radius:0}
.inline-input:hover,.inline-textarea:hover{border-bottom-color:#ddd}
.inline-input:focus,.inline-textarea:focus{border-bottom-color:var(--red);border-bottom-style:solid}
.inline-textarea{resize:vertical;min-height:100px}
.auto-resize{resize:none;overflow:hidden;transition:height .1s}
.inline-textarea:focus{border:1px solid var(--red);border-radius:6px;padding:8px}
.field-group{display:flex;flex-direction:column;gap:10px}
.field-row{display:flex;align-items:center;gap:12px}
.field-row-ta{align-items:flex-start}
.field-row-ta label{padding-top:7px}
.field-row label{font-size:12px;color:var(--text2);width:68px;flex-shrink:0;text-align:right}
.field-row .value{flex:1;position:relative}
.cover-img{max-width:100%;max-height:360px;border-radius:8px;object-fit:cover}
.url-input{width:100%;padding:5px 8px;border:1px solid #eee;border-radius:5px;font-size:11px;color:var(--text2);background:#fafafa;cursor:text}
.img-strip{display:flex;gap:8px;overflow-x:auto;padding:4px 0}
.img-strip .img-item{position:relative;flex-shrink:0;cursor:pointer;border-radius:6px;overflow:hidden;transition:opacity .15s}
.img-strip .img-item img{height:130px;border-radius:6px;display:block}
.img-strip .img-item .chk{position:absolute;top:6px;left:6px;width:20px;height:20px;accent-color:var(--red);cursor:pointer}
.score-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:4px}
.score-item{text-align:center;padding:4px 6px;border-radius:5px;font-size:11px;font-weight:500}
.score-plus{background:#dcfce7;color:#15803d}
.score-minus{background:#fee2e2;color:#b91c1c}
.tag-row{display:flex;flex-wrap:wrap;align-items:center;gap:4px;min-height:34px;padding:6px 8px;border:1px solid var(--border);border-radius:6px}
.tag-bubble{display:inline-flex;align-items:center;background:#eef2ff;color:#4f46e5;padding:3px 10px;border-radius:10px;font-size:11px;gap:6px}
.tag-bubble .del{cursor:pointer;opacity:.5;font-weight:bold}
.tag-bubble .del:hover{opacity:1}
.tag-input{border:none;background:transparent;padding:3px 6px;font-size:11px;width:70px;outline:none}
.selects-row{display:flex;gap:16px;align-items:center}
.selects-row label{font-size:12px;color:var(--text2);margin-right:4px}
.selects-row select{padding:5px 8px;border:1px solid #ddd;border-radius:5px;font-size:12px;background:#fff}
.actions{display:flex;gap:8px;flex-wrap:wrap}
.toast{position:fixed;top:20px;right:20px;background:#22c55e;color:#fff;padding:12px 20px;border-radius:8px;display:none;z-index:999;font-weight:500;font-size:13px}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:200;justify-content:center;align-items:center}
.modal.active{display:flex}
.modal-card{background:var(--card-bg);border-radius:12px;max-width:700px;width:90%;max-height:80vh;overflow-y:auto;padding:24px;box-shadow:0 8px 30px rgba(0,0,0,.15)}
.sep-line{border:none;border-top:1px solid var(--border);margin:10px 0}
</style>
</head>
<body>
<div class="topbar">
  <a href="/">← 返回</a>
  {% if news.fetch_by %}<span class="badge badge-gray">{{news.fetch_by}}</span>{% endif %}
  <span class="title">{{news.title}}</span>
  <button class="btn btn-gray" id="regenBtn" onclick="regenerateContent()">🔄 重新生成</button>
  <button class="btn btn-red" id="saveBtn">💾 保存修改</button>
</div>

<div class="page-detail">

  <div class="card">
    {% if news.image_url %}<img src="{{ '/local-image?path=' + news.image_url if news.image_url.startswith('/') else news.image_url }}" class="cover-img" style="margin-bottom:10px">{% endif %}
    <div class="meta-grid">
      <span class="meta-item">来源 <b>{{news.source or '-'}}</b></span>
      <span class="meta-item">新闻时间 <b>{{news.pub_time or '-'}}</b></span>
      <span class="meta-item">入库 <b>{{news.created_at[:16] if news.created_at else '-'}}</b></span>
      {% if scores %}
      <span class="meta-item">📊 标题 <b>{{"%.1f"|format(news.title_score or 0)}}</b> · 内容 <b>{{"%.1f"|format(news.content_score or 0)}}</b></span>
      {% endif %}
    </div>
    <div style="display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:6px">
      <span style="font-size:12px;color:var(--text2)">分类</span>
      <input class="inline-input" name="category" value="{{news.category or ''}}" style="max-width:120px">
      <span style="color:var(--border)">|</span>
      <span style="font-size:12px;color:var(--text2)">发布XHS</span>
      <select name="publish_xhs" onchange="autoSaveField('publish_xhs',this.value)" style="padding:4px 6px;border:1px solid #ddd;border-radius:5px;font-size:12px">
        <option value="0" {{'selected' if not news.publish_xhs else ''}}>否</option>
        <option value="1" {{'selected' if news.publish_xhs else ''}}>是</option>
      </select>
      <span style="color:var(--border)">|</span>
      <span style="font-size:12px;color:var(--text2)">状态</span>
      <select name="status" onchange="autoSaveField('status',this.value)" style="padding:4px 6px;border:1px solid #ddd;border-radius:5px;font-size:12px">
        <option value="active" {{'selected' if news.status=='active' else ''}}>活跃</option>
        <option value="archived" {{'selected' if news.status=='archived' else ''}}>已归档</option>
      </select>
    </div>
    {% if news.publish_time %}<div class="meta-item" style="margin-bottom:6px">发布XHS时间 <b>{{news.publish_time}}</b> <span style="cursor:pointer;color:var(--red);font-size:11px" onclick="autoSaveField('publish_time','');location.reload()">[清除]</span></div>{% endif %}
    <div class="field-row" style="margin-bottom:8px"><label>标签</label><div class="value"><div class="tag-row" id="tagBubbles"></div></div></div>
    <hr class="sep-line">
    <div class="field-row" style="margin-bottom:3px"><label>原文</label><div class="value"><a href="{{news.link or ''}}" target="_blank" style="font-size:11px;color:var(--text2);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block" title="{{news.link or ''}}">{{news.link or '-'}}</a></div></div>
    <div class="field-row" style="margin-bottom:3px"><label>封面</label><div class="value"><input class="url-input" name="image_url" value="{{news.image_url or ''}}" onclick="this.select()"></div></div>
    {% if news.original_image_url and news.original_image_url != news.image_url %}
    <div class="field-row" style="margin-bottom:3px"><label>原图</label><div class="value"><a href="{{news.original_image_url or ''}}" target="_blank" style="font-size:11px;color:var(--text2);text-decoration:none;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block" title="{{news.original_image_url or ''}}">{{news.original_image_url or '-'}}</a></div></div>
    {% endif %}
    <div class="field-row"><label>图集</label><div class="value"><input class="url-input" name="gallery_url" value="{{news.gallery_url or ''}}" placeholder="https://..." onclick="this.select()"></div></div>
  </div>

  <div class="card">
    <h3>📸 图集 <span style="font-weight:400;font-size:12px;color:var(--text2)">— 下载并选择发布图片</span></h3>
    <div class="actions" style="margin-bottom:8px">
      <button class="btn btn-gray btn-sm" onclick="downloadGallery()" id="galleryBtn">📥 下载图集</button>
      <button class="btn btn-gray btn-sm" id="manageGalleryBtn" onclick="toggleGalleryModal()" {% if not news.gallery_images and not news.cached_images %}style="display:none"{% endif %}>🖼️ 管理图集</button>
    </div>
    <pre id="galleryLog" style="display:none;margin-bottom:8px;padding:10px;background:#1e1e1e;color:#0f0;border-radius:6px;font-size:11px;max-height:200px;overflow-y:auto;white-space:pre-wrap;font-family:Menlo,monospace"></pre>
    {% if news.gallery_images %}
    <div class="img-strip" id="publishImgStrip">
      {% for p in news.gallery_images %}
      <div class="img-item" onclick="togglePublishImg(this)" style="display:flex;flex-direction:column;align-items:center">
        <img src="/local-image?path={{p}}">
        <input type="checkbox" class="chk" data-path="{{p}}" onclick="event.stopPropagation()">
        <button class="btn btn-gray" style="font-size:9px;padding:1px 6px;position:absolute;bottom:2px;right:2px" onclick="event.stopPropagation();setAsCover('{{p}}')" title="设为封面">📷</button>
      </div>
      {% endfor %}
    </div>
    {% endif %}
  </div>

  {% if scores %}
  <div class="card">
    <h3 style="margin-bottom:8px">📊 评分明细</h3>
    <div style="display:flex;gap:8px;margin-bottom:10px">
      <button class="btn btn-red btn-sm" id="tabTitle" onclick="switchScoreTab('title')">标题评分 {{"%.1f"|format(news.title_score or 0)}}</button>
      <button class="btn btn-gray btn-sm" id="tabContent" onclick="switchScoreTab('content')">内容评分 {{"%.1f"|format(news.content_score or 0)}}</button>
    </div>
    <div class="score-grid" id="scoreGrid"></div>
  </div>
  {% endif %}

  <div class="card">
    <h3>✏️ 内容编辑</h3>
    <div class="field-group">
      <div class="field-row"><label>标题</label><div class="value">
        <input class="inline-input" name="title" id="titleInput" value="{{news.title}}" oninput="updateTitleCount()" style="padding-right:50px">
        <span id="titleCount" style="position:absolute;right:8px;top:6px;font-size:11px;color:var(--text3)"></span>
      </div></div>
      <div class="field-row field-row-ta"><label>🎬 短配文</label><div class="value"><textarea class="inline-textarea auto-resize" name="video_caption" style="min-height:40px">{{news.video_caption or ''}}</textarea></div></div>
      <div class="field-row"><label>引流摘要</label><div class="value"><input class="inline-input" name="summary" value="{{news.summary or ''}}"></div></div>
      <hr class="sep-line">
      <div class="field-row field-row-ta"><label>新闻要点</label><div class="value"><textarea class="inline-textarea auto-resize" name="content" style="min-height:120px">{{news.content or ''}}</textarea></div></div>
      <div class="field-row field-row-ta"><label>我的解读</label><div class="value"><textarea class="inline-textarea auto-resize" name="comment" style="min-height:120px">{{news.comment or ''}}</textarea></div></div>
    </div>
  </div>

  {% if news.title_ja or news.content_ja %}
  <div class="card">
    <h3 style="margin-bottom:8px">📰 原文</h3>
    {% if news.title_ja %}<p style="font-size:11px;color:var(--text3);margin-bottom:2px">日文标题</p><p style="font-size:13px;font-weight:600;margin-bottom:8px;color:var(--text)">{{news.title_ja}}</p>{% endif %}
    {% if news.content_ja %}<p style="font-size:11px;color:var(--text3);margin-bottom:2px">日文摘要</p><p style="font-size:12px;color:var(--text2);white-space:pre-wrap;line-height:1.6">{{news.content_ja[:2000]}}</p>{% endif %}
  </div>
  {% endif %}

</div>

<div class="toast" id="toast">已保存</div>

<div class="modal" id="taskModal" onclick="if(event.target===this)closeTaskModal()">
  <div class="modal-card" style="max-width:750px;background:#1e1e1e;color:#0f0">
    <h3 id="taskModalTitle" style="color:#fff;margin-bottom:12px">🖥️ 终端</h3>
    <pre id="taskLog" style="font:12px Menlo,monospace;white-space:pre-wrap;min-height:200px;max-height:50vh;overflow-y:auto;margin:0">等待中...</pre>
    <div style="margin-top:12px;text-align:right"><button class="btn" style="background:#555;color:#fff" onclick="closeTaskModal()">关闭</button></div>
  </div>
</div>
<script>function closeTaskModal(){document.getElementById('taskModal').classList.remove('active')}</script>

<div class="modal" id="galleryModal" onclick="if(event.target===this)closeGalleryModal()">
  <div class="modal-card" style="max-width:800px">
    <h3>📸 选择要保留的图片</h3>
    <div id="galleryGrid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:12px 0;max-height:60vh;overflow-y:auto"></div>
    <div class="actions" style="justify-content:flex-end">
      <button class="btn btn-gray" onclick="selectAllGallery(true)">全选</button>
      <button class="btn btn-gray" onclick="selectAllGallery(false)">取消全选</button>
      <button class="btn btn-red" onclick="saveGallery()">💾 保存</button>
      <button class="btn btn-orange" onclick="saveAndUpload()">☁️ 保存并上传</button>
      <button class="btn btn-gray" onclick="closeGalleryModal()">关闭</button>
    </div>
  </div>
</div>

<script>
const key='{{news.key}}';
{% if scores %}
const scoreData={{scores|tojson}};
const titleCols=['剧情感','冲突感','猎奇感','用户共鸣','名人','热点','简单通知','震惊体','概括全部'];
const contentCols=['原创度','趣味性','有用信息','对立信息','视频','离题','啰嗦重复','主动讨赏','生活照','搞怪照','宣传照','写真','中年男照','负面情绪'];
const titlePlus=['剧情感','冲突感','猎奇感','用户共鸣','名人','热点'];
const contentPlus=['原创度','趣味性','有用信息','对立信息','视频'];
function renderScoreGrid(cols){
  let html='';
  cols.forEach(c=>{
    const isPlus=titlePlus.includes(c)||contentPlus.includes(c);
    html+=`<div class="score-item ${isPlus?'score-plus':'score-minus'}">${c}: ${scoreData[c]||0}</div>`;
  });
  document.getElementById('scoreGrid').innerHTML=html;
}
function switchScoreTab(tab){
  document.getElementById('tabTitle').className=tab==='title'?'btn btn-red btn-sm':'btn btn-gray btn-sm';
  document.getElementById('tabContent').className=tab==='content'?'btn btn-red btn-sm':'btn btn-gray btn-sm';
  renderScoreGrid(tab==='title'?titleCols:contentCols);
}
switchScoreTab('title');
{% endif %}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
async function autoSaveField(field,val){
  var data={};data[field]=field==='publish_xhs'?parseInt(val):val;
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  var t=document.getElementById('toast');t.textContent='已保存';t.style.display='block';setTimeout(()=>t.style.display='none',1000);
}
async function runTask(opts){
  const {title, apiUrl, apiBody, btn, origText, onDone} = opts;
  btn.disabled=true;btn.style.opacity='0.6';btn.textContent='⏳ 运行中...';btn.style.background='var(--red)';btn.style.color='#fff';
  document.getElementById('taskModalTitle').textContent=title;
  document.getElementById('taskLog').textContent='⏳ 启动中...';
  document.getElementById('taskModal').classList.add('active');
  var r=await fetch(apiUrl,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(apiBody)});
  var d=await r.json();
  if(d.locked){document.getElementById('taskLog').textContent='🔒 '+d.msg;btn.textContent=origText;btn.style.opacity='1';btn.style.background='';btn.style.color='';btn.disabled=false;return}
  var tid=d.task_id,lastLen=0;
  for(var i=0;i<60;i++){
    await new Promise(r=>setTimeout(r,2000));
    try{var sr=await fetch('/api/task/'+tid);var sd=await sr.json()}catch(e){continue}
    if(sd.log&&sd.log.length>lastLen){document.getElementById('taskLog').textContent=sd.log;lastLen=sd.log.length}
    if(sd.status==='done'){btn.textContent='✅ 完成';btn.style.opacity='1';btn.style.background='';btn.style.color='';setTimeout(()=>{if(onDone)onDone()},1000);return}
    if(sd.status&&sd.status.startsWith('error')){btn.textContent='❌ 失败';btn.style.opacity='1';btn.style.background='';btn.style.color='';btn.disabled=false;return}
  }
  btn.textContent='⏰ 超时';btn.style.opacity='1';btn.style.background='';btn.style.color='';btn.disabled=false;
}
async function regenerateContent(){
  if(!confirm('重新生成会覆盖当前标题和内容，确定？'))return;
  var btn=document.getElementById('regenBtn'),orig=btn.textContent;
  runTask({title:'🔄 重新生成',apiUrl:'/api/regenerate/'+key,apiBody:{},btn:btn,origText:orig,onDone:()=>location.reload()});
}
// Check if regen is already running on page load
(async function checkRegenRunning(){
  var r=await fetch('/api/task/regen_'+key);var d=await r.json();
  if(d.status==='running'){
    var btn=document.getElementById('regenBtn');
    btn.disabled=true;btn.style.opacity='0.6';btn.style.background='var(--red)';btn.style.color='#fff';
    btn.textContent='⏳ 运行中...';
    document.getElementById('taskModalTitle').textContent='🔄 重新生成';
    document.getElementById('taskLog').textContent=d.log||'⏳ 运行中...';
    document.getElementById('taskModal').classList.add('active');
    // Keep polling
    var tid='regen_'+key,lastLen=(d.log||'').length;
    for(var i=0;i<60;i++){
      await new Promise(r=>setTimeout(r,2000));
      try{var sr=await fetch('/api/task/'+tid);var sd=await sr.json()}catch(e){continue}
      if(sd.log&&sd.log.length>lastLen){document.getElementById('taskLog').textContent=sd.log;lastLen=sd.log.length}
      if(sd.status==='done'){btn.textContent='✅ 完成';btn.style.background='';btn.style.color='';btn.style.opacity='1';setTimeout(()=>location.reload(),1000);return}
      if(sd.status&&sd.status.startsWith('error')){btn.textContent='❌ 失败';btn.style.background='';btn.style.color='';btn.style.opacity='1';btn.disabled=false;return}
    }
  }
})();
async function setAsCover(path){
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_url:path})});
  location.reload();
}

let tags={% if news.tags %}{{news.tags|tojson}}{% else %}[]{% endif %};
function renderTags(){
  const el=document.getElementById('tagBubbles');
  el.innerHTML=tags.map((t,i)=>`<span class="tag-bubble">${esc(t)}<span class="del" onclick="delTag(${i})">×</span></span>`).join('')
    +'<input class="tag-input" id="tagInput" placeholder="+添加" onkeydown="addTag(event)">';
}
function delTag(i){tags.splice(i,1);renderTags()}
function addTag(e){
  if(e.key==='Enter'||e.key===','){
    e.preventDefault();const v=e.target.value.trim().replace(/,$/,'');
    if(v){tags.push(v);e.target.value='';renderTags()}
  }
}
renderTags();
function xhsCharCount(s){
  // XHS: 字符显示宽度计数。全角(中日韩/假名/全角标点)=2,半角(ASCII/数字)=1,总数/2
  var w=0;
  for(var i=0;i<s.length;i++){
    var c=s.charCodeAt(i);
    if(c>=0xd800&&c<=0xdfff){w+=4;i++;continue} // surrogate emoji
    if(c<=0x7f)w+=1;       // ASCII
    else if(c<=0x7ff)w+=2; // Latin supplement etc
    else w+=2;             // CJK, kana, fullwidth - all width 2
  }
  return Math.ceil(w/2);
}
function updateTitleCount(){
  var el=document.getElementById('titleInput'),c=document.getElementById('titleCount');
  if(!el||!c)return;
  var n=xhsCharCount(el.value);
  c.textContent=n+'/20';
  c.style.color=n>20?'var(--red)':'var(--text3)';
}
updateTitleCount();
function autoGrow(el){el.style.height='auto';el.style.height=(el.scrollHeight+2)+'px'}
document.querySelectorAll('.auto-resize').forEach(function(ta){
  ta.addEventListener('input',function(){autoGrow(this)});
  autoGrow(ta);
});

document.getElementById('saveBtn').addEventListener('click',async()=>{
  const data={};
  ['title','summary','content','comment','category','video_caption','gallery_url','image_url'].forEach(k=>{data[k]=document.querySelector('[name='+k+']').value});
  data.tags=tags;
  data.publish_xhs=parseInt(document.querySelector('[name=publish_xhs]').value);
  data.status=document.querySelector('[name=status]').value;
  var pubPaths=[];document.querySelectorAll('#publishImgStrip input[type=checkbox]:checked').forEach(function(cb){pubPaths.push(cb.dataset.path)});
  if(pubPaths.length)data.publish_images=pubPaths;
  const r=await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r.ok){const t=document.getElementById('toast');t.style.display='block';setTimeout(()=>t.style.display='none',1500)}
});

var galleryImages=[];
(function initGallery(){
  {% if news.gallery_images %}var saved={{news.gallery_images|tojson}};{% else %}var saved=[];{% endif %}
  var savedSet=new Set(saved);
  saved.forEach(function(p){galleryImages.push({path:p,sel:true})});
  {% if news.cached_images %}var cached={{news.cached_images|tojson}};
  cached.forEach(function(p){if(!savedSet.has(p))galleryImages.push({path:p,sel:false})});
  {% endif %}
  var hasCache={% if news.cached_images %}cached.length{% else %}0{% endif %};
  if(hasCache>0||galleryImages.length>0){
    document.getElementById('galleryBtn').textContent='\u{1f504} 重新下载';
    var mb=document.getElementById('manageGalleryBtn');if(mb)mb.style.display='';
  }
})();
var galleryRunning=false;
async function downloadGallery(){
  if(galleryRunning){return}
  const btn=document.getElementById('galleryBtn');galleryRunning=true;
  btn.disabled=true;btn.textContent='⏳ 下载中...';btn.style.background='var(--red)';btn.style.color='#fff';btn.style.opacity='0.6';
  document.getElementById('taskModalTitle').textContent='📸 下载图集';
  document.getElementById('taskLog').textContent='⏳ 启动中...';
  document.getElementById('taskModal').classList.add('active');
  const log=document.getElementById('galleryLog');log.style.display='block';
  var r=await fetch('/api/gallery-download/'+key,{method:'POST'});
  var d=await r.json();
  if(d.locked){document.getElementById('taskLog').textContent='🔒 '+d.msg;resetGalleryBtn();return}
  for(var i=0;i<120;i++){
    await new Promise(r=>setTimeout(r,2000));
    var sr=await fetch('/api/gallery-status/'+key);var sd=await sr.json();
    if(sd.log){document.getElementById('taskLog').textContent=sd.log;log.textContent=sd.log;log.scrollTop=log.scrollHeight}
    if(sd.status==='done'){
      sd.images.forEach(function(p){galleryImages.push({path:p,sel:false})});
      resetGalleryBtn();btn.textContent='\u{1f504} 重新下载';
      // Show manage gallery button
      var mgmtBtn=document.getElementById('manageGalleryBtn');
      if(!mgmtBtn){
        mgmtBtn=document.createElement('button');
        mgmtBtn.id='manageGalleryBtn';mgmtBtn.className='btn btn-gray btn-sm';
        mgmtBtn.textContent='\u{1f5bc} 管理图集';
        mgmtBtn.onclick=toggleGalleryModal;
        btn.parentNode.insertBefore(mgmtBtn,btn.nextSibling);
      }
      document.getElementById('taskLog').textContent+='\n✅ 完成 ('+sd.images.length+'张)';
      log.textContent+='\n✅ 完成 ('+sd.images.length+'张)';
      setTimeout(function(){showGalleryModal();log.style.display='none'},1500);
      return;
    }
    if(sd.status&&sd.status.toString().startsWith('error')){document.getElementById('taskLog').textContent+='\n❌ '+sd.status;log.textContent+='\n❌ '+sd.status;resetGalleryBtn();return}
  }
  log.textContent+='\n⏰ 超时';resetGalleryBtn();
}
function resetGalleryBtn(){
  galleryRunning=false;var b=document.getElementById('galleryBtn');
  b.disabled=false;b.style.background='';b.style.color='';b.style.opacity='1';
}
// Check if gallery download is running on page load
(function checkGalleryRunning(){
  fetch('/api/gallery-status/'+key).then(r=>r.json()).then(function(d){
    if(d.status==='running'){
      galleryRunning=true;var b=document.getElementById('galleryBtn');
      b.disabled=true;b.textContent='⏳ 下载中...';b.style.background='var(--red)';b.style.color='#fff';b.style.opacity='0.6';
      document.getElementById('taskModalTitle').textContent='📸 下载图集';
      document.getElementById('taskLog').textContent=d.log||'⏳ 运行中...';
      document.getElementById('taskModal').classList.add('active');
      document.getElementById('galleryLog').style.display='block';
      // Keep polling
      var tid=setInterval(async function(){
        var sr=await fetch('/api/gallery-status/'+key);var sd=await sr.json();
        if(sd.log){document.getElementById('taskLog').textContent=sd.log;document.getElementById('galleryLog').textContent=sd.log}
        if(sd.status==='done'){
          clearInterval(tid);resetGalleryBtn();b.textContent='\u{1f504} 重新下载';
          sd.images.forEach(function(p){galleryImages.push({path:p,sel:false})});
          // Show manage gallery button
          var mgmtBtn=document.getElementById('manageGalleryBtn');
          if(!mgmtBtn){
            mgmtBtn=document.createElement('button');mgmtBtn.id='manageGalleryBtn';
            mgmtBtn.className='btn btn-gray btn-sm';mgmtBtn.textContent='\u{1f5bc} 管理图集';
            mgmtBtn.onclick=toggleGalleryModal;b.parentNode.insertBefore(mgmtBtn,b.nextSibling);
          }
          document.getElementById('taskLog').textContent+='\n✅ 完成 ('+sd.images.length+'张)';
        }
        if(sd.status&&sd.status.toString().startsWith('error')){clearInterval(tid);resetGalleryBtn()}
      },3000);
    }
  });
})();
function toggleGalleryModal(){var m=document.getElementById('galleryModal');if(m.classList.contains('active'))closeGalleryModal();else showGalleryModal()}
function showGalleryModal(){
  const grid=document.getElementById('galleryGrid');
  grid.innerHTML=galleryImages.map(function(p,i){return `<div style="position:relative;cursor:pointer" onclick="toggleGalleryImg(${i})">
    <img src="/local-image?path=${encodeURIComponent(p.path)}" style="width:100%;height:120px;object-fit:cover;border-radius:6px;border:3px solid ${p.sel?'#4CAF50':'#ddd'}">
    <input type="checkbox" ${p.sel?'checked':''} style="position:absolute;top:4px;right:4px;pointer-events:none">
  </div>`}).join('');
  document.getElementById('galleryModal').classList.add('active');
}
function toggleGalleryImg(i){galleryImages[i].sel=!galleryImages[i].sel;showGalleryModal()}
function selectAllGallery(val){galleryImages.forEach(function(p){p.sel=val});showGalleryModal()}
async function saveGallery(){
  const selected=galleryImages.filter(function(p){return p.sel}).map(function(p){return p.path});
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_images:selected})});
  closeGalleryModal();location.reload();
}
async function saveAndUpload(){
  const selected=galleryImages.filter(function(p){return p.sel}).map(function(p){return p.path});
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_images:selected})});
  await fetch('/api/gallery-upload/'+key,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected:selected})});
  alert('已上传 '+selected.length+' 张');closeGalleryModal();location.reload();
}
function closeGalleryModal(){document.getElementById('galleryModal').classList.remove('active')}
function togglePublishImg(el){var cb=el.querySelector('input[type=checkbox]');cb.checked=!cb.checked;el.style.opacity=cb.checked?'1':'0.4';savePublishImages()}
async function savePublishImages(){
  var paths=[];document.querySelectorAll('#publishImgStrip input[type=checkbox]:checked').forEach(function(cb){paths.push(cb.dataset.path)});
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_images:paths})});
}
(function initPublishCheckboxes(){
  {% if news.publish_images %}var pubSet=new Set({{news.publish_images|tojson}});{% else %}var pubSet=new Set();{% endif %}
  document.querySelectorAll('#publishImgStrip input[type=checkbox]').forEach(function(cb){
    if(pubSet.has(cb.dataset.path)){cb.checked=true}else{cb.parentElement.style.opacity='0.4'}
  });
})();
</script>
</body></html>"""

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/detail/<key>')
def detail(key):
    from flask import render_template_string as rts
    import json, glob as _glob
    news = get_by_key(key)
    if not news:
        return "Not found", 404
    # Parse gallery_images / publish_images JSON
    gi = news.get('gallery_images', '')
    news['gallery_images'] = json.loads(gi) if isinstance(gi, str) and gi else (gi or [])
    pi = news.get('publish_images', '')
    news['publish_images'] = json.loads(pi) if isinstance(pi, str) and pi else (pi or [])
    # Scan cache for extra images (use config's GALLERY_CACHE_DIR)
    from config.yahoo_conf import GALLERY_CACHE_DIR
    cache_dir = os.path.join(os.path.expanduser(GALLERY_CACHE_DIR), key)
    cached = []
    if os.path.isdir(cache_dir):
        for f in sorted(os.listdir(cache_dir)):
            if f.endswith(('.jpg','.jpeg','.png','.webp')) and not f.startswith('.') and not f.startswith('cover.'):
                cached.append(os.path.abspath(os.path.join(cache_dir, f)))
    news['cached_images'] = cached
    # Fallback: 从 meta.json 读图集链接
    if not news.get('gallery_url'):
        meta_path = os.path.join(os.path.expanduser(GALLERY_CACHE_DIR), key, 'meta.json')
        if os.path.exists(meta_path):
            import json as _json
            try:
                with open(meta_path) as f:
                    meta = _json.load(f)
                news['gallery_url'] = meta.get('gallery_url', '')
            except: pass
    scores = get_scores(key)
    return rts(DETAIL_HTML, news=news, scores=scores)

@app.route('/api/news')
def api_list():
    s = stats()
    rows = query_news(
        date_from=request.args.get('date_from',''),
        date_to=request.args.get('date_to',''),
        category=request.args.get('category',''),
        status=request.args.get('status','active'),
        search=request.args.get('search',''),
        publish_xhs=request.args.get('publish_xhs',''),
        sort_by=request.args.get('sort_by','created_at'),
        sort_dir=request.args.get('sort_dir','DESC'),
        limit=min(int(request.args.get('limit',200)), 500),
    )
    return jsonify({"rows": rows, **s})

@app.route('/api/news/<key>')
def api_detail(key):
    news = get_by_key(key)
    if not news: return jsonify({"error": "not found"}), 404
    news['scores'] = get_scores(key)
    return jsonify(news)

@app.route('/api/news/<key>', methods=['PUT'])
def api_update(key):
    data = request.get_json()
    update_news(key, data)
    return jsonify({"ok": True})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
