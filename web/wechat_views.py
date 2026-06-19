"""wechat_views.py — 公众号相关路由和模板（Blueprint）

所有公众号逻辑集中在此文件，app.py 只需最小改动。
"""
import os
import sys
import re
import subprocess
from pathlib import Path
from flask import Blueprint, request, jsonify, render_template_string as rts
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from sqlite_db import get_by_key, update_news
from config.yahoo_conf import GALLERY_CACHE_DIR

wechat_bp = Blueprint('wechat', __name__)


WECHAT_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{news.wechat_title or news.title}} — 公众号</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#0f0f10;--bg2:#16161a;--bg3:#1c1c20;
  --hv:rgba(255,255,255,.04);--br:rgba(255,255,255,.06);--br2:rgba(255,255,255,.10);
  --t:#e8e8e8;--t2:#8a8a8a;--t3:#525252;
  --ac:#07c160;--bl:#5e6ad2;--rd:#e5484d;--yw:#ffc53d;
  --r:6px;--f:"Inter",-apple-system,"PingFang SC",sans-serif;
}
html,body{height:100%;overflow:hidden}
body{font:13px/1.5 var(--f);background:var(--bg);color:var(--t);display:flex;flex-direction:column;-webkit-font-smoothing:antialiased}

/* topbar */
.tb{height:48px;display:flex;align-items:center;gap:8px;padding:0 16px;background:var(--bg2);border-bottom:1px solid var(--br);flex-shrink:0;z-index:100}
.tb-back{display:flex;align-items:center;gap:5px;color:var(--t2);text-decoration:none;font-size:12px;padding:0 8px;height:28px;border-radius:var(--r);border:1px solid var(--br);cursor:pointer;white-space:nowrap;flex-shrink:0;background:transparent;font-family:var(--f)}
.tb-back:hover{border-color:var(--br2);color:var(--t)}
.tb-sep{width:1px;height:16px;background:var(--br);flex-shrink:0}
.tb-crumb{font-size:11px;color:var(--t3);white-space:nowrap;flex-shrink:0}
.tb-crumb a{color:var(--t3);text-decoration:none}.tb-crumb a:hover{color:var(--t2)}
.tb-doc{flex:1;min-width:0;font-size:13px;color:var(--t2);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tb-saved{font-size:11px;color:var(--t3);white-space:nowrap;flex-shrink:0}
.tb-saved.saving{color:var(--yw)}.tb-saved.saved{color:var(--ac)}
.btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;height:30px;padding:0 12px;border-radius:var(--r);font-size:12px;font-weight:500;font-family:var(--f);cursor:pointer;white-space:nowrap;flex-shrink:0;text-decoration:none;border:1px solid transparent;transition:opacity .12s,border-color .12s}
.btn:disabled{opacity:.35;pointer-events:none}
.btn-ghost{background:transparent;border-color:var(--br);color:var(--t2)}.btn-ghost:hover{border-color:var(--br2);color:var(--t)}
.btn-fill{background:var(--ac);color:#fff}.btn-fill:hover{opacity:.88}
.btn-sm{height:26px;padding:0 10px;font-size:11px}

/* layout */
.layout{flex:1;display:flex;overflow:hidden}

/* sidebar */
.sb{width:260px;min-width:260px;background:var(--bg2);border-right:1px solid var(--br);display:flex;flex-direction:column;overflow-y:auto;flex-shrink:0}
.sb::-webkit-scrollbar{width:4px}.sb::-webkit-scrollbar-thumb{background:var(--br2);border-radius:2px}
.sb-sec{padding:14px 16px;border-bottom:1px solid var(--br)}
.sb-lbl{font-size:10px;font-weight:600;letter-spacing:.07em;text-transform:uppercase;color:var(--t3);margin-bottom:10px}
.sb-link{display:inline-flex;align-items:center;gap:4px;height:28px;padding:0 10px;border-radius:var(--r);font-size:11px;color:var(--t2);text-decoration:none;border:1px solid var(--br);background:transparent;transition:border-color .12s,color .12s;cursor:pointer}
.sb-link:hover{border-color:var(--br2);color:var(--t)}
.sb-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sb-dg{background:var(--ac)}.sb-dy{background:var(--yw)}.sb-db{background:var(--bl)}.sb-dm{background:var(--t3)}
.sb-sel{background:var(--bg3);border:1px solid var(--br);border-radius:var(--r);color:var(--t);font-size:11px;font-family:var(--f);padding:5px 9px;outline:none;cursor:pointer;width:100%;margin-top:8px;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6'%3E%3Cpath fill='%23525252' d='M5 6L0 0h10z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 8px center;padding-right:24px}
.sb-sel:focus{border-color:var(--br2)}
.sb-cover{aspect-ratio:3/2;background:var(--bg3);border:1px solid var(--br);border-radius:var(--r);overflow:hidden;position:relative;cursor:pointer}
.sb-cover img{width:100%;height:100%;object-fit:cover;display:block}
.sb-cover-ph{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:6px;height:100%;color:var(--t3);font-size:11px}
.sb-cover-ov{position:absolute;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;font-size:11px;color:#fff;opacity:0;transition:opacity .15s}
.sb-cover:hover .sb-cover-ov{opacity:1}
.sb-themes{display:flex;flex-direction:column;gap:2px}
.sb-theme{display:flex;align-items:center;gap:8px;padding:7px 10px;border-radius:var(--r);cursor:pointer;position:relative;transition:background .1s}
.sb-theme:hover{background:var(--hv)}
.sb-theme.on{background:var(--bg3)}
.sb-theme.on::before{content:'';position:absolute;left:0;top:6px;bottom:6px;width:3px;background:var(--ac);border-radius:0 2px 2px 0}
.sb-theme-dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.sb-theme-name{font-size:12px;color:var(--t2)}.sb-theme.on .sb-theme-name{color:var(--t)}
.sb-theme-desc{font-size:10px;color:var(--t3);margin-left:auto}
.sb-imgs{display:grid;grid-template-columns:1fr 1fr;gap:6px;max-height:200px;overflow-y:auto}
.sb-img{aspect-ratio:1;background:var(--bg3);border:1px solid var(--br);border-radius:var(--r);overflow:hidden;position:relative;cursor:pointer}
.sb-img img{width:100%;height:100%;object-fit:cover;display:block}
.sb-img-ov{position:absolute;inset:0;background:rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;font-size:18px;color:#fff;opacity:0;transition:opacity .12s}
.sb-img:hover .sb-img-ov{opacity:1}
.sb-bottom{margin-top:auto;padding:14px 16px 20px;border-top:1px solid var(--br);display:flex;flex-direction:column;gap:8px}
.sb-meta{font-size:10px;color:var(--t3);line-height:1.5}

/* editor */
.ed-area{flex:1;display:flex;flex-direction:column;overflow:hidden;background:#fafafa}
.ed-scroll{flex:1;overflow-y:auto;padding:40px 48px 80px}
.ed-scroll::-webkit-scrollbar{width:6px}.ed-scroll::-webkit-scrollbar-thumb{background:#ddd;border-radius:3px}
.ed-title-wrap{display:flex;align-items:flex-start;gap:12px;margin-bottom:24px;padding-bottom:20px;border-bottom:1px solid #e8e8e8}
.ed-title{flex:1;border:none;background:transparent;color:#111;font-size:24px;font-weight:700;font-family:var(--f);line-height:1.35;outline:none;resize:none;overflow:hidden}
.ed-title::placeholder{color:#ccc}
.ed-cnt{font-size:11px;color:#bbb;padding-top:8px;flex-shrink:0;white-space:nowrap}
.ed-cnt.w{color:#f76b15}
#wxEditorjs{min-height:360px;font-family:var(--f);color:#1a1a1a}
#wxEditorjs .ce-block__content{max-width:none}
#wxEditorjs .codex-editor__redactor{padding-bottom:40px!important}

/* preview drawer */
.preview-drawer{position:fixed;top:48px;right:-520px;width:520px;bottom:0;background:#fff;border-left:1px solid #e5e7eb;z-index:300;transition:right .25s ease;display:flex;flex-direction:column;overflow:hidden;box-shadow:-4px 0 20px rgba(0,0,0,.1)}
.preview-drawer.open{right:0}
.preview-drawer-hdr{padding:12px 16px;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;gap:10px;flex-shrink:0;background:#fff}
.preview-drawer-title{font-size:13px;font-weight:600;color:#1a1a2e}
.preview-drawer-close{background:none;border:none;cursor:pointer;color:#9ca3af;font-size:16px;padding:2px 6px;border-radius:6px;margin-left:auto}
.preview-drawer-close:hover{color:#1a1a2e;background:#f3f4f6}
.preview-drawer-body{flex:1;overflow-y:auto;background:#f0f0f0}
.preview-drawer-body iframe{width:100%;height:100%;border:none}
/* orig drawer */
.orig-drawer{position:fixed;top:48px;right:-440px;width:440px;bottom:0;background:#fff;border-left:1px solid #e5e7eb;z-index:200;transition:right .25s ease;display:flex;flex-direction:column;overflow:hidden;box-shadow:-4px 0 20px rgba(0,0,0,.08)}
.orig-drawer.open{right:0}
.orig-drawer-hdr{padding:12px 16px;border-bottom:1px solid #e5e7eb;display:flex;align-items:center;gap:10px;flex-shrink:0;background:#fff}
.orig-drawer-title{font-size:13px;font-weight:600;color:#1a1a2e}
.orig-drawer-close{background:none;border:none;cursor:pointer;color:#9ca3af;font-size:16px;padding:2px 6px;border-radius:6px;margin-left:auto}
.orig-drawer-close:hover{color:#1a1a2e;background:#f3f4f6}
.orig-drawer-body{flex:1;overflow-y:auto;padding:16px;font-size:12.5px;line-height:1.8;color:#374151;white-space:pre-wrap;background:#fafafa}
.orig-drawer-body::-webkit-scrollbar{width:4px}
.orig-drawer-body::-webkit-scrollbar-thumb{background:#e5e7eb;border-radius:2px}
/* toast */
.wx-toast{position:fixed;bottom:24px;right:24px;background:#1c1c20;border:1px solid var(--br2);color:var(--t);padding:10px 16px;border-radius:var(--r);font-size:12px;font-weight:500;z-index:9999;opacity:0;transform:translateY(8px);transition:opacity .2s,transform .2s;pointer-events:none}
.wx-toast.show{opacity:1;transform:none}
.wx-toast.ok{border-left:3px solid var(--ac)}.wx-toast.err{border-left:3px solid var(--rd)}
</style>
</head>
<body>
<div class="wx-toast" id="wxToast"></div>
<div class="tb">
  <a class="tb-back" href="javascript:history.back()">← 返回</a>
  <div class="tb-sep"></div>
  <span class="tb-crumb"><a href="/">文章列表</a> / <a href="/detail/{{news.key}}">详情</a> / 公众号</span>
  <div class="tb-sep"></div>
  <div class="tb-doc" id="tbDoc">{{news.wechat_title or news.title or '(无标题)'}}</div>
  <span class="tb-saved" id="tbSaved">已保存</span>
  <button class="btn btn-ghost btn-sm" onclick="toggleOrigDrawer()">原文参考</button>
  <button class="btn btn-ghost btn-sm" onclick="togglePreviewDrawer()">预览</button>
  <button class="btn btn-ghost btn-sm" onclick="doSave()">保存</button>
  <button class="btn btn-fill btn-sm" id="btnPush" onclick="doPush()">推送草稿</button>
</div>
<div class="layout">
  <div class="sb">
    <div class="sb-sec"><div class="sb-lbl">链接</div>
      <div style="display:flex;gap:6px;flex-wrap:wrap">
        <a class="sb-link" href="/detail/{{news.key}}" target="_blank">📄 文章详情</a>
        {% if news.link %}<a class="sb-link" href="{{news.link}}" target="_blank">🔗 日文原文</a>{% endif %}
      </div>
    </div>
    <div class="sb-sec"><div class="sb-lbl">发布状态</div>
      <div style="display:flex;align-items:center;gap:8px" id="sbStRow">
        <span class="sb-dot" id="sbStDot"></span>
        <span style="font-size:12px;color:var(--t2)" id="sbStTxt">—</span>
      </div>
      <select class="sb-sel" id="sbStSel" onchange="onStChange(this.value)">
        <option value="0" {{'selected' if not news.wechat_publish else ''}}>不发布</option>
        <option value="1" {{'selected' if news.wechat_publish else ''}}>标记待发布</option>
      </select>
      {% if news.wechat_pub_time %}<div style="font-size:10px;color:var(--t3);margin-top:6px">发布: {{news.wechat_pub_time[:16]}}</div>{% endif %}
      {% if news.wechat_draft_id %}<div style="font-size:10px;color:var(--t3);margin-top:3px;word-break:break-all">ID: {{news.wechat_draft_id[:24]}}…</div>{% endif %}
    </div>
    <div class="sb-sec"><div class="sb-lbl">封面图</div>
      <div class="sb-cover" onclick="document.getElementById('cvFile').click()">
        {% if news.image_url %}<img id="sbCvImg" src="{{'/local-image?path='+news.image_url if news.image_url.startswith('/') else news.image_url}}">
        {% else %}<div class="sb-cover-ph"><span style="font-size:22px">🖼</span><span>点击选择封面</span></div><img id="sbCvImg" src="" style="display:none">{% endif %}
        <div class="sb-cover-ov">更换封面</div>
      </div>
      <input type="file" id="cvFile" accept="image/*" style="display:none" onchange="onCvChange(this)">
    </div>
    <div class="sb-sec"><div class="sb-lbl">排版主题</div>
      <div class="sb-themes" id="sbThemes">
        <div class="sb-theme on" data-t="sports" onclick="selTheme('sports')"><span class="sb-theme-dot" style="background:#FA5151"></span><span class="sb-theme-name">活力橘</span><span class="sb-theme-desc">偶像/娱乐</span></div>
        <div class="sb-theme" data-t="newspaper" onclick="selTheme('newspaper')"><span class="sb-theme-dot" style="background:#0F4C81"></span><span class="sb-theme-name">新闻蓝</span><span class="sb-theme-desc">权威感</span></div>
        <div class="sb-theme" data-t="magazine" onclick="selTheme('magazine')"><span class="sb-theme-dot" style="background:#92617E"></span><span class="sb-theme-name">优雅紫</span><span class="sb-theme-desc">深度报道</span></div>
        <div class="sb-theme" data-t="minimal-gray" onclick="selTheme('minimal-gray')"><span class="sb-theme-dot" style="background:#333"></span><span class="sb-theme-name">简洁黑</span><span class="sb-theme-desc">严肃媒体</span></div>
      </div>
    </div>
    <div class="sb-sec" style="flex:1"><div class="sb-lbl">图片素材</div>
      <div class="sb-imgs" id="sbImgs">
        {% for img in all_images %}
        <div class="sb-img" onclick="insImg('{{img.path}}')" title="{{img.source}}">
          <img src="{{'/local-image?path='+img.path if img.path.startswith('/') else img.path}}" loading="lazy" onerror="this.parentElement.style.display='none'">
          <div class="sb-img-ov">＋</div>
        </div>
        {% else %}<div style="font-size:11px;color:var(--t3);padding:8px 0">暂无素材</div>{% endfor %}
      </div>
    </div>
    <div class="sb-bottom">
      <button class="btn btn-fill" style="width:100%;justify-content:center" id="btnPush2" onclick="doPush()">推送草稿到公众号</button>
      <div class="sb-meta" id="sbMeta">{{('草稿 ID: '+news.wechat_draft_id[:20]+'…') if news.wechat_draft_id else '尚未推送'}}</div>
    </div>
  </div>
  <div class="ed-area">
    <div class="ed-scroll">
      <div class="ed-title-wrap">
        <textarea class="ed-title" id="wxTitle" rows="1" placeholder="输入公众号标题..." oninput="onTitleIn(this)">{{news.wechat_title or news.title or ''}}</textarea>
        <span class="ed-cnt" id="wxCnt">0 字</span>
      </div>
      <div id="wxEditorjs"></div>
    </div>
  </div>
</div>
<!-- Preview drawer -->
<div class="preview-drawer" id="previewDrawer">
  <div class="preview-drawer-hdr">
    <span class="preview-drawer-title">👁 微信预览</span>
    <span id="previewThemeLbl" style="font-size:11px;color:#9ca3af"></span>
    <button class="preview-drawer-close" onclick="closePreviewDrawer()">✕</button>
  </div>
  <div class="preview-drawer-body">
    <iframe id="previewFrame" src="about:blank"></iframe>
  </div>
</div>

<!-- Orig drawer -->
<div class="orig-drawer" id="origDrawer">
  <div class="orig-drawer-hdr">
    <span class="orig-drawer-title">📄 原文参考</span>
    <button class="orig-drawer-close" onclick="toggleOrigDrawer()">✕</button>
  </div>
  <div class="orig-drawer-body">{{news.content or '（无原文）'}}</div>
</div>

<textarea id="wxHidden" style="display:none">{{news.wechat_content or news.content or ''}}</textarea>
<script>
var WK="{{news.key}}";
var _th='newspaper',_ed=null,_dirty=false,_stimer=null;
function _S(id){return document.getElementById(id);}
function _toast(msg,type){var e=_S('wxToast');e.textContent=msg;e.className='wx-toast show '+(type||'ok');clearTimeout(e._t);e._t=setTimeout(function(){e.className='wx-toast';},2200);}
function _setSave(s){var e=_S('tbSaved');if(!e)return;if(s==='saving'){e.textContent='保存中…';e.className='tb-saved saving';}else if(s==='saved'){e.textContent='已保存';e.className='tb-saved saved';}else{e.textContent='未保存';e.className='tb-saved';}}
function onTitleIn(el){
  el.style.height='auto';el.style.height=el.scrollHeight+'px';
  var n=el.value.length;var c=_S('wxCnt');if(c){c.textContent=n+' 字';c.className='ed-cnt'+(n>64?' w':'');}
  var d=_S('tbDoc');if(d)d.textContent=el.value||'(无标题)';
  _markDirty();
}
(function(){var e=_S('wxTitle');if(e){onTitleIn(e);}})();
function _initStDot(){
  var v=_S('sbStSel').value;
  var dot=_S('sbStDot'),txt=_S('sbStTxt');
  if(v==='1'&&{{1 if news.wechat_pub_time else 0}}){dot.className='sb-dot sb-dg';txt.textContent='已发布';}
  else if(v==='1'&&{{'1' if news.wechat_draft_id else '0'}}!=='0'){dot.className='sb-dot sb-dy';txt.textContent='草稿';}
  else if(v==='1'){dot.className='sb-dot sb-db';txt.textContent='待发布';}
  else{dot.className='sb-dot sb-dm';txt.textContent='未配置';}
}
_initStDot();
function onStChange(v){
  var dot=_S('sbStDot'),txt=_S('sbStTxt');
  if(v==='1'){dot.className='sb-dot sb-db';txt.textContent='待发布';}
  else{dot.className='sb-dot sb-dm';txt.textContent='未配置';}
  _markDirty();
}
function selTheme(name){
  _th=name;
  document.querySelectorAll('#sbThemes .sb-theme').forEach(function(e){e.classList.toggle('on',e.getAttribute('data-t')===name);});
}
function onCvChange(inp){
  if(!inp.files||!inp.files[0])return;
  var r=new FileReader();
  r.onload=function(e){var i=_S('sbCvImg');if(i){i.src=e.target.result;i.style.display='block';}};
  r.readAsDataURL(inp.files[0]);_markDirty();
}
function insImg(path){
  if(!_ed)return;
  var src=path.startsWith('/')?'/local-image?path='+encodeURIComponent(path):path;
  var idx=_ed.blocks.getCurrentBlockIndex();
  _ed.blocks.insert('image',{file:{url:src},caption:'',withBorder:false,stretched:false,withBackground:false},{},idx+1,true);
}
function _markDirty(){_dirty=true;_setSave('unsaved');clearTimeout(_stimer);_stimer=setTimeout(function(){if(_dirty)doSave(true);},4000);}
function _blocks2txt(blocks){
  var parts=[],n=1;
  (blocks||[]).forEach(function(b){
    if(b.type==='paragraph'&&b.data&&b.data.text)parts.push(b.data.text.replace(/<[^>]+>/g,''));
    else if(b.type==='header'&&b.data&&b.data.text)parts.push((b.data.level===3?'### ':'## ')+b.data.text.replace(/<[^>]+>/g,''));
    else if(b.type==='quote'&&b.data&&b.data.text)parts.push('> '+b.data.text.replace(/<[^>]+>/g,''));
    else if(b.type==='image'&&b.data&&b.data.file&&b.data.file.url){parts.push('【图片'+n+'：'+b.data.file.url+'】');n++;}
    else if(b.type==='galleryImage'&&b.data){var ps=(b.data.paths||[]).filter(function(p){return !!p;});if(ps.length){parts.push('【图片'+n+'：'+ps.join('|')+'】');n++;}}
  });
  return parts.filter(function(s){return s&&s.trim();}).join('\n\n');
}
function _txt2blocks(txt){
  var blocks=[],last=0,n=0;
  var re=/【(?:图片|推文)\d+：([^】]*)】/g;
  function addText(t){
    if(!t||!t.trim())return;
    // Split on single newlines too, like detail page
    t.split('\n').forEach(function(line){
      var p=line.trim();if(!p)return;
      if(p.indexOf('### ')===0)blocks.push({type:'header',data:{text:p.slice(4),level:3}});
      else if(p.indexOf('## ')===0)blocks.push({type:'header',data:{text:p.slice(3),level:2}});
      else if(p.indexOf('> ')===0)blocks.push({type:'quote',data:{text:p.slice(2),caption:''}});
      else blocks.push({type:'paragraph',data:{text:p}});
    });
  }
  var m;re.lastIndex=0;
  while((m=re.exec(txt))!==null){
    addText(txt.slice(last,m.index));
    var ps=m[1].split('|').filter(function(p){return p.trim();});
    if(ps.length===1&&(ps[0].indexOf('http')===0||ps[0].indexOf('/')===0))
      blocks.push({type:'image',data:{file:{url:ps[0]},caption:'',withBorder:false,stretched:false,withBackground:false}});
    else if(ps.length>0)blocks.push({type:'galleryImage',data:{paths:ps,caption:m[0]}});
    last=m.index+m[0].length;n++;
  }
  addText(txt.slice(last));
  if(!blocks.length)blocks.push({type:'paragraph',data:{text:txt}});
  return blocks;
}
var WxGalleryBlock=(function(){
  function C(o){this.api=o.api;this.data={paths:(o.data&&o.data.paths)||[],caption:(o.data&&o.data.caption)||''};}
  C.toolbox={title:'图片组',icon:'🖼'};C.isReadOnlySupported=true;
  C.prototype.render=function(){
    var w=document.createElement('div');w.style.cssText='border:1px dashed rgba(0,0,0,.12);border-radius:6px;padding:8px;margin:4px 0;background:#f8f8f8';
    var ps=this.data.paths||[];
    if(ps.length){var g=document.createElement('div');g.style.cssText='display:flex;gap:6px;flex-wrap:wrap';
      ps.forEach(function(p){var i=document.createElement('img');i.src=p.indexOf('/')===0?'/local-image?path='+encodeURIComponent(p):p;i.style.cssText='height:80px;border-radius:4px;object-fit:cover';g.appendChild(i);});w.appendChild(g);}
    else{w.textContent='(图片占位)';w.style.color='#aaa';w.style.fontSize='12px';}
    this._el=w;return w;
  };
  C.prototype.save=function(){return{paths:this.data.paths||[],caption:this.data.caption||''};};
  return C;
})();
function _initEd(){
  if(typeof EditorJS==='undefined')return;
  var raw=(_S('wxHidden')||{}).value||'';
  var blocks=_txt2blocks(raw);
  _ed=new EditorJS({
    holder:'wxEditorjs',minHeight:200,
    placeholder:'开始写公众号正文... (/ 插入标题或图片)',
    tools:{
      header:{class:Header,config:{levels:[2,3],defaultLevel:2},inlineToolbar:true},
      quote:{class:Quote,inlineToolbar:true,config:{quotePlaceholder:'引用内容',captionPlaceholder:'出处（可选）'}},
      galleryImage:{class:WxGalleryBlock}
    },
    data:{blocks:blocks},
    onChange:function(){
      _ed.save().then(function(o){
        if(_S('wxHidden'))_S('wxHidden').value=_blocks2txt(o.blocks||[]);
        var cnt=_S('wxCnt'); if(cnt){var n=(_S('wxHidden').value||'').length;cnt.textContent=n+' 字';}
        _markDirty();
      }).catch(function(){});
    },
    onReady:function(){
      var raw2=(_S('wxHidden')||{}).value||'';
      var n=raw2.length;var c=_S('wxCnt');if(c)c.textContent=n+' 字';
    }
  });
}
(function(){
  function L(s,cb){var sc=document.createElement('script');sc.src=s;sc.onload=cb;sc.onerror=function(){cb();};document.head.appendChild(sc);}
  L('https://cdn.jsdelivr.net/npm/@editorjs/editorjs@2.29.1/dist/editorjs.umd.min.js',function(){
    L('https://cdn.jsdelivr.net/npm/@editorjs/header@2.8.1/dist/header.umd.min.js',function(){
      L('https://cdn.jsdelivr.net/npm/@editorjs/quote@2.6.0/dist/quote.umd.min.js',function(){
        L('https://cdn.jsdelivr.net/npm/@editorjs/image@2.10.3/dist/image.umd.js',function(){
          _initEd();
        });
      });
    });
  });
})();
async function doSave(silent){
  _setSave('saving');
  var title=(_S('wxTitle')||{}).value||'';
  var publish=parseInt((_S('sbStSel')||{}).value)||0;
  var content=(_S('wxHidden')||{}).value||'';
  if(_ed){try{var o=await _ed.save();content=_blocks2txt(o.blocks||[]);}catch(e){}}
  try{
    var r=await fetch('/api/wechat/'+WK,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({wechat_title:title,wechat_publish:publish,wechat_content:content})});
    if(r.ok){_dirty=false;_setSave('saved');if(!silent)_toast('已保存','ok');}
    else{_setSave('');if(!silent)_toast('保存失败','err');}
  }catch(e){_setSave('');if(!silent)_toast('网络错误','err');}
}
function doPreview(){
  var drawer=document.getElementById('previewDrawer');
  var frame=document.getElementById('previewFrame');
  var lbl=document.getElementById('previewThemeLbl');
  if(lbl) lbl.textContent='主题：'+_th;
  if(drawer) drawer.classList.add('open');
  if(!frame) return;
  frame.srcdoc='<div style="padding:20px;font-size:13px;color:#888">加载中...</div>';
  fetch('/api/wechat/'+WK+'/preview?theme='+_th)
    .then(function(r){return r.text();})
    .then(function(html){frame.srcdoc=html;})
    .catch(function(e){frame.srcdoc='<div style="padding:20px;color:red">预览失败: '+e.message+'</div>';});
}
function closePreviewDrawer(){
  var drawer=document.getElementById('previewDrawer');
  if(drawer) drawer.classList.remove('open');
}
async function doPush(){
  ['btnPush','btnPush2'].forEach(function(id){var b=_S(id);if(b){b.disabled=true;b.textContent='推送中…';}});
  try{
    await doSave(true);
    var r=await fetch('/api/wechat/'+WK+'/publish?theme='+_th,{method:'POST'});
    var d=await r.json();
    if(r.ok&&d.ok){_toast('已推送到草稿箱','ok');}
    else{_toast((d.error||'推送失败'),'err');}
  }catch(e){_toast('推送失败','err');}
  ['btnPush','btnPush2'].forEach(function(id){var b=_S(id);if(b){b.disabled=false;b.textContent=id==='btnPush'?'推送草稿':'推送草稿到公众号';}});
}
function toggleOrigDrawer(){
  var d=document.getElementById('origDrawer');
  if(!d) return;
  var willOpen = !d.classList.contains('open');
  if(willOpen) {
    var pd=document.getElementById('previewDrawer');
    if(pd) pd.classList.remove('open');
  }
  d.classList.toggle('open');
}
function togglePreviewDrawer(){
  var d=document.getElementById('previewDrawer');
  if(!d) return;
  if(d.classList.contains('open')){
    d.classList.remove('open');
  } else {
    var od=document.getElementById('origDrawer');
    if(od) od.classList.remove('open');
    doPreview();
  }
}
document.addEventListener('keydown',function(e){if((e.ctrlKey||e.metaKey)&&e.key==='s'){e.preventDefault();doSave(false);}});
</script>
</body>
</html>"""



@wechat_bp.route('/api/wechat-list')
def api_wechat_list():
    from sqlite_db import _connect
    with _connect() as db:
        rows = [dict(r) for r in db.execute(
            "SELECT key,title,wechat_title,wechat_publish,wechat_draft_id,wechat_pub_time,updated_at,created_at,image_url "
            "FROM news WHERE status='active' AND (wechat_content!='' OR wechat_publish=1 OR wechat_draft_id!='') "
            "ORDER BY updated_at DESC LIMIT 200"
        ).fetchall()]
    return jsonify({"items": rows})

@wechat_bp.route('/wechat-list')
def wechat_list():
    from flask import render_template_string as rts
    from sqlite_db import _connect
    search = request.args.get('search', '')
    with _connect() as db:
        sql = "SELECT * FROM news WHERE status='active' AND (wechat_content!='' OR wechat_publish=1 OR wechat_draft_id!='')"
        params = []
        if search:
            sql += " AND (wechat_title LIKE ? OR title LIKE ?)"
            params.extend([f'%{search}%', f'%{search}%'])
        sql += " ORDER BY created_at DESC LIMIT 200"
        rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    return rts(WECHAT_LIST_HTML, rows=rows, search=search)

WECHAT_LIST_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>公众号 · 文章管理</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#f4f4f8;--card-bg:#fff;--sidebar-bg:#16162a;
  --text:#1a1a2e;--text2:#6b7280;--text3:#9ca3af;
  --border:#e5e7eb;--radius:10px;--shadow:0 1px 4px rgba(0,0,0,.07);
  --blue:#3b82f6;--green:#10b981;--orange:#f59e0b;--red:#ef4444;
  --wx:#07c160;--font:-apple-system,"PingFang SC","Microsoft YaHei",sans-serif;
}
body{font-family:var(--font);font-size:13px;color:var(--text);background:var(--bg);display:flex;height:100vh;overflow:hidden}

/* sidebar */
.sidebar{width:200px;min-width:200px;background:var(--sidebar-bg);display:flex;flex-direction:column;padding:16px 0;overflow-y:auto}
.sidebar-logo{display:flex;align-items:center;gap:10px;padding:4px 16px 18px;border-bottom:1px solid rgba(255,255,255,.06);margin-bottom:10px}
.sidebar-logo-icon{width:28px;height:28px;border-radius:7px;background:linear-gradient(135deg,#07c160,#1aad19);display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0}
.sidebar-logo-text{font-size:13px;font-weight:600;color:#fff}
.nav-section-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:#7070a0;padding:6px 16px 4px}
.nav-item{display:flex;align-items:center;gap:9px;padding:7px 14px;border-radius:6px;margin:1px 8px;cursor:pointer;color:#c0c0d8;font-size:12px;font-weight:500;transition:all .15s;text-decoration:none}
.nav-item:hover{background:rgba(255,255,255,.08);color:#ffffff}
.nav-item.active{background:rgba(7,193,96,.12);color:#07c160}
.nav-item .ni{font-size:14px;width:18px;text-align:center;flex-shrink:0}

/* main */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.topbar{background:var(--card-bg);border-bottom:1px solid var(--border);padding:0 20px;height:50px;display:flex;align-items:center;gap:12px;flex-shrink:0}
.topbar-title{font-size:13px;font-weight:600;color:var(--text)}
.content{flex:1;overflow-y:auto;padding:18px 20px}

/* stats */
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:18px}
.stat-card{background:var(--card-bg);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow);border:1px solid var(--border);display:flex;align-items:center;gap:12px}
.stat-icon{width:34px;height:34px;border-radius:8px;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}
.stat-num{font-size:20px;font-weight:700;line-height:1;color:var(--text)}
.stat-label{font-size:11px;color:var(--text3);margin-top:2px}

/* toolbar */
.toolbar{display:flex;align-items:center;gap:8px;margin-bottom:14px}
.search-box{display:flex;align-items:center;gap:7px;background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:5px 11px;width:220px}
.search-box input{border:none;background:none;font-size:12px;color:var(--text);outline:none;width:100%}
.btn{display:inline-flex;align-items:center;gap:5px;height:30px;padding:0 13px;border:none;border-radius:7px;cursor:pointer;font-size:11.5px;font-weight:500;white-space:nowrap;transition:all .12s;border:1px solid transparent}
.btn-wx{background:var(--wx);color:#fff}
.btn-gray{background:#f3f4f6;color:var(--text2);border-color:var(--border)}

/* table */
.table-card{background:var(--card-bg);border-radius:var(--radius);box-shadow:var(--shadow);border:1px solid var(--border);overflow:hidden}
.table-header{display:grid;grid-template-columns:1fr 90px 100px 80px;padding:8px 16px;background:#f9fafb;border-bottom:1px solid var(--border)}
.table-header span{font-size:10.5px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.04em}
.table-row{display:grid;grid-template-columns:1fr 90px 100px 80px;padding:10px 16px;border-bottom:1px solid var(--border);align-items:center;transition:background .1s}
.table-row:last-child{border-bottom:none}
.table-row:hover{background:#f9fafb}
.row-title{font-size:12.5px;font-weight:500;color:var(--text);text-decoration:none;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row-title:hover{color:var(--wx)}
.row-date{font-size:11px;color:var(--text3)}
.badge{display:inline-flex;align-items:center;gap:4px;font-size:10.5px;padding:2px 7px;border-radius:20px;font-weight:500;white-space:nowrap}
.badge-draft{background:#fef3c7;color:#92400e}
.badge-published{background:#d1fae5;color:#065f46}
.badge-pending{background:#dbeafe;color:#1d4ed8}
.badge-none{background:#f3f4f6;color:#9ca3af}
.act-btn{font-size:11px;color:var(--wx);text-decoration:none;padding:3px 8px;border-radius:5px;border:1px solid rgba(7,193,96,.3);transition:all .15s}
.act-btn:hover{background:rgba(7,193,96,.08)}
.empty{padding:60px 20px;text-align:center;color:var(--text3)}
.empty-icon{font-size:36px;margin-bottom:12px}
</style>
</head>
<body>

<div class="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon">💬</div>
    <span class="sidebar-logo-text">公众号</span>
  </div>
  <div class="nav-section-label">管理</div>
  <a href="/wechat-list" class="nav-item active"><span class="ni">📄</span>文章列表</a>
  <a href="/" class="nav-item"><span class="ni">←</span>返回主管理</a>
</div>

<div class="main">
  <div class="topbar">
    <span class="topbar-title">公众号文章管理</span>
    <span style="flex:1"></span>
    <a href="/" class="btn btn-gray" style="text-decoration:none;font-size:11px">← 返回</a>
  </div>
  <div class="content">

    <!-- stats -->
    <div class="stats-row">
      <div class="stat-card">
        <div class="stat-icon" style="background:#f0fdf4">📄</div>
        <div><div class="stat-num">{{rows|length}}</div><div class="stat-label">文章总数</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background:#fef3c7">📝</div>
        <div><div class="stat-num">{{rows|selectattr('wechat_draft_id')|list|length}}</div><div class="stat-label">草稿箱</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background:#dbeafe">⏳</div>
        <div><div class="stat-num">{{rows|selectattr('wechat_publish')|rejectattr('wechat_draft_id')|list|length}}</div><div class="stat-label">待发布</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background:#d1fae5">✅</div>
        <div><div class="stat-num">{{rows|selectattr('wechat_pub_time')|list|length}}</div><div class="stat-label">已发布</div></div>
      </div>
    </div>

    <!-- toolbar -->
    <div class="toolbar">
      <div class="search-box">
        <span style="color:var(--text3);font-size:13px">🔍</span>
        <input id="searchInput" placeholder="搜索文章..." value="{{search or ''}}"
          oninput="clearTimeout(_t);_t=setTimeout(()=>{const u=new URL(location);u.searchParams.set('search',this.value);location=u.toString()},400)">
      </div>
      <span style="flex:1"></span>
      <span style="font-size:11px;color:var(--text3)">共 {{rows|length}} 篇</span>
    </div>

    <!-- table -->
    <div class="table-card">
      {% if rows %}
      <div class="table-header">
        <span>标题</span>
        <span>状态</span>
        <span>更新时间</span>
        <span></span>
      </div>
      {% for r in rows %}
      <div class="table-row">
        <a href="/wechat/{{r.key}}" class="row-title">{{r.wechat_title or r.title or r.key[:16]}}</a>
        <div>
          {% if r.wechat_pub_time %}<span class="badge badge-published">✅ 已发布</span>
          {% elif r.wechat_draft_id %}<span class="badge badge-draft">📝 草稿箱</span>
          {% elif r.wechat_publish %}<span class="badge badge-pending">⏳ 待发布</span>
          {% else %}<span class="badge badge-none">— 未配置</span>{% endif %}
        </div>
        <span class="row-date">{{(r.updated_at or r.created_at or '')[:10]}}</span>
        <a href="/wechat/{{r.key}}" class="act-btn">编辑 →</a>
      </div>
      {% endfor %}
      {% else %}
      <div class="empty">
        <div class="empty-icon">💬</div>
        <div style="font-size:14px;font-weight:500;color:var(--text2);margin-bottom:6px">暂无公众号文章</div>
        <div style="font-size:12px">在文章详情页点击「公众号」入口配置内容</div>
      </div>
      {% endif %}
    </div>

  </div>
</div>
</body>
</html>
"""

@wechat_bp.route('/wechat/<key>')
def wechat_editor(key):
    import json as _json
    news = get_by_key(key)
    if not news:
        return "Not found", 404
    # Parse gallery
    gi = news.get('gallery_images', '')
    news['gallery_images'] = _json.loads(gi) if isinstance(gi, str) and gi else (gi or [])
    # Collect images: gallery_images + cached images + related keys
    rk_raw = news.get('related_keys', '') or ''
    all_images = []
    seen = set()

    def _add_imgs(paths, source):
        for p in (paths or []):
            if p and p not in seen:
                seen.add(p)
                all_images.append({'path': p, 'source': source})

    # 1. Current article gallery + cached images
    _add_imgs(news['gallery_images'], '本文')
    try:
        from config.yahoo_conf import GALLERY_CACHE_DIR
        import glob as _glob
        cache_dir = os.path.join(os.path.expanduser(GALLERY_CACHE_DIR), key)
        if os.path.isdir(cache_dir):
            for f in sorted(_glob.glob(os.path.join(cache_dir, '*.jpg')) +
                           _glob.glob(os.path.join(cache_dir, '*.webp')) +
                           _glob.glob(os.path.join(cache_dir, '*.png'))):
                if 'cover' not in os.path.basename(f):
                    _add_imgs([f], '本文')
        cover = os.path.join(cache_dir, 'cover.jpg')
        if os.path.isfile(cover):
            _add_imgs([cover], '封面')
    except Exception:
        pass

    # 2. Cover image from news.image_url
    if news.get('image_url'):
        _add_imgs([news['image_url']], '封面')

    # 3. Related keys
    for rk in rk_raw.split(','):
        rk = rk.strip()
        if not rk: continue
        rr = get_by_key(rk)
        if not rr: continue
        title_short = (rr.get('title', '') or rk)[:12]
        gi2 = rr.get('gallery_images', '')
        gi2_list = _json.loads(gi2) if isinstance(gi2, str) and gi2 else (gi2 or [])
        _add_imgs(gi2_list, title_short)
        try:
            from config.yahoo_conf import GALLERY_CACHE_DIR
            import glob as _glob
            c2 = os.path.join(os.path.expanduser(GALLERY_CACHE_DIR), rk)
            if os.path.isdir(c2):
                for f in sorted(_glob.glob(os.path.join(c2, '*.jpg')) +
                               _glob.glob(os.path.join(c2, '*.webp'))):
                    _add_imgs([f], title_short)
        except Exception:
            pass

    return rts(WECHAT_HTML, news=news, all_images=all_images)

@wechat_bp.route('/api/wechat/<key>/preview')
def api_wechat_preview(key):
    theme = request.args.get('theme', 'newspaper')
    news = get_by_key(key)
    if not news: return 'Not found', 404
    import sys as _sys
    _sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
    try:
        from wechat_publisher import _render_html_preview
        content = news.get('wechat_content') or news.get('content') or ''
        title   = news.get('wechat_title')  or news.get('title', '')
        html = _render_html_preview(content, title, theme)
        return html, 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return f'<pre>预览渲染失败: {e}</pre>', 500

@wechat_bp.route('/api/wechat/<key>', methods=['PUT'])
def api_wechat_update(key):
    data = request.get_json()
    allowed = {'wechat_title','wechat_content','wechat_publish'}
    updates = {k: v for k, v in data.items() if k in allowed}
    if updates:
        update_news(key, updates)
    return jsonify({"ok": True})

@wechat_bp.route('/api/wechat/<key>/publish', methods=['POST'])
def api_wechat_publish(key):
    import subprocess, os
    scripts_dir = os.path.join(os.path.dirname(__file__), '..', 'scripts')
    result = subprocess.run(
        [sys.executable, 'wechat_publisher.py', '--key', key],
        capture_output=True, text=True, timeout=120,
        cwd=scripts_dir,
        env={**os.environ, 'PYTHONPATH': scripts_dir}
    )
    if result.returncode == 0:
        # Extract media_id from output
        import re
        m = re.search(r'media_id\s*=\s*(\S+)', result.stdout)
        if m:
            update_news(key, {'wechat_draft_id': m.group(1)})
        return jsonify({"ok": True, "output": result.stdout[-500:]})
    return jsonify({"ok": False, "error": result.stderr[-500:]})

