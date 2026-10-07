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
.sb-img-cv{position:absolute;left:0;right:0;bottom:0;background:rgba(0,0,0,.62);color:#fff;font-size:10px;text-align:center;padding:3px 0;opacity:0;transition:opacity .12s}
.sb-img:hover .sb-img-cv{opacity:1}
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
/* image 块：换图 / 删除控件（沿用官方 ImageTool，仅补 UI） */
#wxEditorjs .image-tool__image{position:relative;max-width:320px;margin:0 auto}
/* 官方 image 块的 <img> 没有任何尺寸约束，竖图会撑满正文宽度（实测 997x1329）。
   按 GalleryImageBlock 的同样标准限高，object-fit:contain 保证不变形。 */
#wxEditorjs .image-tool__image-picture{max-width:100%;max-height:320px;object-fit:contain;display:block;margin:0 auto}
#wxEditorjs .wx-img-ctl{display:none;position:absolute;top:6px;right:6px;gap:6px;z-index:6}
#wxEditorjs .image-tool--filled .wx-img-ctl{display:flex}
#wxEditorjs .wx-img-ctl button{font-size:11px;padding:3px 10px;border:0;border-radius:12px;background:rgba(0,0,0,.55);color:#fff;cursor:pointer;line-height:1.4}
#wxEditorjs .wx-img-ctl button:hover{background:rgba(0,0,0,.78)}

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
        {% set _cv = news.wechat_image_url or news.image_url %}
        {% if _cv %}<img id="sbCvImg" src="{{'/local-image?path='+_cv if _cv.startswith('/') else _cv}}">
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
      <button class="btn btn-ghost btn-sm" id="btnDlAll" onclick="dlAllGalleries()"
              style="width:100%;justify-content:center;margin-bottom:8px"
              title="抓取本篇 + 关联文章的图集（与详情页「重新下载」同一机制）">📥 下载全部</button>
      <div id="dlAllLog" style="display:none;font-size:10px;color:var(--t3);padding:0 0 8px;white-space:pre-wrap;font-family:Menlo,monospace;line-height:1.5"></div>
      <div class="sb-imgs" id="sbImgs">
        {% for img in all_images %}
        <div class="sb-img" onclick="insImg('{{img.path}}')" title="{{img.source}}">
          <img src="{{'/local-image?path='+img.path if img.path.startswith('/') else img.path}}" loading="lazy" onerror="this.parentElement.style.display='none'">
          <div class="sb-img-ov">＋</div>
          <div class="sb-img-cv" onclick="event.stopPropagation();setCover('{{img.path}}')" title="设为封面">设为封面</div>
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
<div id="wxImgPicker" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#fff;border:1px solid #ddd;border-radius:10px;padding:14px;z-index:8000;box-shadow:0 8px 32px rgba(0,0,0,.25);width:360px;max-height:70vh;overflow-y:auto">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
    <span style="font-size:13px;font-weight:600">选择图片插入</span>
    <button onclick="closeImgPicker()" style="background:none;border:none;font-size:16px;cursor:pointer">✕</button>
  </div>
  <div id="wxImgPickerGrid" style="display:flex;flex-wrap:wrap;gap:6px"></div>
</div>
<script>
var WK="{{news.key}}";
var _th='newspaper',_ed=null,_dirty=false,_stimer=null;
var _allImgs={{all_images|map(attribute='path')|list|tojson}};
var RELATED_KEYS={{related_articles|map(attribute='key')|list|tojson}};
var HAS_ANY_IMG={{1 if all_images else 0}};
// 筛选/排序统一走 URL 参数（服务端渲染），空值则删掉该参数
// 点表头排序：同一列再点一次切升/降序（与主新闻列表 setSort 同一套行为）
function sortBy(col){
  const u=new URL(location);
  const cur=u.searchParams.get('sort')||'created_at';
  const curDir=u.searchParams.get('dir')||'desc';
  u.searchParams.set('sort',col);
  u.searchParams.set('dir',(cur===col&&curDir==='desc')?'asc':'desc');
  location=u.toString();
}
function flt(k,v){const u=new URL(location);if(v)u.searchParams.set(k,v);else u.searchParams.delete(k);location=u.toString();}
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
function _showCover(path){
  var i=_S('sbCvImg');
  if(!i)return;
  i.src=path.indexOf('/')===0?'/local-image?path='+encodeURIComponent(path):path;
  i.style.display='block';
}
async function setCover(path){
  try{
    var r=await fetch('/api/wechat/'+WK+'/cover',{method:'POST',
      headers:{'Content-Type':'application/json'},body:JSON.stringify({path:path})});
    var d=await r.json();
    if(d.ok){_showCover(d.wechat_image_url);_toast('已设为封面','ok');}
    else _toast(d.msg||'设置失败','err');
  }catch(e){_toast('网络错误','err');}
}
async function onCvChange(inp){
  if(!inp.files||!inp.files[0])return;
  var f=inp.files[0];
  // 先本地预览，再真正上传落库（原来只预览、从不保存）
  var r0=new FileReader();
  r0.onload=function(e){var i=_S('sbCvImg');if(i){i.src=e.target.result;i.style.display='block';}};
  r0.readAsDataURL(f);
  try{
    var fd=new FormData();fd.append('file',f);
    _toast('上传封面中…');
    var r=await fetch('/api/wechat/'+WK+'/cover',{method:'POST',body:fd});
    var d=await r.json();
    if(d.ok){_showCover(d.wechat_image_url);_toast('封面已更新','ok');}
    else _toast(d.msg||'上传失败','err');
  }catch(e){_toast('上传失败','err');}
  inp.value='';
}
// 一键抓取本篇 + 关联文章的图集（复用详情页「重新下载」的两个端点）
async function dlAllGalleries(){
  var keys=[WK].concat(RELATED_KEYS||[]);
  var btn=_S('btnDlAll'), log=_S('dlAllLog');
  if(btn){btn.disabled=true;btn.textContent='⏳ 抓取中…';}
  if(log){log.style.display='block';}
  var st={};
  keys.forEach(function(k,i){st[k]={label:(i===0?'本篇 ':'关联'+i+' ')+k.slice(0,8)+'…',status:'pending',images:0};});
  function render(){
    if(!log)return;
    log.textContent=keys.map(function(k){
      var s=st[k];
      var mark={pending:'⏳',running:'⏳',done:'✅',locked:'🔒',timeout:'⏰'}[s.status]||'❌';
      var tail=s.status==='done'?' '+s.images+' 张'
             :s.status==='locked'?' 已在下载中，跳过'
             :(s.status==='pending'||s.status==='running')?'':' '+s.status;
      return mark+' '+s.label+tail;
    }).join('\n');
  }
  // ① 并行触发：所有 key 同时 POST，不等任何一篇下完
  await Promise.all(keys.map(async function(k){
    try{
      var r=await fetch('/api/gallery-download/'+k,{method:'POST',
        headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_url:''})});
      var d=await r.json();
      st[k].status=d.locked?'locked':'running';
    }catch(e){st[k].status='error: '+e;}
  }));
  render();
  // ② 并行轮询：每篇独立循环，互不阻塞；总耗时 ≈ 最慢那篇
  await Promise.all(keys.map(async function(k){
    if(st[k].status==='locked'||st[k].status.indexOf('error')===0)return;
    for(var n=0;n<120;n++){                      // 与详情页同款：2s x 120 = 240s/篇
      await new Promise(function(z){setTimeout(z,2000)});
      try{
        var sd=await (await fetch('/api/gallery-status/'+k)).json();
        var s=String(sd.status||'');
        if(s==='done'){st[k].status='done';st[k].images=(sd.images||[]).length;break;}
        if(s.indexOf('error')===0){st[k].status=s;break;}
      }catch(e){/* 单次轮询失败不算失败，下一轮继续 */}
    }
    if(st[k].status==='running')st[k].status='timeout';
    render();
  }));
  var ok=keys.filter(function(k){return st[k].status==='done'}).length;
  var bad=keys.length-ok;
  if(btn){btn.disabled=false;btn.textContent='🔄 重新下载';}
  _toast('抓取完成：成功 '+ok+' / 共 '+keys.length+(bad?'，'+bad+' 篇未成功':''),bad?'err':'ok');
  await doSave(true);        // 先落盘编辑器内容，避免刷新丢改动
  location.reload();         // 素材库是服务端渲染的，刷新才看得到新图
}
(function initDlAll(){if(HAS_ANY_IMG){var b=_S('btnDlAll');if(b)b.textContent='🔄 重新下载';}})();
function insImg(path){
  if(!_ed)return;
  var src=path.startsWith('/')?'/local-image?path='+encodeURIComponent(path):path;
  var idx=_ed.blocks.getCurrentBlockIndex();
  _ed.blocks.insert('image',{file:{url:src},caption:'',withBorder:false,stretched:false,withBackground:false},{},idx+1,true);
}
function _markDirty(){_dirty=true;_setSave('unsaved');clearTimeout(_stimer);_stimer=setTimeout(function(){if(_dirty)doSave(true);},4000);}
// 图片 URL 双向转换：编辑器里要能显示（走服务器代理），正文里要存真实路径
function _imgUrl(p){return (p&&p.indexOf('/')===0)?'/local-image?path='+encodeURIComponent(p):p;}
function _imgPath(u){
  var P='/local-image?path=';
  if(u&&u.indexOf(P)===0){try{return decodeURIComponent(u.slice(P.length));}catch(e){return u;}}
  return u;
}
function _blocks2txt(blocks){
  var parts=[],n=1;
  (blocks||[]).forEach(function(b){
    if(b.type==='paragraph'&&b.data&&b.data.text)parts.push(b.data.text.replace(/<[^>]+>/g,''));
    else if(b.type==='header'&&b.data&&b.data.text)parts.push((b.data.level===3?'### ':'## ')+b.data.text.replace(/<[^>]+>/g,''));
    else if(b.type==='quote'&&b.data&&b.data.text)parts.push('> '+b.data.text.replace(/<[^>]+>/g,''));
    else if(b.type==='image'&&b.data&&b.data.file&&b.data.file.url){parts.push('【图片'+n+'：'+_imgPath(b.data.file.url)+'】');n++;}
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
      else if(p.indexOf('> ')===0)blocks.push({type:'paragraph',data:{text:p.slice(2)}});
      else blocks.push({type:'paragraph',data:{text:p}});
    });
  }
  var m;re.lastIndex=0;
  while((m=re.exec(txt))!==null){
    addText(txt.slice(last,m.index));
    var ps=m[1].split('|').filter(function(p){return p.trim();});
    if(ps.length===1&&(ps[0].indexOf('http')===0||ps[0].indexOf('/')===0))
      blocks.push({type:'image',data:{file:{url:_imgUrl(ps[0])},caption:'',withBorder:false,stretched:false,withBackground:false}});
    else if(ps.length>0)blocks.push({type:'galleryImage',data:{paths:ps,caption:m[0]}});
    last=m.index+m[0].length;n++;
  }
  addText(txt.slice(last));
  if(!blocks.length)blocks.push({type:'paragraph',data:{text:txt}});
  return blocks;
}
let _wxImgPickerCb=null,_wxImgPickerCancel=null;
function openImgPicker(cb,onCancel){
  _wxImgPickerCb=cb||null;_wxImgPickerCancel=onCancel||null;
  const grid=document.getElementById('wxImgPickerGrid');
  grid.innerHTML='';
  (_allImgs||[]).forEach(function(p){
    const d=document.createElement('div');
    d.style.cssText='cursor:pointer;border:2px solid transparent;border-radius:6px;overflow:hidden;flex-shrink:0';
    d.onmouseenter=function(){d.style.borderColor='#7c3aed';};
    d.onmouseleave=function(){d.style.borderColor='transparent';};
    d.onclick=function(){
      _wxImgPickerCancel=null;
      closeImgPicker();
      if(_wxImgPickerCb){_wxImgPickerCb(p);return;}
    };
    const img=document.createElement('img');
    img.src=_imgUrl(p);
    img.style.cssText='width:88px;height:88px;object-fit:cover;display:block';
    img.title=p.split('/').pop().split('?')[0];
    d.appendChild(img);grid.appendChild(d);
  });
  document.getElementById('wxImgPicker').style.display='block';
}
function closeImgPicker(){document.getElementById('wxImgPicker').style.display='none';var f=_wxImgPickerCancel;_wxImgPickerCancel=null;if(f)f();}

class GalleryImageBlock {
  static get toolbox(){return{title:'图片',icon:'<svg xmlns="http://www.w3.org/2000/svg" width="17" height="15" viewBox="0 0 336 276"><path d="M291 150V79c0-19-15-34-34-34H79c-19 0-34 15-34 34v42l67-44 81 72 56-29 42 30zm0 52l-43-30-56 30-81-72-66 44v30c0 19 15 34 34 34h178c17 0 31-13 34-29zM79 0h178c44 0 79 35 79 79v118c0 44-35 79-79 79H79c-44 0-79-35-79-79V79C0 35 35 0 79 0z"/></svg>'};}
  static get isReadOnlySupported(){return true;}
  constructor({data,api}){this.api=api;this.data={paths:data.paths||[],caption:data.caption||''};this._el=null;}
  render(){const wrap=document.createElement('div');wrap.className='gb-wrap';wrap.style.cssText='border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;background:#fafafa;margin:2px 0';this._el=wrap;this._rebuild();return wrap;}
  _rebuild(){
    const wrap=this._el;if(!wrap)return;
    wrap.innerHTML='';
    const paths=this.data.paths.filter(p=>p);
    this.data.paths=paths;
    if(paths.length){
      const row=document.createElement('div');
      row.style.cssText='display:flex;gap:4px;padding:6px;background:#f0f0f0;justify-content:center';
      paths.forEach((p,idx)=>{
        const cell=document.createElement('div');
        cell.style.cssText='position:relative;flex:'+(paths.length===1?'0 0 auto':'1 1 0')+';max-width:'+(paths.length===1?'100%':'50%');
        const img=document.createElement('img');
        img.src=_imgUrl(p);
        img.style.cssText='width:100%;max-height:320px;object-fit:contain;border-radius:4px;display:block';
        const del=document.createElement('button');
        del.textContent='✕';del.title='移除此图';
        del.style.cssText='position:absolute;top:4px;right:4px;background:rgba(0,0,0,.5);color:#fff;border:none;border-radius:50%;width:22px;height:22px;cursor:pointer;font-size:12px;line-height:1;padding:0';
        del.onclick=()=>{
          this.data.paths.splice(idx,1);
          if(this.data.paths.length===0){try{const bi=this.api.blocks.getCurrentBlockIndex();this.api.blocks.delete(bi);}catch(e){this._rebuild();}}
          else{this._rebuild();}
        };
        cell.appendChild(img);cell.appendChild(del);row.appendChild(cell);
      });
      wrap.appendChild(row);
    }
    const bar=document.createElement('div');
    bar.style.cssText='display:flex;gap:6px;padding:6px 8px;align-items:center;flex-wrap:wrap;background:#fff';
    const addBtn=document.createElement('button');
    addBtn.textContent=paths.length?'+ 添加图片':'📷 从图库选图';
    addBtn.style.cssText='font-size:11px;padding:3px 10px;border:1px dashed #999;border-radius:12px;background:none;cursor:pointer;color:#555';
    addBtn.onclick=()=>openImgPicker(p=>{this.data.paths.push(p);this._rebuild();});
    bar.appendChild(addBtn);
    const cap=document.createElement('input');
    cap.placeholder='图片说明（图片标记）';cap.value=this.data.caption;
    cap.style.cssText='flex:1;font-size:11px;border:none;outline:none;background:transparent;color:#888;min-width:80px';
    cap.oninput=()=>{this.data.caption=cap.value;};
    bar.appendChild(cap);
    wrap.appendChild(bar);
  }
  save(){return{paths:this.data.paths,caption:this.data.caption};}
}
function _initEd(){
  if(typeof EditorJS==='undefined'||typeof ImageTool==='undefined')return;
  // 必须在这里定义：ImageTool 由 CDN 异步加载，页面解析时还不存在，
  // 在顶层写 `class X extends ImageTool` 会抛 ReferenceError 并中断整段脚本。
  class WechatImageTool extends ImageTool {
    render(){
      const wrapper=super.render();
      if(!wrapper||wrapper.querySelector('.wx-img-ctl'))return wrapper;
      const ctl=document.createElement('div');
      ctl.className='wx-img-ctl';
      const swap=document.createElement('button');
      swap.textContent='🔄 换图';swap.title='从图库换一张';
      swap.onclick=(e)=>{e.preventDefault();e.stopPropagation();openImgPicker(p=>this._swap(p));};
      const del=document.createElement('button');
      del.textContent='✕';del.title='删除这张图';
      del.onclick=(e)=>{e.preventDefault();e.stopPropagation();this._del();};
      ctl.appendChild(swap);ctl.appendChild(del);
      const box=(this.ui&&this.ui.nodes&&this.ui.nodes.imageContainer)||wrapper;
      box.appendChild(ctl);
      return wrapper;
    }
    _index(){
      const id=this.block&&this.block.id;
      try{
        if(id!==undefined&&typeof this.api.blocks.getBlockIndex==='function'){
          const i=this.api.blocks.getBlockIndex(id);
          if(typeof i==='number'&&i>=0)return i;
        }
      }catch(e){}
      try{
        const bs=this.api.blocks.getBlocks();
        const w=this.ui&&this.ui.nodes&&this.ui.nodes.wrapper;
        for(let i=0;i<bs.length;i++){
          if(id!==undefined&&bs[i].id===id)return i;
          if(w&&bs[i].holder&&bs[i].holder.contains(w))return i;
        }
      }catch(e){}
      return -1;
    }
    _del(){
      const i=this._index();
      if(i>=0)this.api.blocks.delete(i);
    }
    _swap(path){
      const i=this._index();
      if(i<0)return;
      const cap=(this.data&&this.data.caption)||'';
      const data={file:{url:_imgUrl(path)},caption:cap,withBorder:false,stretched:false,withBackground:false};
      const go=()=>this.api.blocks.insert('image',data,{},i,true);
      const r=this.api.blocks.delete(i);
      if(r&&typeof r.then==='function')r.then(go);else go();
    }
  }
  var raw=(_S('wxHidden')||{}).value||'';
  var blocks=_txt2blocks(raw);
  _ed=new EditorJS({
    holder:'wxEditorjs',minHeight:200,
    placeholder:'开始写公众号正文... (/ 插入标题或图片)',
    tools:{
      header:{class:Header,config:{levels:[2,3],defaultLevel:2},inlineToolbar:true},
      quote:{class:Quote,inlineToolbar:true,config:{quotePlaceholder:'引用内容',captionPlaceholder:'出处（可选）'}},
      image:{class:WechatImageTool,config:{uploader:{uploadByFile:function(file){
        return new Promise(function(res){
          openImgPicker(function(p){res({success:1,file:{url:_imgUrl(p),name:p.split('/').pop()}});},
                        function(){res({success:0,message:'已取消选图'});});
        });
      }}}},
      galleryImage:{class:GalleryImageBlock}
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
    var meta=_S('sbMeta');
    if(r.ok&&d.ok){_toast('已推送到草稿箱','ok');if(meta)meta.textContent='✅ 已推送（刷新可见草稿 ID）';}
    else{
      var msg=d.error||'推送失败';
      _toast(msg,'err');
      // toast 只显示 2.2s，长错误看不完 —— 同时写进侧栏底部常驻显示
      if(meta)meta.textContent='❌ '+msg;
    }
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



# ── 公众号列表：筛选 / 排序（独立页 /wechat-list 与 /api/wechat-list 共用）──
# 排序字段白名单：URL 参数值 -> SQL 列名。绝不把用户输入拼进 ORDER BY。
_WECHAT_SORT = {
    "created_at": "created_at",        # 入库时间（默认）
    "updated_at": "updated_at",        # 编辑时间
    "pub_time":   "wechat_pub_time",   # 发布时间
    "reads":      "wechat_reads",      # 阅读次数（采集器待补，见 §指标）
}
_WECHAT_SORT_LABEL = {
    "created_at": "入库时间",
    "updated_at": "编辑时间",
    "pub_time":   "发布时间",
    "reads":      "阅读/点赞",
}
# 状态筛选（键为 URL 参数值）
_WECHAT_STATUS = {
    "all":       "",
    "published": "AND wechat_pub_time != '' ",
    "draft":     "AND wechat_draft_id != '' AND wechat_pub_time = '' ",
    "pending":   "AND wechat_publish = 1 AND wechat_draft_id = '' AND wechat_pub_time = '' ",
    "none":      "AND wechat_publish = 0 AND wechat_draft_id = '' AND wechat_pub_time = '' ",
}
_WECHAT_STATUS_LABEL = {
    "all": "全部状态", "published": "已发布", "draft": "草稿箱",
    "pending": "待发布", "none": "未配置",
}
_WECHAT_BASE_WHERE = ("status='active' AND (wechat_content!='' OR wechat_publish=1 "
                      "OR wechat_draft_id!='') ")


def wechat_list_query(args) -> tuple:
    """构造公众号列表的 SQL，返回 (sql, params, meta)。

    meta 带回解析后的筛选值，供模板/前端回填控件状态。

    日期范围按**入库时间**（created_at）过滤 —— 与默认排序轴一致，避免
    「按 A 排、按 B 筛」造成的困惑。
    """
    search = (args.get("search") or "").strip()
    st = args.get("st") or "all"
    src = (args.get("src") or "").strip()
    df = (args.get("df") or "").strip()
    dt = (args.get("dt") or "").strip()
    sort = args.get("sort") or "created_at"
    direction = (args.get("dir") or "desc").lower()

    if sort not in _WECHAT_SORT:
        sort = "created_at"
    if st not in _WECHAT_STATUS:
        st = "all"
    direction = "ASC" if direction == "asc" else "DESC"

    sql = ("SELECT key,title,wechat_title,wechat_publish,wechat_draft_id,wechat_pub_time,"
           "updated_at,created_at,image_url,wechat_image_url,source,channel,"
           "wechat_reads,wechat_likes,wechat_collected_at "
           "FROM news WHERE " + _WECHAT_BASE_WHERE)
    params = []
    if search:
        sql += "AND (wechat_title LIKE ? OR title LIKE ?) "
        params += [f"%{search}%", f"%{search}%"]
    sql += _WECHAT_STATUS[st]
    if src:
        sql += "AND source = ? "
        params.append(src)
    if df:
        sql += "AND date(created_at) >= date(?) "
        params.append(df)
    if dt:
        sql += "AND date(created_at) <= date(?) "
        params.append(dt)
    sql += f"ORDER BY {_WECHAT_SORT[sort]} {direction} LIMIT 200"

    # 表头箭头：当前排序列显示 ▲/▼，其余显示 ↕（可点提示）
    arrows = {k: ("▲" if direction == "ASC" else "▼") if k == sort else "↕"
              for k in _WECHAT_SORT}
    meta = {"search": search, "st": st, "src": src, "df": df, "dt": dt,
            "sort": sort, "dir": direction.lower(), "arrows": arrows,
            "sort_label": _WECHAT_SORT_LABEL[sort],
            "sort_col": _WECHAT_SORT[sort]}
    return sql, params, meta


def _wechat_stats() -> dict:
    """公众号文章的**全量**统计 —— 不受筛选影响（统计卡片始终显示全量）。

    分桶口径与 _WECHAT_STATUS 完全一致，这样点某个状态筛选时，
    卡片上的数字与列表条数对得上。
    """
    from sqlite_db import _connect
    with _connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(wechat_pub_time != '') AS published,"
            " SUM(wechat_draft_id != '' AND wechat_pub_time = '') AS draft,"
            " SUM(wechat_publish = 1 AND wechat_draft_id = '' AND wechat_pub_time = '') AS pending,"
            " SUM(wechat_publish = 0 AND wechat_draft_id = '' AND wechat_pub_time = '') AS none"
            " FROM news WHERE " + _WECHAT_BASE_WHERE
        ).fetchone()
    return {k: int(row[k] or 0) for k in ("total", "published", "draft", "pending", "none")}


def _wechat_sources() -> list:
    """公众号文章出现过的来源（给筛选下拉用）。"""
    from sqlite_db import _connect
    with _connect() as db:
        rows = db.execute(
            "SELECT DISTINCT source FROM news WHERE " + _WECHAT_BASE_WHERE +
            "AND source IS NOT NULL AND source != '' ORDER BY source"
        ).fetchall()
    return [r[0] for r in rows]


@wechat_bp.route('/api/wechat-list')
def api_wechat_list():
    from sqlite_db import _connect
    sql, params, meta = wechat_list_query(request.args)
    with _connect() as db:
        rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    return jsonify({"items": rows, "sources": _wechat_sources(),
                    "stats": _wechat_stats(),          # 全量，不受筛选影响
                    "meta": meta, "count": len(rows)})

@wechat_bp.route('/wechat-list')
def wechat_list():
    from flask import render_template_string as rts
    from sqlite_db import _connect
    sql, params, meta = wechat_list_query(request.args)
    with _connect() as db:
        rows = [dict(r) for r in db.execute(sql, params).fetchall()]
    return rts(WECHAT_LIST_HTML, rows=rows, meta=meta,
               stats=_wechat_stats(),              # 全量，不受筛选影响
               sources=_wechat_sources(),
               status_labels=_WECHAT_STATUS_LABEL,
               sort_labels=_WECHAT_SORT_LABEL)

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
.flt-sel,.flt-date,.flt-btn{font-size:11.5px;color:var(--text2);background:var(--card-bg);border:1px solid var(--border);border-radius:8px;padding:5px 8px;outline:none}
.flt-sel{cursor:pointer;max-width:150px}
.flt-btn{cursor:pointer;padding:5px 11px;text-decoration:none;white-space:nowrap}
.flt-btn:hover{border-color:var(--wx);color:var(--wx)}
.btn{display:inline-flex;align-items:center;gap:5px;height:30px;padding:0 13px;border:none;border-radius:7px;cursor:pointer;font-size:11.5px;font-weight:500;white-space:nowrap;transition:all .12s;border:1px solid transparent}
.btn-wx{background:var(--wx);color:#fff}
.btn-gray{background:#f3f4f6;color:var(--text2);border-color:var(--border)}

/* table */
.table-card{background:var(--card-bg);border-radius:var(--radius);box-shadow:var(--shadow);border:1px solid var(--border);overflow:hidden}
.table-header{display:grid;grid-template-columns:1fr 76px 86px 86px 86px 84px 56px;padding:8px 16px;background:#f9fafb;border-bottom:1px solid var(--border)}
.table-header span{font-size:10.5px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.04em}
.table-row{display:grid;grid-template-columns:1fr 76px 86px 86px 86px 84px 56px;padding:10px 16px;border-bottom:1px solid var(--border);align-items:center;transition:background .1s}
.table-row:last-child{border-bottom:none}
.table-row:hover{background:#f9fafb}
.row-title{font-size:12.5px;font-weight:500;color:var(--text);text-decoration:none;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.row-title:hover{color:var(--wx)}
.row-date{font-size:11px;color:var(--text3)}
.th-sort{cursor:pointer;user-select:none}
.th-sort:hover{color:var(--wx)}
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
        <div><div class="stat-num">{{stats.total}}</div><div class="stat-label">文章总数</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background:#fef3c7">📝</div>
        <div><div class="stat-num">{{stats.draft}}</div><div class="stat-label">草稿箱</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background:#dbeafe">⏳</div>
        <div><div class="stat-num">{{stats.pending}}</div><div class="stat-label">待发布</div></div>
      </div>
      <div class="stat-card">
        <div class="stat-icon" style="background:#d1fae5">✅</div>
        <div><div class="stat-num">{{stats.published}}</div><div class="stat-label">已发布</div></div>
      </div>
    </div>

    <!-- toolbar -->
    <div class="toolbar">
      <div class="search-box">
        <span style="color:var(--text3);font-size:13px">🔍</span>
        <input id="searchInput" placeholder="搜索标题..." value="{{meta.search}}"
          oninput="clearTimeout(_t);_t=setTimeout(()=>flt('search',this.value),400)">
      </div>
      <select class="flt-sel" onchange="flt('st',this.value)" title="状态筛选">
        {% for k,v in status_labels.items() %}<option value="{{k}}" {{'selected' if meta.st==k else ''}}>{{v}}</option>{% endfor %}
      </select>
      <select class="flt-sel" onchange="flt('src',this.value)" title="来源筛选">
        <option value="">全部来源</option>
        {% for s in sources %}<option value="{{s}}" {{'selected' if meta.src==s else ''}}>{{s}}</option>{% endfor %}
      </select>
      <input type="date" class="flt-date" value="{{meta.df}}" onchange="flt('df',this.value)" title="入库起始日期">
      <span style="color:var(--text3);font-size:11px">~</span>
      <input type="date" class="flt-date" value="{{meta.dt}}" onchange="flt('dt',this.value)" title="入库结束日期">
      <a class="flt-btn" href="/wechat-list" title="清空全部筛选">重置</a>
      <span style="flex:1"></span>
      <span style="font-size:11px;color:var(--text3)">筛选出 {{rows|length}} 篇</span>
    </div>

    <!-- table -->
    <div class="table-card">
      {% if rows %}
      <div class="table-header">
        <span>标题</span>
        <span>状态</span>
        <span class="th-sort" onclick="sortBy('created_at')" title="点此按入库时间排序">入库时间 {{meta.arrows.created_at}}</span>
        <span class="th-sort" onclick="sortBy('updated_at')" title="点此按编辑时间排序">编辑时间 {{meta.arrows.updated_at}}</span>
        <span class="th-sort" onclick="sortBy('pub_time')" title="点此按发布时间排序">发布时间 {{meta.arrows.pub_time}}</span>
        <span class="th-sort" onclick="sortBy('reads')" title="点此按阅读次数排序">阅读/点赞 {{meta.arrows.reads}}</span>
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
        <span class="row-date">{{(r.created_at or '')[:10]}}</span>
        <span class="row-date">{{(r.updated_at or '')[:10]}}</span>
        <span class="row-date">{{(r.wechat_pub_time or '')[:10] or '—'}}</span>
        {% if r.wechat_collected_at %}<span class="row-date" title="采集于 {{r.wechat_collected_at}}">{{r.wechat_reads}} / {{r.wechat_likes}}</span>
        {% else %}<span class="row-date" title="尚未采集（需账号有 datacube 权限且文章已发布）">—</span>{% endif %}
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

    # 2. 公众号封面：优先 wechat_image_url（公众号页 setCover 写的就是它），回退 image_url。
    #    只收本地路径：素材库用于往正文插图，正文图必须本地（发布时 uploadimg 上传）；
    #    远程 URL 写进正文微信显示不出来。未换过封面时回退值仍是 Yahoo 远程地址，
    #    收录它既与侧栏封面重复，又会往正文塞远程图。
    _wx_cover = (news.get('wechat_image_url') or '').strip() or (news.get('image_url') or '').strip()
    if _wx_cover.startswith('/'):
        _add_imgs([_wx_cover], '封面')

    # 3. Related keys
    related_articles = []   # [{'key','title','count'}] 供「下载全部」按钮并行抓图
    for rk in rk_raw.split(','):
        rk = rk.strip()
        if not rk: continue
        rr = get_by_key(rk)
        if not rr: continue
        title_short = (rr.get('title', '') or rk)[:12]
        _n_before = len(all_images)
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
        related_articles.append({'key': rk, 'title': title_short,
                                 'count': len(all_images) - _n_before})

    return rts(WECHAT_HTML, news=news, all_images=all_images, related_articles=related_articles)

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
        # Web 预览：图片走服务器代理，否则另一台机器上的浏览器加载不了 file://
        html = _render_html_preview(content, title, theme,
                                    img_base="/local-image?path=")
        return html, 200, {'Content-Type': 'text/html; charset=utf-8'}
    except Exception as e:
        return f'<pre>预览渲染失败: {e}</pre>', 500

@wechat_bp.route('/api/wechat/<key>/cover', methods=['POST'])
def api_wechat_cover(key):
    """设置公众号封面并落库（写 news.wechat_image_url）。

    两种入参：
      - JSON {"path": "/本地路径"}   ← 从图片素材库选
      - multipart 字段 file          ← 选本地文件（真正上传并保存）
    原来「换封面」只在前端用 FileReader 预览，从不落库，发布时用的还是旧封面。
    """
    import time as _time
    from pathlib import Path as _P
    from sqlite_db import update_news

    data = request.get_json(silent=True) or {}
    path = (data.get('path') or '').strip()

    if not path:
        f = request.files.get('file')
        if not f:
            return jsonify({"ok": False, "msg": "缺少 path 或 file"}), 400
        try:
            from config.yahoo_conf import GALLERY_CACHE_DIR
            d = _P(GALLERY_CACHE_DIR).expanduser() / key
            d.mkdir(parents=True, exist_ok=True)
            ext = _P(f.filename or '').suffix or '.jpg'
            dest = d / ("cover_%d%s" % (int(_time.time()), ext))
            f.save(str(dest))
            path = str(dest)
        except Exception as e:
            return jsonify({"ok": False, "msg": "封面上传失败: %s" % e}), 500

    if not update_news(key, {'wechat_image_url': path}):
        return jsonify({"ok": False, "msg": "写库失败"}), 500
    return jsonify({"ok": True, "wechat_image_url": path})


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
    # 页面选的主题原来被忽略（服务端不读 ?theme=），导致永远用默认 sports 主题
    theme = (request.args.get('theme') or '').strip()
    cmd = [sys.executable, 'wechat_publisher.py', '--key', key]
    if theme:
        cmd += ['--theme', theme]
    result = subprocess.run(
        cmd,
        capture_output=True, text=True, timeout=120,
        cwd=scripts_dir,
        env={**os.environ, 'PYTHONPATH': scripts_dir}
    )
    import re
    from sqlite_db import _log_db_error
    if result.returncode == 0:
        # 只认哨兵行：原先用 media_id\s*=\s*(\S+) 会先匹配到封面上传那行的
        # 截断 id（media_id={cover[:12]}...），导致 DB 里存的 draft_id 一直是错的
        m = re.search(r'DRAFT_MEDIA_ID:\s*(\S+)', result.stdout)
        if not m:
            # 退出码 0 却没拿到 media_id —— 不能报成功（本项目反复出现的误报反模式）
            _log_db_error(f"发布器未返回 media_id key={key}")
            return jsonify({"ok": False,
                            "error": "发布器未返回 media_id，草稿可能未创建",
                            "output": result.stdout[-800:]}), 500
        update_news(key, {'wechat_draft_id': m.group(1)})
        return jsonify({"ok": True, "output": result.stdout[-500:]})
    # 失败：优先把 stdout 里的 ❌/⚠️ 行挑出来，比一坨 traceback 好读
    lines = [ln.strip() for ln in result.stdout.splitlines()
             if ln.strip().startswith(("❌", "⚠️"))]
    if not lines and result.stderr.strip():
        lines = [result.stderr.strip().splitlines()[-1]]
    msg = "；".join(lines[-3:]) or "推送失败（无输出）"
    _log_db_error(f"推送公众号失败 key={key}: {msg}")
    return jsonify({"ok": False, "error": msg, "output": result.stdout[-800:]})

