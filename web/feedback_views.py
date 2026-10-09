"""feedback_views.py — 发布规律（feedback_patterns）REST + 人工 review 页。"""
import os
import sys

from flask import Blueprint, jsonify, render_template_string, request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from services import feedback_patterns as _fp  # noqa: E402

feedback_bp = Blueprint("feedback", __name__)

_SEARCH_FIELDS = ("title", "body", "tags", "genre", "title_style", "entities",
                  "action", "direction", "category", "confidence")


@feedback_bp.get("/api/feedback-patterns")
def api_list():
    q = (request.args.get("q") or "").strip()
    cat = (request.args.get("category") or "").strip()
    rows = _fp.all_patterns()
    if cat:
        rows = [r for r in rows if (r.get("category") or "") == cat]
    if q:
        terms = [t for t in q.split() if t]

        def _hit(r):
            blob = " ".join(str(r.get(k) or "") for k in _SEARCH_FIELDS)
            return all(t in blob for t in terms)
        rows = [r for r in rows if _hit(r)]
    rows.sort(key=lambda r: -(r.get("no") or 0))
    return jsonify({"ok": True, "total": len(rows), "rows": rows})


@feedback_bp.get("/api/feedback-patterns/<int:no>")
def api_get(no):
    r = _fp.get_pattern(no)
    return jsonify({"ok": r is not None, "row": r})


@feedback_bp.put("/api/feedback-patterns/<int:no>")
def api_update(no):
    body = request.get_json(silent=True) or {}
    ok = _fp.update_row(no, body)
    return jsonify({"ok": ok, "row": _fp.get_pattern(no)})


@feedback_bp.get("/feedback-patterns")
def page():
    return render_template_string(PAGE_HTML)


PAGE_HTML = r"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>发布规律 · 人工 review</title>
<style>
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;margin:0;background:#0f1115;color:#e6e6ee}
.wrap{padding:14px 18px}h1{font-size:15px;margin:0 0 10px}
.bar{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center}
input,select,textarea,button{background:#1a1d24;color:#e6e6ee;border:1px solid #2a2f3a;border-radius:6px;padding:6px 8px;font-size:12px}
button{cursor:pointer}table{width:100%;border-collapse:collapse;font-size:12px}
th,td{border-bottom:1px solid #232935;padding:6px 8px;text-align:left;vertical-align:top}
th{position:sticky;top:0;background:#161a21;z-index:1}tr:hover{background:#141821}
.facet{color:#9fb0c8}.dir-零曝光,.act-跳过,.act-降级{color:#ff6b6b}.dir-爆发,.act-优先{color:#4ade80}
a{color:#8ab4ff}.modal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center;padding:20px}
.modal.on{display:flex}.card{background:#12151b;border:1px solid #2a2f3a;border-radius:10px;max-width:900px;width:100%;max-height:90vh;overflow:auto;padding:16px}
.card label{display:block;font-size:11px;color:#8b97ab;margin:8px 0 3px}
.row{display:flex;gap:10px;flex-wrap:wrap}.row>div{flex:1;min-width:150px}
textarea{width:100%;min-height:150px;font-family:ui-monospace,Menlo,monospace}
</style></head><body><div class="wrap">
<h1>📈 发布规律 feedback_patterns · 人工 review　<a href="/">← 返回</a></h1>
<div class="bar">
  <input id="q" placeholder="搜索 标题/正文/题材/实体/对策…" style="min-width:280px">
  <select id="cat"><option value="">全部类目</option></select>
  <button onclick="load()">查询</button>
  <span id="cnt" class="facet"></span>
</div>
<table><thead><tr>
<th>#</th><th>类目</th><th>题材</th><th>标题类型</th><th>方向</th><th>对策</th><th>置信</th><th>实体</th><th>标题 / 正文</th><th></th>
</tr></thead><tbody id="tb"></tbody></table></div>
<div class="modal" id="m"><div class="card">
  <h1 id="mno">编辑</h1>
  <div class="row">
    <div><label>category 类目</label><input id="f_category"></div>
    <div><label>genre 题材</label><input id="f_genre"></div>
    <div><label>title_style 标题类型</label><input id="f_title_style"></div>
    <div><label>publish_mode 发布方式</label><input id="f_publish_mode"></div>
  </div>
  <div class="row">
    <div><label>direction 方向</label><input id="f_direction"></div>
    <div><label>action 对策</label><input id="f_action"></div>
    <div><label>confidence 置信</label><input id="f_confidence"></div>
    <div><label>entities 实体</label><input id="f_entities"></div>
  </div>
  <label>tags 标签</label><input id="f_tags" style="width:100%">
  <label>title 标题</label><input id="f_title" style="width:100%">
  <label>body 正文(md)</label><textarea id="f_body"></textarea>
  <div class="bar" style="margin-top:12px"><button onclick="save()">保存</button>
  <button onclick="closeM()">取消</button><span id="msg" class="facet"></span></div>
</div></div>
<script>
const FACETS=["category","genre","title_style","publish_mode","direction","action","confidence","entities"];
let R={},cur=null;
const esc=s=>String(s==null?"":s).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]));
async function load(){
  const q=document.getElementById('q').value,cat=document.getElementById('cat').value;
  const r=await(await fetch('/api/feedback-patterns?q='+encodeURIComponent(q)+'&category='+encodeURIComponent(cat))).json();
  R={};r.rows.forEach(x=>R[x.no]=x);
  const cats=[...new Set(r.rows.map(x=>x.category).filter(Boolean))].sort();
  const sel=document.getElementById('cat'),v=sel.value;
  sel.innerHTML='<option value="">全部类目</option>'+cats.map(c=>'<option>'+esc(c)+'</option>').join('');sel.value=v;
  document.getElementById('cnt').textContent='共 '+r.total+' 条';
  document.getElementById('tb').innerHTML=r.rows.map(x=>'<tr>'+
   '<td>'+x.no+'</td><td>'+esc(x.category)+'</td><td>'+esc(x.genre)+'</td><td>'+esc(x.title_style)+'</td>'+
   '<td class="dir-'+esc(x.direction)+'">'+esc(x.direction)+'</td><td class="act-'+esc(x.action)+'">'+esc(x.action)+'</td>'+
   '<td>'+esc(x.confidence)+'</td><td>'+esc(x.entities)+'</td>'+
   '<td><b>'+esc(x.title)+'</b><div class="facet" style="margin-top:3px;white-space:pre-wrap">'+esc((x.body||'').slice(0,220))+'</div></td>'+
   '<td><button onclick="edit('+x.no+')">编辑</button></td></tr>').join('');
}
function edit(no){const x=R[no];cur=x;document.getElementById('mno').textContent='编辑 #'+no;
  FACETS.forEach(f=>document.getElementById('f_'+f).value=x[f]||'');
  document.getElementById('f_tags').value=x.tags||'';document.getElementById('f_title').value=x.title||'';
  document.getElementById('f_body').value=x.body||'';document.getElementById('msg').textContent='';
  document.getElementById('m').classList.add('on');}
function closeM(){document.getElementById('m').classList.remove('on');}
async function save(){if(!cur)return;
  const body={tags:document.getElementById('f_tags').value,title:document.getElementById('f_title').value,
              body:document.getElementById('f_body').value};
  FACETS.forEach(f=>body[f]=document.getElementById('f_'+f).value);
  const r=await(await fetch('/api/feedback-patterns/'+cur.no,{method:'PUT',
    headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})).json();
  document.getElementById('msg').textContent=r.ok?'✅ 已保存':'❌ 保存失败';
  if(r.ok){await load();}
}
load();
</script></body></html>"""
