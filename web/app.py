#!/usr/bin/env python3
"""新闻管理 Web UI — SQLite 版 Notion 替代"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from flask import Flask, jsonify, render_template_string, request
from sqlite_db import init_db, query_news, get_by_key, update_news, get_scores, upsert_scores, stats

app = Flask(__name__)
init_db()

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
  <div class="grow"></div>
  <button class="btn btn-sm btn-secondary" onclick="location.reload()">刷新</button>
</div>
<div class="filters">
  <input type="text" id="search" placeholder="搜索标题/正文...">
  <input type="date" id="dateFrom" title="开始日期">
  <input type="date" id="dateTo" title="结束日期">
  <select id="category"><option value="">全部分类</option></select>
  <select id="status"><option value="active">活跃</option><option value="archived">已归档</option></select>
  <button class="btn btn-primary btn-sm" onclick="loadList()">筛选</button>
</div>
<table class="table">
  <thead><tr>
    <th onclick="setSort('pub_time')">发布时间 ↕</th>
    <th>标题</th>
    <th>分类</th>
    <th onclick="setSort('title_score')">评分</th>
    <th>标签</th>
  </tr></thead>
  <tbody id="tbody"></tbody>
</table>
<div class="pagination" id="pager"></div>

<!-- Modal preview -->
<div class="modal" id="modal" onclick="if(event.target===this)closeModal()">
  <div class="modal-content" id="modalContent"></div>
</div>

<script>
let sortBy='pub_time',sortDir='DESC',page=0;
const S=id=>document.getElementById(id);

async function loadList(){
  const p=new URLSearchParams({sort_by:sortBy,sort_dir:sortDir,limit:100,offset:page*100,
    search:S('search').value,date_from:S('dateFrom').value,date_to:S('dateTo').value,
    category:S('category').value,status:S('status').value});
  const r=await fetch('/api/news?'+p); const d=await r.json();
  S('tbody').innerHTML=d.rows.map(n=>`<tr>
    <td style="white-space:nowrap">${n.pub_time||''}</td>
    <td><a href="/detail/${n.key}" style="color:#333;text-decoration:none"
           onclick="event.stopPropagation()"><b>${esc(n.title||'')}</b></a><br>
        <span style="color:#999;font-size:11px">${esc((n.content||'').substring(0,50))}</span></td>
    <td>${n.category||''}</td>
    <td><span class="score ${n.title_score>3?'score-hi':n.title_score>1?'score-mid':'score-lo'}">${(n.title_score||0).toFixed(1)}</span></td>
    <td>${(n.tags||[]).slice(0,3).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ')}</td>
  </tr>`).join('');
  S('stats').innerHTML=`共 ${d.total} 条 | 今日 ${d.today} | 待发布 ${d.pending}`;
}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
function setSort(col){ sortBy=col; sortDir=sortBy===col&&sortDir==='DESC'?'ASC':'DESC'; loadList(); }

async function preview(key){
  const r=await fetch('/api/news/'+key); const n=await r.json();
  S('modalContent').innerHTML=`
    ${n.image_url?`<img class="preview-img" src="${esc(n.image_url)}">`:''}
    <h2>${esc(n.title)}</h2>
    <div class="meta">${n.pub_time} | ${n.source} | ${n.category}</div>
    ${n.summary?`<p style="color:#555;margin:8px 0">${esc(n.summary)}</p>`:''}
    <div class="section"><h4>新闻要点</h4><p>${esc(n.content||'').replace(/\n/g,'<br>')}</p></div>
    <div class="section"><h4>我的解读</h4><p>${esc(n.comment||'').replace(/\n/g,'<br>')}</p></div>
    <div class="section"><h4>标签</h4>${(n.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ')}</div>
    <div style="margin-top:16px"><a href="/detail/${n.key}" class="btn btn-primary btn-sm">编辑详情</a> <button class="btn btn-secondary btn-sm" onclick="closeModal()">关闭</button></div>`;
  S('modal').classList.add('active');
}
function closeModal(){S('modal').classList.remove('active');}

async function loadCategories(){
  const cats=[...new Set((await(await fetch('/api/news?limit=500')).json()).rows.map(r=>r.category).filter(Boolean))];
  S('category').innerHTML='<option value="">全部分类</option>'+cats.map(c=>`<option>${esc(c)}</option>`).join('');
}
loadList();loadCategories();
</script>
</body></html>"""

DETAIL_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>编辑 - {{news.title}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:14px -apple-system,sans-serif;background:#f8f8f8;color:#333}
.header{background:#fff;border-bottom:1px solid #e0e0e0;padding:12px 20px;display:flex;align-items:center;gap:16px}
.header a{color:#ff2442;text-decoration:none;font-size:13px}
form{padding:20px;max-width:800px;margin:0 auto}
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
</style>
</head>
<body>
<div class="header"><a href="/">← 返回列表</a><span>{{news.title}}</span></div>
<form id="editForm">
  <label>标题</label><input name="title" value="{{news.title}}">
  <label>引流摘要</label><input name="summary" value="{{news.summary or ''}}">
  <label>新闻要点</label><textarea name="content">{{news.content or ''}}</textarea>
  <label>我的解读</label><textarea name="comment">{{news.comment or ''}}</textarea>
  <div class="row">
    <div><label>分类</label><input name="category" value="{{news.category or ''}}"></div>
    <div><label>标签 (逗号分隔)</label><input name="tags" value="{{', '.join(news.tags or [])}}"></div>
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
    <a href="/" class="btn btn-secondary">取消</a>
  </div>
</form>
<div class="toast" id="toast">已保存</div>
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
document.getElementById('editForm').addEventListener('submit',async e=>{
  e.preventDefault();
  const fd=new FormData(e.target);
  const data={}; for(const[k,v]of fd)data[k]=v;
  data.tags=data.tags.split(',').map(s=>s.trim()).filter(Boolean);
  data.publish_xhs=parseInt(data.publish_xhs);
  const r=await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  if(r.ok){const t=document.getElementById('toast');t.style.display='block';setTimeout(()=>t.style.display='none',1500)}
});
</script>
</body></html>"""

@app.route('/')
def index():
    return render_template_string(INDEX_HTML)

@app.route('/detail/<key>')
def detail(key):
    from flask import render_template_string as rts
    news = get_by_key(key)
    if not news:
        return "Not found", 404
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
