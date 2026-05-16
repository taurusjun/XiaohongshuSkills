#!/usr/bin/env python3
"""新闻管理 Web UI — SQLite 版 Notion 替代"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))  # project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from flask import Flask, jsonify, render_template_string, request, send_file
from config.yahoo_conf import STORAGE_BACKEND
from sqlite_db import init_db, query_news, get_by_key, update_news, get_scores, upsert_scores, stats
from gallery_downloader import trigger_download, get_status as gstatus, upload_selected

import subprocess, json, glob, threading

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
def _run_task(cmd, task_id):
    try:
        _sp.run(cmd, capture_output=True, text=True, timeout=600,
                cwd=os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        _tasks[task_id] = 'done'
    except Exception as e:
        _tasks[task_id] = f'error: {e}'

@app.route('/api/gallery-download/<key>', methods=['POST'])
def api_gallery_download(key):
    trigger_download(key)
    return jsonify({"status": "started"})

@app.route('/api/trigger-fetch', methods=['POST'])
def api_trigger_fetch():
    global _task_counter
    data = request.json or {}
    tid = str(_task_counter); _task_counter += 1
    _tasks[tid] = 'running'
    if data.get('mode') == 'keywords':
        cmd = [sys.executable, 'yahoo_news_auto.py', '--keyword', data.get('keyword','AKB'),
               '--max', str(data.get('max',5)), '--push', '--auto']
    else:
        cmd = [sys.executable, 'yahoo_recommendations.py',
               '--max', str(data.get('max',10)), '--push', '--auto']
    threading.Thread(target=_run_task, args=(cmd, tid), daemon=True).start()
    return jsonify({"task_id": tid})

@app.route('/api/trigger-publish', methods=['POST'])
def api_trigger_publish():
    global _task_counter
    tid = str(_task_counter); _task_counter += 1
    _tasks[tid] = 'running'
    cmd = [sys.executable, 'yahoo_news_publish.py', '--auto', '--force', '--reuse-existing-tab']
    threading.Thread(target=_run_task, args=(cmd, tid), daemon=True).start()
    return jsonify({"task_id": tid})

@app.route('/api/task/<tid>')
def api_task(tid):
    return jsonify({"status": _tasks.get(tid, 'unknown')})

@app.route('/api/gallery-upload/<key>', methods=['POST'])
def api_gallery_upload(key):
    data = request.json or {}
    selected = data.get('selected', [])
    urls = upload_selected(key, selected)
    return jsonify({"ok": True, "urls": urls})

@app.route('/api/gallery-status/<key>')
def api_gallery_status(key):
    return jsonify(gstatus(key))

@app.route('/api/news')
def api_list():
    s = stats()
    rows = query_news(
        date_from=request.args.get('date_from',''),
        date_to=request.args.get('date_to',''),
        category=request.args.get('category',''),
        status=request.args.get('status','active'),
        search=request.args.get('search',''),
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
