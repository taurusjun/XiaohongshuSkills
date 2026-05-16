#!/usr/bin/env python3
"""新闻管理 Web UI — SQLite 版 Notion 替代"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))  # project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from flask import Flask, jsonify, render_template_string, request, send_file
from config.yahoo_conf import STORAGE_BACKEND
from sqlite_db import init_db, query_news, get_by_key, update_news, get_scores, upsert_scores, stats
from web.gallery_downloader import trigger_download, get_status as gstatus, upload_selected

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
_publish_lock = threading.Lock()
_publish_running = False
_fetch_lock = threading.Lock()
_fetch_running = False

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
        for line in proc.stdout:
            log_lines.append(line.rstrip())
            _tasks[task_id] = {'status': 'running', 'log': '\n'.join(log_lines)}
        proc.wait(timeout=600)
        log = '\n'.join(log_lines).strip()
        _tasks[task_id] = {'status': 'done', 'log': log}
    except Exception as e:
        _tasks[task_id] = {'status': f'error: {e}', 'log': '\n'.join(log_lines)}
    finally:
        if on_done:
            on_done()

@app.route('/api/gallery-download/<key>', methods=['POST'])
def api_gallery_download(key):
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
        cmd = [sys.executable, 'yahoo_news_auto.py', '--keyword', data.get('keyword','AKB'),
               '--max', str(data.get('max',5)), '--push', '--auto']
    else:
        cmd = [sys.executable, 'yahoo_recommendations.py',
               '--max', str(data.get('max',10)), '--push', '--auto']
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
        return jsonify(t)
    return jsonify({"status": t, "log": ""})

@app.route('/api/active-tasks')
def api_active_tasks():
    """返回所有运行中的任务"""
    active = []
    for tid, t in _tasks.items():
        if isinstance(t, dict) and t.get('status') == 'running':
            active.append({'task_id': tid, 'status': 'running', 'log': t.get('log', '')})
    return jsonify({"active": active, "fetch_running": _fetch_running, "publish_running": _publish_running})

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
*{margin:0;padding:0;box-sizing:border-box}
body{font:14px -apple-system,sans-serif;background:#f8f8f8;color:#333}
.header{background:#fff;border-bottom:1px solid #e0e0e0;padding:12px 20px;display:flex;align-items:center;gap:16px;position:sticky;top:0;z-index:100}
.header h1{font-size:18px;font-weight:600}
.stats{display:flex;gap:16px;font-size:13px;color:#666}
.filters{display:flex;gap:8px;flex-wrap:wrap;padding:12px 20px;background:#fff;border-bottom:1px solid #eee}
.filters input,.filters select{padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:13px}
.filters input{width:200px}
.table{width:100%;border-collapse:collapse;background:#fff}
.table th{background:#f5f5f5;padding:10px 12px;text-align:left;font-size:12px;font-weight:600;color:#666;border-bottom:2px solid #e0e0e0;cursor:pointer;user-select:none}
.table td{padding:8px 12px;border-bottom:1px solid #f0f0f0;font-size:13px}
.table tr:hover{background:#fafafa}
.table tr{cursor:pointer}
.score{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;font-weight:600}
.score-hi{background:#d4edda;color:#155724}
.score-mid{background:#fff3cd;color:#856404}
.score-lo{background:#f8d7da;color:#721c24}
.tag{display:inline-block;background:#e8f0fe;color:#1967d2;padding:1px 6px;border-radius:4px;font-size:11px;margin:1px 2px}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:200;justify-content:center;align-items:center}
.modal.active{display:flex}
.modal-content{background:#fff;border-radius:12px;max-width:700px;width:90%;max-height:85vh;overflow-y:auto;padding:24px}
.modal img.preview-img{max-width:100%;max-height:300px;border-radius:8px;margin-bottom:12px}
.modal h2{font-size:20px;margin-bottom:8px}
.modal .meta{color:#666;font-size:12px;margin-bottom:12px}
.modal .section{margin:12px 0;padding:8px 0;border-top:1px solid #eee}
.modal .section h4{font-size:13px;color:#999;margin-bottom:4px}
.detail-page{padding:20px;max-width:800px;margin:0 auto}
.detail-page label{display:block;font-size:13px;color:#666;margin:12px 0 4px}
.detail-page input,.detail-page textarea,.detail-page select{width:100%;padding:8px 12px;border:1px solid #ddd;border-radius:6px;font-size:14px}
.detail-page textarea{min-height:100px;resize:vertical}
.btn{padding:8px 16px;border:none;border-radius:6px;cursor:pointer;font-size:13px;font-weight:500}
.btn-primary{background:#ff2442;color:#fff}
.btn-secondary{background:#f0f0f0;color:#333}
.btn-sm{padding:4px 10px;font-size:12px}
.flex{display:flex;gap:8px;align-items:center}
.grow{flex:1}
.pagination{padding:16px;text-align:center;font-size:13px;color:#666}
</style>
</head>
<body>
<div class="header">
  <h1>📰 新闻管理</h1>
  <div class="stats" id="stats"></div>
  <span id="taskBar" style="display:none;font-size:12px;cursor:pointer;color:#ff6b35;font-weight:500" onclick="showTaskModal()"></span>
  <div class="grow"></div>
  <input type="text" id="keywordInput" placeholder="关键词" style="width:80px;padding:4px 6px;font-size:12px;border:1px solid #ddd;border-radius:4px" value="AKB">
  <input type="number" id="kwMax" value="5" min="1" max="30" style="width:50px;padding:4px 6px;font-size:12px;border:1px solid #ddd;border-radius:4px">
  <button class="btn btn-sm btn-primary" onclick="triggerFetch('keywords')" id="kwBtn">🔍 抓取</button>
  <input type="number" id="recomMax" value="10" min="1" max="30" style="width:50px;padding:4px 6px;font-size:12px;border:1px solid #ddd;border-radius:4px">
  <button class="btn btn-sm btn-primary" onclick="triggerFetch('recom')" id="recomBtn">📰 推荐</button>
  <button class="btn btn-sm" onclick="triggerPublish()" id="pubBtn" style="background:#ff6b35;color:#fff">📤 发布到小红书</button>
  <button class="btn btn-sm btn-secondary" onclick="location.reload()">刷新</button>
</div>
<div class="filters">
  <input type="text" id="search" placeholder="搜索标题/正文...">
  <input type="date" id="dateFrom" title="开始日期">
  <input type="date" id="dateTo" title="结束日期">
  <select id="category"><option value="">全部分类</option></select>
  <select id="status"><option value="active">活跃</option><option value="archived">已归档</option></select>
  <select id="publishXhs"><option value="">发布小红书</option><option value="published">已发布</option><option value="pending">待发布</option><option value="unpublished">未发布</option></select>
  <button class="btn btn-primary btn-sm" onclick="loadList()">筛选</button>
</div>
<table class="table">
  <thead><tr>
    <th style="width:40px">#</th>
    <th onclick="setSort('pub_time')">新闻时间 ↕</th>
    <th>标题</th>
    <th style="width:55px">发布XHS</th>
    <th style="width:85px">发布XHS时间</th>
    <th>分类</th>
    <th onclick="setSort('title_score')">评分 ↕</th>
    <th>标签</th>
  </tr></thead>
  <tbody id="tbody"></tbody>
</table>
<div class="pagination" id="pager"></div>

<!-- Modal preview -->
<div class="modal" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal-content" id="modalContent"></div>
</div>

<!-- Terminal log modal -->
<div class="modal" id="taskModal" onclick="if(event.target===this)closeTaskModal()">
  <div class="modal-content" style="max-width:750px;background:#1e1e1e;color:#0f0">
    <h3 id="taskModalTitle" style="color:#fff;margin-bottom:12px">🖥️ 终端</h3>
    <pre id="taskLog" style="font:12px Menlo,monospace;white-space:pre-wrap;min-height:300px;max-height:60vh;overflow-y:auto;margin:0">等待中...</pre>
    <div style="margin-top:12px;text-align:right">
      <button class="btn btn-sm" style="background:#555;color:#fff" onclick="closeTaskModal()">关闭</button>
    </div>
  </div>
</div>

<script>
let sortBy='created_at',sortDir='DESC',page=0;
let activeTaskId=null, activeTaskLabel='';
const S=id=>document.getElementById(id);

async function checkActiveTasks(){
  const r=await fetch('/api/active-tasks'); const d=await r.json();
  if(d.active.length>0 || d.fetch_running){
    // 抓取运行中：禁抓取/推荐按钮 + 灰化
    S('taskBar').style.display=''; S('taskBar').textContent='⏳ 抓取任务运行中...点击查看';
    activeTaskId=d.active.length>0?d.active[0].task_id:localStorage.getItem('lastTaskId');
    ['kwBtn','recomBtn'].forEach(id=>{ S(id).disabled=true; S(id).style.opacity='0.5'; });
    S('pubBtn').disabled=false; S('pubBtn').style.opacity='1';
  } else if(d.publish_running){
    S('taskBar').style.display=''; S('taskBar').textContent='⏳ 发布任务运行中...点击查看';
    activeTaskId=localStorage.getItem('lastTaskId');
    S('pubBtn').disabled=true; S('pubBtn').style.opacity='0.5';
    ['kwBtn','recomBtn'].forEach(id=>{ S(id).disabled=false; S(id).style.opacity='1'; });
  } else {
    S('taskBar').style.display='none'; activeTaskId=null; localStorage.removeItem('lastTaskId');
    ['kwBtn','recomBtn','pubBtn'].forEach(id=>{ S(id).disabled=false; S(id).style.opacity='1'; });
  }
}
function showTaskModal(){
  if(!activeTaskId) return;
  S('taskModal').classList.add('active');
  pollTaskLog(activeTaskId);
}
async function pollTaskLog(tid){
  const sr=await fetch('/api/task/'+tid); const sd=await sr.json();
  if(sd.log) S('taskLog').textContent=sd.log;
  if(sd.status==='running'){ setTimeout(()=>pollTaskLog(tid), 3000); }
  else{ checkActiveTasks(); }
}

async function loadList(){
  const p=new URLSearchParams({sort_by:sortBy,sort_dir:sortDir,limit:100,offset:page*100,
    search:S('search').value,date_from:S('dateFrom').value,date_to:S('dateTo').value,
    category:S('category').value,status:S('status').value,publish_xhs:S('publishXhs').value});
  const r=await fetch('/api/news?'+p); const d=await r.json();
  S('tbody').innerHTML=d.rows.map((n,i)=>`<tr>
    <td style="color:#999;font-size:11px">${page*100+i+1}</td>
    <td style="white-space:nowrap">${n.pub_time||''}</td>
    <td><a href="/detail/${n.key}" style="color:#333;text-decoration:none"
           onclick="event.stopPropagation()"><b>${esc(n.title||'')}</b></a><br>
        <span style="color:#999;font-size:11px">${esc((n.content||'').substring(0,50))}</span></td>
    <td><input type="checkbox" ${n.publish_xhs?'checked':''} onchange="togglePublish('${n.key}',this.checked)" onclick="event.stopPropagation()"></td>
    <td style="font-size:11px;color:#999">${n.publish_time||''}</td>
    <td>${n.category||''}</td>
    <td><span class="score ${n.title_score>3?'score-hi':n.title_score>1?'score-mid':'score-lo'}">${(n.title_score||0).toFixed(1)}</span></td>
    <td>${(n.tags||[]).slice(0,3).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ')}</td>
  </tr>`).join('');
  S('stats').innerHTML=`共 ${d.total} 条 | 今日 ${d.today} | 待发布 ${d.pending}`;
  // Pagination
  const totalPages=Math.ceil(d.total/100);
  let pager='';
  if(totalPages>1){
    pager+=`<button class="btn btn-sm btn-secondary" onclick="goPage(${page-1})" ${page<=0?'disabled':''}>‹ 上一页</button> `;
    for(let i=0;i<totalPages;i++){
      if(i===page)pager+=`<button class="btn btn-sm btn-primary">${i+1}</button> `;
      else pager+=`<button class="btn btn-sm btn-secondary" onclick="goPage(${i})">${i+1}</button> `;
    }
    pager+=`<button class="btn btn-sm btn-secondary" onclick="goPage(${page+1})" ${page>=totalPages-1?'disabled':''}>下一页 ›</button>`;
  }
  S('pager').innerHTML=pager;
}
function goPage(n){page=n;loadList();window.scrollTo(0,0);}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function setSort(col){ if(sortBy===col){sortDir=sortDir==='DESC'?'ASC':'DESC'}else{sortBy=col;sortDir='DESC'} loadList(); }

async function preview(key){
  const r=await fetch('/api/news/'+key); const n=await r.json();
  let imgs='';
  if(n.image_url) imgs+=`<img class="preview-img" src="${esc(n.image_url)}">`;
  // Gallery images
  if(n.gallery_images){ try{
    const g=typeof n.gallery_images==='string'?JSON.parse(n.gallery_images):n.gallery_images;
    g.forEach(p=>{ imgs+=`<img class="preview-img" src="/local-image?path=${encodeURIComponent(p)}">`; });
  }catch(e){}}
  S('modalContent').innerHTML=`
    ${imgs}
    <h2>${esc(n.title)}</h2>
    <div class="meta">${n.pub_time} | ${n.source} | ${n.category} | 📊标题${(n.title_score||0).toFixed(1)} 内容${(n.content_score||0).toFixed(1)}</div>
    ${n.summary?`<p style="color:#555;margin:8px 0">${esc(n.summary)}</p>`:''}
    <div class="section"><h4>新闻要点</h4><p>${esc(n.content||'').replace(/\n/g,'<br>')}</p></div>
    <div class="section"><h4>我的解读</h4><p>${esc(n.comment||'').replace(/\n/g,'<br>')}</p></div>
    ${n.video_caption?`<div class="section"><h4>🎬 短配文</h4><p>${esc(n.video_caption||'')}</p></div>`:''}
    <div class="section"><h4>标签</h4>${(n.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ')}</div>
    <div style="margin-top:16px"><a href="/detail/${n.key}" class="btn btn-primary btn-sm">编辑详情</a> <button class="btn btn-secondary btn-sm" onclick="closeModal()">关闭</button></div>`;
  S('modal').classList.add('active');
}
function closeModal(){S('modal').classList.remove('active');}
function closeTaskModal(){S('taskModal').classList.remove('active');}
function restoreButtons(){['kwBtn','recomBtn','pubBtn'].forEach(id=>{var b=S(id);if(b){b.disabled=false;b.style.opacity='1';}});}
function disableFetchBtns(){['kwBtn','recomBtn'].forEach(id=>{var b=S(id);if(b){b.disabled=true;b.style.opacity='0.5';}});}
async function triggerFetch(mode){
  const bid=mode==='keywords'?'kwBtn':'recomBtn';
  const b=document.getElementById(bid);
  const orig=b.textContent; disableFetchBtns(); b.textContent='⏳ 运行中...';
  const body={mode};
  if(mode==='keywords'){body.keyword=document.getElementById('keywordInput').value;body.max=parseInt(document.getElementById('kwMax').value)||5;}
  else{body.max=parseInt(document.getElementById('recomMax').value)||10;}
  // Show terminal modal
  S('taskModalTitle').textContent=mode==='keywords'?'🔍 抓取关键词':'📰 推荐新闻';
  S('taskLog').textContent='⏳ 启动中...';
  S('taskModal').classList.add('active');
  const r=await fetch('/api/trigger-fetch',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const d=await r.json();
  if(d.locked){S('taskLog').textContent='🔒 '+d.msg; b.textContent=orig; b.style.opacity='1'; b.disabled=false; return;}
  const tid=d.task_id;
  activeTaskId=tid; activeTaskLabel=mode==='keywords'?'关键词抓取':'推荐抓取';
  localStorage.setItem('lastTaskId',tid);
  S('taskBar').style.display=''; S('taskBar').textContent='⏳ '+activeTaskLabel+'运行中...点击查看';
  for(let i=0;i<120;i++){
    await new Promise(r=>setTimeout(r,3000));
    const sr=await fetch('/api/task/'+tid); const sd=await sr.json();
    if(sd.log)S('taskLog').textContent=sd.log;
    if(sd.status==='done'){b.textContent='✅ 完成';b.style.opacity='1';activeTaskId=null;localStorage.removeItem('lastTaskId');S('taskBar').style.display='none';setTimeout(()=>{b.disabled=false;b.textContent=orig;loadList();},2000);return;}
    if(sd.status&&sd.status.startsWith('error')){b.textContent='❌ 失败';b.style.opacity='1';b.disabled=false;activeTaskId=null;localStorage.removeItem('lastTaskId');S('taskBar').style.display='none';S('taskLog').textContent+='\n\n❌ '+sd.status;return;}
  }
  b.textContent='⏰ 超时'; b.style.opacity='1'; b.disabled=false; activeTaskId=null; localStorage.removeItem('lastTaskId'); S('taskBar').style.display='none';
}
async function triggerPublish(){
  const b=document.getElementById('pubBtn'); b.disabled=true; b.style.opacity='0.5'; b.textContent='⏳ 发布中...';
  S('taskModalTitle').textContent='📤 发布到小红书';
  S('taskLog').textContent='⏳ 启动中...';
  S('taskModal').classList.add('active');
  const r=await fetch('/api/trigger-publish',{method:'POST'});
  const d=await r.json();
  if(d.locked){S('taskLog').textContent='🔒 '+d.msg; b.textContent='📤 发布到小红书'; b.style.opacity='1'; b.disabled=false; return;}
  const tid=d.task_id;
  activeTaskId=tid; activeTaskLabel='发布';
  localStorage.setItem('lastTaskId',tid);
  S('taskBar').style.display=''; S('taskBar').textContent='⏳ 发布任务运行中...点击查看';
  for(let i=0;i<120;i++){
    await new Promise(r=>setTimeout(r,3000));
    const sr=await fetch('/api/task/'+tid); const sd=await sr.json();
    if(sd.log)S('taskLog').textContent=sd.log;
    if(sd.status==='done'){b.textContent='✅ 完成';b.style.opacity='1';activeTaskId=null;localStorage.removeItem('lastTaskId');S('taskBar').style.display='none';setTimeout(()=>{b.disabled=false;b.textContent='📤 发布到小红书';},2000);return;}
    if(sd.status&&sd.status.startsWith('error')){b.textContent='❌ 失败';b.style.opacity='1';b.disabled=false;activeTaskId=null;localStorage.removeItem('lastTaskId');S('taskBar').style.display='none';S('taskLog').textContent+='\n\n❌ '+sd.status;return;}
  }
  b.textContent='⏰ 超时'; b.style.opacity='1'; b.disabled=false; activeTaskId=null; localStorage.removeItem('lastTaskId'); S('taskBar').style.display='none';
}
async function togglePublish(key,val){
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_xhs:val?1:0})});
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
<title>编辑 - {{news.title}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:14px -apple-system,sans-serif;background:#f8f8f8;color:#333;padding-bottom:3em}
.header{background:#fff;border-bottom:1px solid #e0e0e0;padding:12px 20px;display:flex;align-items:center;gap:16px}
.header a{color:#ff2442;text-decoration:none;font-size:13px}
form{padding:20px 20px 3em;max-width:800px;margin:0 auto}
label{display:block;font-size:13px;color:#666;margin:14px 0 4px;font-weight:500}
input,textarea,select{width:100%;padding:10px 14px;border:1px solid #ddd;border-radius:8px;font-size:14px;font-family:inherit}
textarea{min-height:120px;resize:vertical}
.row{display:grid;grid-template-columns:1fr 1fr;gap:16px}
.btn{padding:10px 24px;border:none;border-radius:8px;cursor:pointer;font-size:14px;font-weight:500}
.btn-primary{background:#ff2442;color:#fff}
.btn-secondary{background:#f0f0f0;color:#333}
.actions{display:flex;gap:12px;margin-top:24px}
.score-block{background:#f5f5f5;border-radius:8px;padding:16px;margin-top:16px}
.score-block h3{font-size:14px;margin-bottom:8px}
.score-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:6px}
.score-item{text-align:center;padding:6px;border-radius:6px;font-size:12px}
.score-plus{background:#d4edda}
.score-minus{background:#f8d7da}
.toast{position:fixed;top:20px;right:20px;background:#28a745;color:#fff;padding:12px 20px;border-radius:8px;display:none;z-index:999}
.tag-bubble{display:inline-flex;align-items:center;background:#e8f0fe;color:#1967d2;padding:4px 10px;border-radius:12px;font-size:12px;margin:2px 4px;gap:6px}
.tag-bubble .del{cursor:pointer;opacity:.5;font-weight:bold}
.tag-bubble .del:hover{opacity:1}
.tag-input{border:none;background:transparent;padding:4px 8px;font-size:12px;width:80px;outline:none}
.tag-add{cursor:pointer;background:#e8f0fe;color:#1967d2;padding:4px 10px;border-radius:12px;font-size:12px;border:1px dashed #1967d2}
.modal{display:none;position:fixed;top:0;left:0;width:100%;height:100%;background:rgba(0,0,0,.5);z-index:200;justify-content:center;align-items:center}
.modal.active{display:flex}
.modal-content{background:#fff;border-radius:12px;max-width:700px;width:90%;max-height:85vh;overflow-y:auto;padding:24px}
</style>
</head>
<body>
<div class="header"><a href="/">← 返回列表</a><span>{{news.title}}</span></div>
<form id="editForm">
  {% if news.image_url %}<img src="{{news.image_url}}" style="max-width:100%;max-height:300px;border-radius:8px;margin-bottom:12px">{% endif %}
  <div style="margin-bottom:12px;background:#f8f8f8;padding:10px;border-radius:8px;font-size:12px">
    <div style="margin-bottom:4px"><label style="font-size:11px">🔗 原文链接</label><input value="{{news.link or ''}}" readonly style="width:100%;padding:3px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px;color:#666;background:#fff" onclick="this.select()"></div>
    <div style="margin-bottom:4px"><label style="font-size:11px">🖼️ 封面图链接</label><input value="{{news.image_url or ''}}" readonly style="width:100%;padding:3px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px;color:#666;background:#fff" onclick="this.select()"></div>
    {% if news.original_image_url and news.original_image_url != news.image_url %}
    <div style="margin-bottom:4px"><label style="font-size:11px">📷 原图链接</label><input value="{{news.original_image_url or ''}}" readonly style="width:100%;padding:3px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px;color:#666;background:#fff" onclick="this.select()"></div>
    {% endif %}
    <div style="margin-bottom:4px"><label style="font-size:11px">📸 图集源链接</label><input name="gallery_url" value="{{news.gallery_url or ''}}" style="width:100%;padding:3px 6px;border:1px solid #ddd;border-radius:4px;font-size:11px;color:#666;background:#fff" placeholder="https://..."></div>
  </div>
  {% if news.gallery_images %}
  <label style="font-size:13px;color:#666;margin:14px 0 4px;font-weight:500">📸 发布图片 (勾选的将发到小红书)</label>
  <div id="publishImgStrip" style="display:flex;gap:8px;overflow-x:auto;margin-bottom:4px">
    {% for p in news.gallery_images %}
    <div style="position:relative;flex-shrink:0;cursor:pointer" onclick="togglePublishImg(this)">
      <img src="/local-image?path={{p}}" style="max-height:150px;border-radius:6px">
      <input type="checkbox" style="position:absolute;top:4px;left:4px;pointer-events:none" data-path="{{p}}">
    </div>
    {% endfor %}
  </div>
  <button type="button" class="btn btn-sm btn-primary" onclick="savePublishImages()" style="margin-bottom:12px">💾 保存发布图选择</button>
  {% endif %}
  <div class="row">
    <div>📊 标题评分: {{"%.1f"|format(news.title_score or 0)}}</div>
    <div>📊 内容评分: {{"%.1f"|format(news.content_score or 0)}}</div>
  </div>
  <label>标题</label><input name="title" value="{{news.title}}">
  <label>🎬 短配文</label><textarea name="video_caption" style="min-height:40px">{{news.video_caption or ''}}</textarea>
  <label>引流摘要</label><input name="summary" value="{{news.summary or ''}}">
  <label>新闻要点</label><textarea name="content">{{news.content or ''}}</textarea>
  <label>我的解读</label><textarea name="comment">{{news.comment or ''}}</textarea>
  <div class="row">
    <div><label>分类</label><input name="category" value="{{news.category or ''}}"></div>
    <div><label>标签</label><div id="tagBubbles" style="display:flex;flex-wrap:wrap;align-items:center;min-height:36px;padding:4px;border:1px solid #ddd;border-radius:8px"></div></div>
  </div>
  <div class="row">
    <div><label>发布XHS</label><select name="publish_xhs">
      <option value="0" {{'selected' if not news.publish_xhs else ''}}>否</option>
      <option value="1" {{'selected' if news.publish_xhs else ''}}>是</option>
    </select></div>
    <div><label>状态</label><select name="status">
      <option value="active" {{'selected' if news.status=='active' else ''}}>活跃</option>
      <option value="archived" {{'selected' if news.status=='archived' else ''}}>已归档</option>
    </select></div>
  </div>
  {% if scores %}
  <div class="score-block">
    <h3>📊 评分明细</h3>
    <div class="score-grid" id="scoreGrid"></div>
  </div>
  {% endif %}
  <div class="actions">
    <button type="submit" class="btn btn-primary">保存</button>
    <button type="button" class="btn btn-secondary" onclick="downloadGallery()" id="galleryBtn">📸 下载图集</button>
    <button type="button" class="btn btn-secondary" onclick="toggleGalleryModal()">🖼️ 管理图集</button>
    <a href="/" class="btn btn-secondary">取消</a>
  </div>
  <pre id="galleryLog" style="display:none;margin-top:12px;padding:12px;background:#1e1e1e;color:#0f0;border-radius:6px;font-size:12px;max-height:300px;overflow-y:auto;white-space:pre-wrap;font-family:Menlo,monospace"></pre>
</form>
<div class="toast" id="toast">已保存</div>
<div class="modal" id="galleryModal" onclick="if(event.target===this)closeGalleryModal()">
  <div class="modal-content" style="max-width:800px">
    <h3>📸 选择要保留的图片</h3>
    <div id="galleryGrid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:12px 0;max-height:60vh;overflow-y:auto"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end">
      <button class="btn btn-secondary" onclick="selectAllGallery(true)">全选</button>
      <button class="btn btn-secondary" onclick="selectAllGallery(false)">取消全选</button>
      <button class="btn btn-primary" onclick="saveGallery()">💾 保存</button>
      <button class="btn btn-primary" onclick="saveAndUpload()" style="background:#ff6b35">☁️ 保存并上传</button>
      <button class="btn btn-secondary" onclick="closeGalleryModal()">关闭</button>
    </div>
  </div>
</div>
<script>
const key='{{news.key}}';
{% if scores %}
const scoreData={{scores|tojson}};
const plusCols=['剧情感','冲突感','猎奇感','用户共鸣','名人','热点','原创度','趣味性','有用信息','对立信息','视频','生活照','搞怪照'];
const minusCols=['简单通知','震惊体','概括全部','离题','啰嗦重复','主动讨赏','宣传照','写真','中年男照','负面情绪'];
let html='';
[...plusCols,...minusCols].forEach(c=>{
  html+=`<div class="score-item ${plusCols.includes(c)?'score-plus':'score-minus'}">${c}: ${scoreData[c]||0}</div>`;
});
document.getElementById('scoreGrid').innerHTML=html;
{% endif %}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
// Tag management
let tags={% if news.tags %}{{news.tags|tojson}}{% else %}[]{% endif %};
function renderTags(){
  const el=document.getElementById('tagBubbles');
  el.innerHTML=tags.map((t,i)=>`<span class="tag-bubble">${esc(t)}<span class="del" onclick="delTag(${i})">×</span></span>`).join('')
    +'<input class="tag-input" id="tagInput" placeholder="+ 添加" onkeydown="addTag(event)">';
}
function delTag(i){tags.splice(i,1);renderTags();}
function addTag(e){
  if(e.key==='Enter'||e.key===','){
    e.preventDefault();
    const v=e.target.value.trim().replace(/,$/,'');
    if(v){tags.push(v);e.target.value='';renderTags();}
  }
}
renderTags();

document.getElementById('editForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const fd=new FormData(e.target);
  const data={}; for(const[k,v]of fd)data[k]=v;
  data.gallery_url=document.querySelector('input[name=gallery_url]').value;
  data.tags=tags;
  data.publish_xhs=parseInt(data.publish_xhs);
  const r=await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r.ok){const t=document.getElementById('toast');t.style.display='block';setTimeout(()=>t.style.display='none',1500)}
});

// Gallery: saved=默认选中, cached=默认不选
var galleryImages=[];
(function initGallery(){
  {% if news.gallery_images %}var saved={{news.gallery_images|tojson}};{% else %}var saved=[];{% endif %}
  var savedSet=new Set(saved);
  saved.forEach(function(p){galleryImages.push({path:p,sel:true});});
  {% if news.cached_images %}var cached={{news.cached_images|tojson}};
  cached.forEach(function(p){if(!savedSet.has(p))galleryImages.push({path:p,sel:false});});
  {% endif %}
  // Already downloaded? Change button text
  var hasCache={% if news.cached_images %}cached.length{% else %}0{% endif %};
  if(hasCache>0||galleryImages.length>0){
    document.getElementById('galleryBtn').textContent='🔄 重新下载';
  }
})();
async function downloadGallery(){
  const btn=document.getElementById('galleryBtn'); btn.disabled=true;
  const log=document.getElementById('galleryLog'); log.style.display='block'; log.textContent='⏳ 开始下载...\n';
  await fetch('/api/gallery-download/'+key,{method:'POST'});
  for(let i=0;i<60;i++){
    await new Promise(r=>setTimeout(r,2000));
    const resp=await fetch('/api/gallery-status/'+key); const d=await resp.json();
    if(d.log){log.textContent=d.log; log.scrollTop=log.scrollHeight;}
    if(d.status==='done'){
      d.images.forEach(function(p){galleryImages.push({path:p,sel:false});});
      btn.disabled=false; btn.textContent='🔄 重新下载';
      log.textContent+='\n✅ 完成 ('+d.images.length+'张)';
      setTimeout(function(){showGalleryModal();log.style.display='none';},1500);
      return;
    }
    if(d.status&&d.status.toString().startsWith('error')){log.textContent+='\n❌ '+d.status;btn.disabled=false;return;}
  }
  log.textContent+='\n⏰ 超时'; btn.disabled=false;
}
function toggleGalleryModal(){var m=document.getElementById('galleryModal');if(m.classList.contains('active'))closeGalleryModal();else showGalleryModal();}
function showGalleryModal(){
  const grid=document.getElementById('galleryGrid');
  grid.innerHTML=galleryImages.map(function(p,i){return `<div style="position:relative;cursor:pointer" onclick="toggleGalleryImg(${i})">
    <img src="/local-image?path=${encodeURIComponent(p.path)}" style="width:100%;height:120px;object-fit:cover;border-radius:6px;border:3px solid ${p.sel?'#4CAF50':'#ddd'}">
    <input type="checkbox" ${p.sel?'checked':''} style="position:absolute;top:4px;right:4px;pointer-events:none">
  </div>`}).join('');
  document.getElementById('galleryModal').classList.add('active');
}
function toggleGalleryImg(i){galleryImages[i].sel=!galleryImages[i].sel;showGalleryModal();}
function selectAllGallery(val){galleryImages.forEach(function(p){p.sel=val;});showGalleryModal();}
async function saveGallery(){
  const selected=galleryImages.filter(function(p){return p.sel;}).map(function(p){return p.path;});
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_images:selected})});
  closeGalleryModal(); location.reload();
}
async function saveAndUpload(){
  const selected=galleryImages.filter(function(p){return p.sel;}).map(function(p){return p.path;});
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_images:selected})});
  await fetch('/api/gallery-upload/'+key,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected:selected})});
  alert('已上传 '+selected.length+' 张');
  closeGalleryModal(); location.reload();
}
function closeGalleryModal(){document.getElementById('galleryModal').classList.remove('active');}
function togglePublishImg(el){ var cb=el.querySelector('input[type=checkbox]'); cb.checked=!cb.checked; el.style.opacity=cb.checked?'1':'0.4'; }
async function savePublishImages(){
  var paths=[]; document.querySelectorAll('#publishImgStrip input[type=checkbox]:checked').forEach(function(cb){paths.push(cb.dataset.path);});
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_images:paths})});
  var t=document.getElementById('toast'); t.textContent='发布图已保存 ('+paths.length+'张)'; t.style.display='block'; setTimeout(function(){t.style.display='none';t.textContent='已保存';},1500);
}
(function initPublishCheckboxes(){
  {% if news.publish_images %}var pubSet=new Set({{news.publish_images|tojson}});{% else %}var pubSet=new Set();{% endif %}
  document.querySelectorAll('#publishImgStrip input[type=checkbox]').forEach(function(cb){
    if(pubSet.has(cb.dataset.path)){cb.checked=true;}else{cb.parentElement.style.opacity='0.4';}
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
            if f.endswith(('.jpg','.jpeg','.png','.webp')) and not f.startswith('.'):
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
