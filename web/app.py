#!/usr/bin/env python3
"""新闻管理 Web UI — SQLite 版 Notion 替代"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))  # project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from flask import Flask, jsonify, render_template_string, request, send_file
from config.yahoo_conf import STORAGE_BACKEND, DB_PATH
from sqlite_db import init_db, query_news, get_by_key, update_news, get_score_dims, upsert_score_dims, stats, recalculate_scores, _connect
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
_log_file_lock = threading.Lock()      # L-3: serialize concurrent log writes

def _save_task_log(task_id: str, log_text: str):
    try:
        today = _dt_ad2.now().strftime('%Y-%m-%d')
        log_path = os.path.join(TASK_LOG_DIR, f'task_{today}.log')
        with _log_file_lock:
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
from web.wechat_views import wechat_bp
app.register_blueprint(wechat_bp)
from web.login_views import login_bp
app.register_blueprint(login_bp)
from web.services_views import services_bp
app.register_blueprint(services_bp)
from web.feedback_views import feedback_bp
app.register_blueprint(feedback_bp)


@app.after_request
def _no_cache_html(resp):
    """避免浏览器缓存 admin 页导致「改了没生效/按钮失效」的旧页问题。"""
    ct = (resp.headers.get("Content-Type") or "")
    if "text/html" in ct:
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
    return resp
init_db()

@app.before_request
def check_backend():
    if STORAGE_BACKEND == "notion":
        return NOTION_ONLY, 200

# Background task tracking {task_id: status}
_tasks = {}
_task_counter = 0
_task_counter_lock = threading.Lock()   # C-2: protect counter
_publish_lock = threading.Lock()
_publish_running = False

# Quick endpoint to reset stuck publish lock
@app.route('/api/admin/reset-publish-lock', methods=['POST'])
def api_reset_publish_lock():
    global _publish_running
    with _publish_lock:
        if _publish_running:
            _publish_running = False
            return jsonify({"ok": True, "msg": "Publish lock reset"})
        return jsonify({"ok": False, "msg": "No lock to reset"})

# Quick endpoint to reset stuck fetch lock
@app.route('/api/admin/reset-fetch-lock', methods=['POST'])
def api_reset_fetch_lock():
    global _fetch_running
    with _fetch_lock:
        if _fetch_running:
            _fetch_running = False
            return jsonify({"ok": True, "msg": "Fetch lock reset"})
        return jsonify({"ok": False, "msg": "No lock to reset"})
_fetch_lock = threading.Lock()
_fetch_running = False
_regen_lock = threading.Lock()
_regen_keys = set()
_tasks_lock = threading.Lock()          # H-3: protect _tasks reads/writes

def _run_task(cmd, task_id, env=None, on_done=None):
    log_lines = []
    if env is None:
        env = {}
    env.setdefault('PYTHONUNBUFFERED', '1')
    proc = None
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1,
                                cwd=os.path.join(os.path.dirname(__file__), '..', 'scripts'),
                                env=env)
        _tasks[task_id] = {'status': 'running', 'log': '', 'proc': proc}
        today = _dt_ad2.now().strftime('%Y-%m-%d')
        log_path = os.path.join(TASK_LOG_DIR, f'task_{today}.log')
        with open(log_path, 'a', buffering=1) as log_fh:
            log_fh.write(f"\n=== {task_id} {_dt_ad2.now().strftime('%H:%M:%S')} ===\n")
            for line in proc.stdout:
                log_lines.append(line.rstrip())
                log_fh.write(line)
                _tasks[task_id] = {'status': 'running', 'log': '\n'.join(log_lines), 'proc': proc}
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
    data = request.get_json(silent=True) or {}
    gallery_url = (data.get('gallery_url') or '').strip()
    trigger_download(key, gallery_url)
    return jsonify({"status": "started"})

@app.route('/api/trigger-fetch', methods=['POST'])
def api_trigger_fetch():
    global _task_counter, _fetch_running
    with _fetch_lock:                   # C-1: acquire lock first
        if _fetch_running:
            return jsonify({"locked": True, "msg": "已有抓取任务在运行，请等待完成"})
        _fetch_running = True
    data = request.json or {}
    with _task_counter_lock:            # C-2: atomic counter
        tid = str(_task_counter); _task_counter += 1
    _tasks[tid] = {'status': 'running', 'log': ''}
    py = sys.executable
    scripts_dir_abs = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'scripts'))
    sub_env = {**os.environ, 'STORAGE_BACKEND': STORAGE_BACKEND, 'PATH': os.environ.get('PATH',''),
               'SQLITE_PATH': DB_PATH}
    sub_env['PYTHONPATH'] = scripts_dir_abs + ':' + os.path.abspath(os.path.join(scripts_dir_abs, '..')) + ':' + sub_env.get('PYTHONPATH','')
    sub_env['PYTHONUNBUFFERED'] = '1'
    if data.get('mode') == 'keywords':
        kws = data.get('keywords', []) or [{"keyword": data.get('keyword','AKB'), "max": data.get('max',5)}]
        import json as _json
        cmd = [py, 'yahoo_news_auto_sqlite.py', '--keywords', _json.dumps(kws), '--push']
        def on_fetch_done():
            global _fetch_running
            with _fetch_lock: _fetch_running = False
            try:
                import sys as _sys, os as _os
                _sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
                from scripts.scoring import dedup_today_candidates
                for kw_entry in kws:
                    dedup_today_candidates(keyword=kw_entry.get("keyword", ""))
            except Exception as _e:
                app.logger.warning(f"[dedup] 去重失败（不影响后续流程）: {_e}")
        threading.Thread(target=_run_task, args=(cmd, tid, sub_env, on_fetch_done), daemon=True).start()
        return jsonify({"task_id": tid})
    else:
        cmd = [py, 'yahoo_recommendations_sqlite.py',
               '--max', str(data.get('max',10)), '--push']
    def on_fetch_done():
        global _fetch_running
        with _fetch_lock:
            _fetch_running = False
    threading.Thread(target=_run_task, args=(cmd, tid, sub_env, on_fetch_done), daemon=True).start()
    return jsonify({"task_id": tid})

@app.route('/api/trigger-publish', methods=['POST'])
def api_trigger_publish():
    global _task_counter, _publish_running
    with _publish_lock:
        if _publish_running:
            return jsonify({"locked": True, "msg": "已有发布任务在运行，请等待完成"})
        _publish_running = True
    with _task_counter_lock:
        tid = str(_task_counter); _task_counter += 1


    cmd = [sys.executable, 'yahoo_news_publish.py', '--auto', '--force', '--reuse-existing-tab']
    post_time = (request.json or {}).get('post_time', '')
    if post_time:
        cmd += ['--post-time', post_time]
    sub_env = {**os.environ, 'STORAGE_BACKEND': STORAGE_BACKEND}

    def on_publish_done():
        global _publish_running
        with _publish_lock:
            _publish_running = False

    # 导出与普通发布都在 yahoo_news_publish.py 内串行执行（export 失败不阻断）
    threading.Thread(target=_run_task, args=(cmd, tid, sub_env, on_publish_done), daemon=True).start()
    return jsonify({"task_id": tid})

@app.route('/api/task/<tid>')
def api_task(tid):
    t = _tasks.get(tid, 'unknown')
    if isinstance(t, dict):
        # H-3: copy before mutating to avoid racing with background thread
        r = {k: v for k, v in t.items() if k != 'proc'}
        if r.get('log'):
            r['log'] = r['log'].replace('\x00', '').replace('\x1b', '')
        return jsonify(r)
    return jsonify({"status": t, "log": ""})

@app.route('/api/task/<tid>/stop', methods=['POST'])
def api_task_stop(tid):
    t = _tasks.get(tid, {})
    if isinstance(t, dict) and t.get('status') == 'running':
        proc = t.get('proc')
        if proc:
            try:
                proc.kill()
            except Exception:
                pass
        _tasks[tid] = {'status': 'error: 用户终止', 'log': t.get('log', '') + '\n\n🛑 任务已终止'}
        return jsonify({"ok": True})
    return jsonify({"ok": False, "msg": "无运行中的任务"})

@app.route('/api/task-logs')
def api_task_logs():
    today = _dt_ad2.now().strftime('%Y-%m-%d')
    log_path = os.path.join(TASK_LOG_DIR, f'task_{today}.log')
    if os.path.exists(log_path):
        with open(log_path) as f:
            return jsonify({"logs": f.read()[-20000:]})
    return jsonify({"logs": ""})

# Custom keywords persistence — stored in agent_config DB
def _load_custom_keywords():
    from sqlite_db import get_config
    return get_config("custom_keywords", default=[])

def _save_custom_keywords(kws):
    from sqlite_db import set_config
    set_config("custom_keywords", kws)

@app.route('/api/custom-keywords', methods=['GET'])
def api_custom_keywords():
    return jsonify({"keywords": _load_custom_keywords()})

@app.route('/api/custom-keywords', methods=['POST'])
def api_custom_keywords_save():
    data = request.json or {}
    kws = data.get('keywords', [])
    _save_custom_keywords(kws)
    return jsonify({"ok": True})

@app.route('/api/agent-config', methods=['GET'])
def api_agent_config_get():
    from sqlite_db import get_config
    return jsonify({
        "focus_topics": get_config("focus_topics", default=[]),
        "yahoo_keyword_map": get_config("yahoo_keyword_map", default={}),
        "tag_config": get_config("tag_config", default={}),
        "daily_quota": get_config("daily_quota", default=5),
        "publish_threshold": get_config("publish_threshold", default=3.0),
        "retry_threshold": get_config("retry_threshold", default=2.0),
    })

@app.route('/api/agent-config', methods=['PUT'])
def api_agent_config_put():
    from sqlite_db import set_config
    data = request.json or {}
    for key in ["focus_topics", "yahoo_keyword_map", "tag_config", "daily_quota",
                "publish_threshold", "retry_threshold"]:
        if key in data:
            set_config(key, data[key])
    return jsonify({"ok": True})

@app.route('/api/keywords')
def api_keywords():
    try:
        from sqlite_db import get_config
        topics = get_config("focus_topics", default=[])
        kw_map = get_config("yahoo_keyword_map", default={})
        daily = get_config("daily_quota", default=5)
        kws = []
        for t in topics:
            cfg = kw_map.get(t, {"keyword": t, "max": daily})
            kws.append({"topic": t, "keyword": cfg.get("keyword", t), "max": cfg.get("max", daily)})
    except Exception:
        kws = [{"topic": "AKB48", "keyword": "AKB", "max": 10}]
    return jsonify({"keywords": kws})

@app.route('/api/task-status/<tid>')
def api_task_status(tid):
    """返回单个任务的当前状态"""
    t = _tasks.get(tid)
    if t is None:
        return jsonify({"error": "not found"}), 404
    return jsonify({
        "task_id": tid,
        "status": t.get('status', 'unknown'),
        "log": t.get('log', '')
    })

@app.route('/api/active-tasks')
def api_active_tasks():
    """返回所有运行中的任务"""
    active = []
    for tid, t in _tasks.items():
        if isinstance(t, dict) and t.get('status') == 'running':
            active.append({'task_id': tid, 'status': 'running', 'log': t.get('log', '')})
    return jsonify({"active": active, "fetch_running": _fetch_running, "publish_running": _publish_running})

@app.route('/api/regenerate-title/<key>', methods=['POST'])
def api_regenerate_title(key):
    global _regen_keys
    with _regen_lock:                   # H-1: remove pre-lock check
        if key in _regen_keys: return jsonify({"locked": True, "msg": "该新闻正在重新生成中"})
        _regen_keys.add(key)
    from sqlite_db import get_by_key
    row = get_by_key(key)
    if not row:
        with _regen_lock: _regen_keys.discard(key)
        return jsonify({"error": "not found"}), 404
    tid = f"regen-title_{key}"
    _tasks[tid] = {'status': 'running', 'log': ''}
    def do_regen_title(row=row):  # L-2: capture row by value
        log = []
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
            from yahoo_common import generate_title_only, evaluate_quality
            from sqlite_db import update_news
            title_ja = row.get('title_ja') or row.get('title', '')
            content_ja = row.get('content_ja') or ''
            content_zh = row.get('content') or ''
            summary = row.get('summary') or ''
            import json as _j
            fs = row.get('format') or 'news'
            primary_format = fs if isinstance(fs, str) else (fs[0] if isinstance(fs, list) and fs else 'news')
            # 资讯体直接标记，不让模型猜；故事体用 story_type 细分
            story_type = row.get('story_type') or ('故事体-自动判断子类型' if primary_format == 'story' else '资讯体')
            current_title = row.get('title') or ''
            log.append(f'体裁: format={primary_format}, story_type={story_type or "(无)"}, is_long_form={row.get("is_long_form",0)}')
            log.append(f'原标题: {title_ja[:60]}')
            log.append(f'当前标题: {current_title[:60]}')
            log.append('生成标题...')
            _tasks[tid] = {'status': 'running', 'log': '\n'.join(log)}
            new_title = generate_title_only(
                title_ja, content_ja,
                summary=summary, story_type=story_type,
                current_title=current_title, content_zh=content_zh,
            )
            if not new_title:
                _tasks[tid] = {'status': 'error: 标题生成失败', 'log': '\n'.join(log)}
                return
            log.append(f'新标题: {new_title[:50]}')
            log.append('评估质量...')
            _tasks[tid] = {'status': 'running', 'log': '\n'.join(log)}
            quality = evaluate_quality(new_title, content_ja or row.get('content',''), row.get('comment',''), title_ja, content_ja[:800])
            new_ts = quality.get('title_score', 0)
            update_news(key, {'title': new_title, 'title_score': new_ts})
            if quality.get('scores'):
                try:
                    from sqlite_db import upsert_score_dims
                    upsert_score_dims(key, quality['scores'])
                except Exception as _e: app.logger.warning(f"[regen] upsert_score_dims failed: {_e}")
            log.append(f'✅ 完成 评分:{new_ts:.1f}')
            _tasks[tid] = {'status': 'done', 'log': '\n'.join(log)}
            print(f'📝 标题重拟: {new_title[:40]} 评分:{new_ts:.1f}')
        except Exception as e:
            _tasks[tid] = {'status': f'error: {e}', 'log': '\n'.join(log)}
        finally:
            with _regen_lock: _regen_keys.discard(key)
    threading.Thread(target=do_regen_title, daemon=True).start()
    return jsonify({"task_id": tid})

@app.route('/api/regenerate/<key>', methods=['POST'])
def api_regenerate(key):
    global _regen_keys
    with _regen_lock:                   # H-1: remove pre-lock check
        if key in _regen_keys: return jsonify({"locked": True, "msg": "该新闻正在重新生成中"})
        _regen_keys.add(key)
    from sqlite_db import get_by_key
    row = get_by_key(key)
    if not row:
        with _regen_lock: _regen_keys.discard(key)
        return jsonify({"error": "not found"}), 404
    def do_regenerate(row=row):  # L-2: capture row by value
        _tasks['regen_'+key] = {'status': 'running', 'log': ''}
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
            from yahoo_common import translate_title, generate_content_and_comment, evaluate_quality, generate_video_caption, generate_story_article
            import json as _json
            log = []
            is_story = row.get('is_long_form') or row.get('format') == 'story'
            title_ja = row.get('title_ja', row.get('title', ''))
            # 优先重新抓取原文（获取翻页内容），失败则用 DB 缓存
            content_ja = row.get('content_ja', '')
            article_link = row.get('link', '')
            if article_link and 'news.yahoo.co.jp' in article_link:
                try:
                    from yahoo_common import fetch_article_details as _fetch_details
                    log.append('重新抓取原文...')
                    fresh = _fetch_details(article_link)
                    if fresh.get('original_title'):
                        title_ja = fresh['original_title']
                        log.append(f'标题已刷新: {title_ja[:50]}')
                    if fresh.get('body_text'):
                        content_ja = fresh['body_text']
                        log.append(f'✅ 原文已更新 ({len(content_ja)} 字)')
                        # 更新 DB 中的原文、标题和图片
                        updates = {'content_ja': content_ja, 'body_text': content_ja}
                        if fresh.get('original_title'):
                            updates['title_ja'] = fresh['original_title']
                        update_news(key, updates)
                        if fresh.get('article_images'):
                            update_news(key, {'_article_images': fresh['article_images']})
                            n_imgs = len(fresh.get('article_images', []))
                            log.append(f'📷 下载 {n_imgs} 张图片...')
                            _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                            try:
                                from yahoo_common import _download_article_images as _dl_art_imgs
                                news = dict(row)
                                news['_article_images'] = fresh['article_images']
                                _dl_art_imgs(news, fresh['article_images'])
                                update_news(key, {'gallery_images': news.get('gallery_images', [])})
                                log.append(f'✅ 图片已下载')
                            except Exception as e:
                                log.append(f'⚠️ 图片下载失败: {e}')
                except Exception as e:
                    log.append(f'⚠️ 重新抓取失败，使用缓存: {e}')

            if is_story:
                log.append('故事体重新生成...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                title_zh = translate_title(title_ja) if title_ja else row.get('title', '')
                # Use fresh images from re-fetch if available, else DB cached
                try:
                    _ = fresh
                    article_imgs = fresh.get('article_images') or []
                except NameError:
                    article_imgs = row.get('_article_images') or []
                log.append(f'调用 generate_story_article: title_ja={title_ja[:60]}, body_ja_len={len(content_ja or row.get("content",""))}, imgs={len(article_imgs)}')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                story = generate_story_article(title_ja, title_zh, content_ja or row.get('content', ''),
                                               article_images=article_imgs)
                if not story:
                    err_log = log + [f'❌ generate_story_article 返回 None — 检查 data/logs/error-{_dt_ad2.now().strftime("%Y-%m-%d")}.log']
                    _tasks['regen_'+key] = {'status': 'error: 故事体生成失败', 'log': '\n'.join(err_log)}
                    return
                new_title = story.get('title', title_zh)
                log.append(f'标题: {new_title[:50]}')
                log.append('评估质量...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                quality = evaluate_quality(new_title, story['body'], story.get('outro', ''), title_ja, content_ja)
                updates = {'title': new_title, 'summary': story.get('intro', ''),
                           'content': story['body'], 'comment': story.get('outro', ''),
                           'is_long_form': True,
                           'format': row.get('format', 'story'),
                           'title_score': quality['title_score'], 'content_score': quality['content_score']}
                if story.get('story_type'):
                    updates['story_type'] = story['story_type']
                # 更新标签：保留原标签 + LLM 提取的人物
                old_tags = row.get('tags') or []
                if isinstance(old_tags, str): old_tags = old_tags.split(',')
                person_tags = [p.strip() for p in story.get('persons','').split(',') if p.strip()]
                from yahoo_common import _build_final_tags
                updates['tags'] = _build_final_tags(list({*old_tags, *person_tags}))
            else:
                log.append('翻译标题...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                title_zh = translate_title(title_ja) if title_ja else row.get('title', '')
                log.append(f'标题: {title_zh[:50]}')
                log.append('生成内容...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                gen = generate_content_and_comment(title_ja, title_zh, body_text=row.get('content',''))
                if not gen:
                    _tasks['regen_'+key] = {'status': 'error: 内容生成失败', 'log': '内容生成失败'}
                    return
                seo_title, summary, content, comment, _, topic_tags = gen
                log.append('生成短配文...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                video_caption = generate_video_caption(seo_title, summary, content, list(topic_tags) if topic_tags else [])
                log.append('评估质量...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                quality = evaluate_quality(seo_title, content, comment, title_ja, row.get('content',''))
                updates = {'title': seo_title, 'summary': summary, 'content': content, 'comment': comment,
                           'video_caption': video_caption,
                           'title_score': quality['title_score'], 'content_score': quality['content_score']}
                if len(content or '') >= 950:
                    updates['is_long_form'] = True
                    updates['format'] = 'news'
                # 更新标签：LLM 生成的 topic_tags
                if topic_tags:
                    old_tags = row.get('tags') or []
                    if isinstance(old_tags, str): old_tags = old_tags.split(',')
                    from yahoo_common import _build_final_tags
                    updates['tags'] = _build_final_tags(list({*old_tags, *(list(topic_tags) if topic_tags else [])}))

            update_news(key, updates)
            if quality.get('scores'):
                try:
                    from sqlite_db import upsert_score_dims
                    upsert_score_dims(key, quality['scores'])
                except Exception as _e: app.logger.warning(f"[regen] upsert_score_dims failed: {_e}")
            ts = quality.get('title_score', 0)
            cs = quality.get('content_score', 0)
            log.append('✅ 完成 标题' + str(round(ts,2)) + ' 内容' + str(round(cs,2)))
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

@app.route('/api/score-dim/<key>/<dimension>', methods=['PUT'])
def api_score_dim_override(key, dimension):
    """人工纠正评分维度"""
    data = request.json or {}
    human_value = data.get("human_value")
    override_note = data.get("override_note", "")
    if human_value is None or human_value not in (0, 0.5, 1):
        return jsonify({"error": "human_value must be 0, 0.5, or 1"}), 400
    dims = get_score_dims(key)
    target = next((d for d in dims if d["dimension"] == dimension), None)
    if not target:
        return jsonify({"error": f"Dimension '{dimension}' not found for {key}"}), 404
    # Move current value to llm_value, write human override
    with _connect() as db:
        db.execute("""UPDATE score_dims SET
            llm_value = value,
            human_value = ?,
            human_override = 1,
            override_note = ?,
            value = ?
            WHERE news_key = ? AND dimension = ?""",
            (human_value, override_note, human_value, key, dimension))
    new_scores = recalculate_scores(key)
    return jsonify({"ok": True, "new_title_score": new_scores["title_score"],
                    "new_content_score": new_scores["content_score"]})

@app.route('/webhook/feishu', methods=['POST'])
def feishu_webhook():
    """飞书事件回调 + 卡片交互分发"""
    from scripts.feishu_bot import verify_feishu_signature
    from scripts.sqlite_db import update_news, set_config, set_state
    body = request.get_data()
    data = request.json or {}
    # Challenge 验证
    if data.get("challenge"):
        return jsonify({"challenge": data["challenge"]})
    # 签名验证
    ts = request.headers.get("X-Lark-Request-Timestamp", "")
    nonce = request.headers.get("X-Lark-Request-Nonce", "")
    sig = request.headers.get("X-Lark-Signature", "")
    if not verify_feishu_signature(ts, nonce, body, sig):
        return jsonify({"error": "invalid signature"}), 401
    # 分发 card action
    if data.get("type") == "card":
        action_val = data.get("action", {}).get("value", {})
        act = action_val.get("action", "")
        key = action_val.get("news_key", "")
        if act == "approve":
            pub_time = action_val.get("pub_time", "")
            update_news(key, {"publish_xhs": 1, "publish_time": pub_time})
        elif act == "skip":
            update_news(key, {"status": "skipped"})
        elif act == "regenerate":
            set_state(f"regen_{key}", {"key": key})
        elif act == "adopt_weights":
            weights = action_val.get("weights", {})
            set_config("dim_weights", weights)
    return jsonify({"code": 0})

@app.route('/api/gallery-upload/<key>', methods=['POST'])
def api_gallery_upload(key):
    data = request.json or {}
    selected = data.get('selected', [])
    urls = upload_selected(key, selected)
    return jsonify({"ok": True, "urls": urls})

@app.route('/api/gallery-status/<key>')
def api_gallery_status(key):
    return jsonify(gstatus(key))

@app.route('/api/metrics-history/<key>')
def api_metrics_history(key):
    """文章 metrics_history 时间序列，最近 72 个快照"""
    from scripts.sqlite_db import _connect
    with _connect() as db:
        rows = db.execute(
            "SELECT collected_at, views, likes, saves, comments, impression, click_rate "
            "FROM metrics_history WHERE news_key=? ORDER BY collected_at ASC LIMIT 72",
            (key,)
        ).fetchall()
    snapshots = [dict(r) for r in rows]
    return jsonify({"snapshots": snapshots, "count": len(snapshots)})


@app.route('/local-image')
def local_image():
    """代理本地图片文件（按当前 GALLERY_CACHE_DIR 归一化迁移来的绝对路径）"""
    from config.yahoo_conf import resolve_image_path
    path = resolve_image_path(request.args.get('path', ''))
    if not path or not os.path.exists(path):
        return '', 404
    return send_file(path)

TOPIC_CACHE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>话题缓存 — XHS 运营管理</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font:13px -apple-system,"PingFang SC",sans-serif;background:#f4f4f8;color:#2a2a3c;padding:24px}
.wrap{max-width:1150px;margin:0 auto}
h1{font-size:16px;margin-bottom:4px}
.sub{color:#7878a0;font-size:12px;margin-bottom:18px}
.card{background:#fff;border:1px solid #e6e6f0;border-radius:10px;padding:16px;margin-bottom:16px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
input,select{font:12px inherit;padding:6px 10px;border:1px solid #dcdce8;border-radius:7px;outline:none;background:#fff}
input:focus,select:focus{border-color:#6c63ff}
button{font:12px inherit;padding:6px 14px;border:0;border-radius:7px;background:#6c63ff;color:#fff;cursor:pointer}
button.gray{background:#e8e8f2;color:#4a4a66}
button.red{background:#ef4444}
button:disabled{opacity:.45;cursor:default}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{padding:7px 9px;text-align:left;border-bottom:1px solid #eeeef6}
th{color:#7878a0;font-weight:600;background:#fafafd;cursor:pointer;user-select:none;white-space:nowrap}
th:hover{color:#6c63ff}
th .arw{color:#6c63ff;font-size:10px}
td code{font-family:ui-monospace,Menlo,monospace;font-size:11px;color:#4a4a66}
.msg{font-size:12px;margin-left:10px}
.msg.ok{color:#16a34a}.msg.err{color:#ef4444}
a{color:#6c63ff;text-decoration:none;font-size:12px}
.pager{display:flex;gap:6px;align-items:center;justify-content:flex-end;margin-top:12px;font-size:12px;color:#7878a0}
.pager .pinfo{margin:0 8px}
</style></head>
<body><div class="wrap">
<h1>话题缓存</h1>
<div class="sub">话题名 → 小红书话题 id 映射。发布时命中即用事务直插 chip，免去敲字与下拉。 <a href="/">← 返回首页</a></div>

<div class="card">
  <div class="row">
    <input id="f_name" placeholder="话题名（不含 #）" style="width:170px">
    <input id="f_id" placeholder="topic_id（24位）" style="width:230px">
    <input id="f_link" placeholder="link（可空）" style="width:260px">
    <button onclick="save()">新增 / 更新</button>
    <span class="msg" id="msg"></span>
  </div>
</div>

<div class="card">
  <div class="row" style="margin-bottom:10px">
    <input id="q" placeholder="搜索话题名 / topic_id…" style="width:240px">
    <button class="gray" onclick="doSearch()">搜索</button>
    <button class="gray" onclick="clearSearch()">清空</button>
    <span class="sub" id="count" style="margin:0"></span>
  </div>
  <table>
    <thead><tr>
      <th onclick="sortBy('name')">话题名 <span class="arw" id="s_name"></span></th>
      <th>topic_id</th>
      <th onclick="sortBy('source')">来源 <span class="arw" id="s_source"></span></th>
      <th onclick="sortBy('hit_count')">命中 <span class="arw" id="s_hit_count"></span></th>
      <th onclick="sortBy('updated_at')">更新时间 <span class="arw" id="s_updated_at"></span></th>
      <th></th>
    </tr></thead>
    <tbody id="tb"></tbody>
  </table>
  <div class="pager">
    <button class="gray" id="b_first" onclick="go(1)">« 首页</button>
    <button class="gray" id="b_prev" onclick="go(state.page-1)">‹ 上一页</button>
    <span class="pinfo" id="pinfo"></span>
    <button class="gray" id="b_next" onclick="go(state.page+1)">下一页 ›</button>
    <button class="gray" id="b_last" onclick="go(lastPage())">末页 »</button>
    <select id="psize" onchange="setSize(this.value)">
      <option value="50">50 / 页</option>
      <option value="100">100 / 页</option>
      <option value="200">200 / 页</option>
      <option value="500">500 / 页</option>
    </select>
  </div>
</div>
</div>
<script>
const state = {page:1, size:50, sort_by:'name', sort_dir:'asc', q:''};
let TOTAL = 0;
function esc(s){ return (s==null?'':String(s)).replace(/[&<>"]/g, function(c){ return ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'})[c]; }); }
function lastPage(){ return Math.max(1, Math.ceil(TOTAL / state.size)); }
function renderSort(){
  ['name','source','hit_count','updated_at'].forEach(function(c){
    const el = document.getElementById('s_'+c);
    if (el) el.textContent = (state.sort_by === c) ? (state.sort_dir === 'asc' ? '▲' : '▼') : '';
  });
}
function renderRows(rows){
  document.getElementById('tb').innerHTML = rows.map(function(r){
    return '<tr><td><b>'+esc(r.name)+'</b></td>'
      + '<td><code>'+esc(r.topic_id)+'</code></td>'
      + '<td>'+esc(r.source||'-')+'</td>'
      + '<td>'+esc(r.hit_count||0)+'</td>'
      + '<td>'+esc((r.updated_at||'').slice(0,16))+'</td>'
      + '<td><button class="gray" onclick="fill(\''+esc(r.name)+'\',\''+esc(r.topic_id)+'\',\''+esc(r.link||'')+'\')">编辑</button> '
      + '<button class="red" onclick="del(\''+esc(r.name)+'\')">删除</button></td></tr>';
  }).join('') || '<tr><td colspan="6" style="color:#aaa;padding:16px">没有数据</td></tr>';
}
function renderPager(){
  const lp = lastPage();
  document.getElementById('pinfo').textContent = '第 ' + state.page + ' / ' + lp + ' 页 · 共 ' + TOTAL + ' 条';
  document.getElementById('count').textContent = TOTAL + ' 条';
  document.getElementById('b_first').disabled = state.page <= 1;
  document.getElementById('b_prev').disabled  = state.page <= 1;
  document.getElementById('b_next').disabled  = state.page >= lp;
  document.getElementById('b_last').disabled  = state.page >= lp;
  renderSort();
}
async function load(){
  const p = new URLSearchParams({page:state.page, page_size:state.size,
                                sort_by:state.sort_by, sort_dir:state.sort_dir, q:state.q});
  const d = await (await fetch('/api/topic-cache?' + p.toString())).json();
  TOTAL = d.total || 0;
  if (state.page > lastPage()) { state.page = lastPage(); return load(); }
  renderRows(d.rows || []);
  renderPager();
}
function go(p){ const lp = lastPage(); state.page = Math.min(Math.max(1, p), lp); load(); }
function setSize(s){ state.size = parseInt(s,10); state.page = 1; load(); }
function sortBy(col){
  if (state.sort_by === col) state.sort_dir = (state.sort_dir === 'asc' ? 'desc' : 'asc');
  else { state.sort_by = col; state.sort_dir = 'asc'; }
  state.page = 1; load();
}
function doSearch(){ state.q = document.getElementById('q').value.trim(); state.page = 1; load(); }
function clearSearch(){ document.getElementById('q').value=''; state.q=''; state.page=1; load(); }
document.getElementById('q').addEventListener('keydown', function(e){ if(e.key==='Enter') doSearch(); });
function fill(n,i,l){ document.getElementById('f_name').value=n; document.getElementById('f_id').value=i; document.getElementById('f_link').value=l; }
function say(t, ok){ const m=document.getElementById('msg'); m.textContent=t; m.className='msg '+(ok?'ok':'err'); setTimeout(function(){ m.textContent=''; }, 3000); }
async function save(){
  const name=document.getElementById('f_name').value.trim().replace(/^#/,'');
  const topic_id=document.getElementById('f_id').value.trim();
  const link=document.getElementById('f_link').value.trim();
  if(!name||!topic_id){ say('话题名和 topic_id 必填', false); return; }
  const d = await (await fetch('/api/topic-cache', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({name:name, topic_id:topic_id, link:link})})).json();
  if(d.ok){ say('已保存', true); document.getElementById('f_id').value=''; document.getElementById('f_link').value=''; load(); }
  else say(d.msg||'保存失败', false);
}
async function del(name){
  if(!confirm('删除 '+name+' ？')) return;
  const d = await (await fetch('/api/topic-cache/'+encodeURIComponent(name), {method:'DELETE'})).json();
  if(d.ok){ say('已删除', true); load(); } else say('删除失败', false);
}
load();
</script>
</body></html>
"""

INDEX_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>XHS 运营管理</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#f4f4f8;
  --sidebar-bg:#1c1c2e;
  --card-bg:#fff;
  --text:#1a1a2e;
  --text2:#6b7280;
  --text3:#9ca3af;
  --border:#e5e7eb;
  --red:#ef4444;
  --orange:#f97316;
  --green:#10b981;
  --blue:#3b82f6;
  --purple:#8b5cf6;
  --accent:#3b82f6;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);
  --radius:10px;
  --font:-apple-system,'Inter','PingFang SC','Segoe UI',sans-serif
}
body{font:13px/1.5 var(--font);background:var(--bg);color:var(--text);height:100vh;display:flex;overflow:hidden;-webkit-font-smoothing:antialiased}

/* ── Sidebar ────────────────────────────── */
.sidebar{width:220px;min-width:220px;background:var(--sidebar-bg);display:flex;flex-direction:column;padding:18px 0;overflow-y:auto;flex-shrink:0}
.sidebar-logo{padding:0 18px 20px;font-size:14px;font-weight:700;color:#fff;letter-spacing:-.01em;display:flex;align-items:center;gap:9px}
.sidebar-logo-icon{width:30px;height:30px;border-radius:8px;background:linear-gradient(135deg,#6c63ff,#a78bfa);display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}
.sidebar-section{padding:0 10px;margin-bottom:4px}
.sidebar-label{font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;color:#3a3a5a;padding:0 8px;margin-bottom:5px}
.nav-item{display:flex;align-items:center;gap:9px;padding:7px 10px;border-radius:7px;cursor:pointer;color:#7878a0;font-size:12px;font-weight:500;transition:all .15s;user-select:none}
.nav-item:hover{background:rgba(255,255,255,.06);color:#c0c0d8}
.nav-item.active{background:rgba(108,99,255,.22);color:#fff}
.nav-item .ni{font-size:13px;width:16px;text-align:center}
.nav-badge{margin-left:auto;background:var(--red);color:#fff;font-size:9px;font-weight:700;padding:1px 5px;border-radius:10px;min-width:16px;text-align:center}
.nav-badge.g{background:var(--green)}
.sidebar-divider{height:1px;background:#252538;margin:8px 10px}
.sidebar-stats{padding:14px 18px;margin-top:auto}
.sidebar-stat-label{font-size:9px;color:#3a3a5a;letter-spacing:.08em;text-transform:uppercase;margin-bottom:8px}
.sidebar-stat-row{display:flex;justify-content:space-between}
.sidebar-stat-item{text-align:center}
.sidebar-stat-num{font-size:17px;font-weight:700;color:#fff;line-height:1}
.sidebar-stat-name{font-size:9px;color:#3a3a5a;margin-top:2px}

/* ── Main ───────────────────────────────── */
.main{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}

/* Topbar */
.topbar{background:var(--card-bg);border-bottom:1px solid var(--border);padding:0 20px;height:50px;display:flex;align-items:center;gap:12px;flex-shrink:0}
.topbar-title{font-size:13px;font-weight:600;color:var(--text)}
.topbar-spacer{flex:1}
.topbar-search{display:flex;align-items:center;gap:7px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:5px 11px;width:210px}
.topbar-search input{border:none;background:none;font-size:12px;color:var(--text);outline:none;width:100%}
.topbar-search input::placeholder{color:var(--text3)}

/* Metric cards */
.metrics{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;padding:14px 20px 0;flex-shrink:0}
.mc{background:var(--card-bg);border-radius:var(--radius);padding:14px 16px;box-shadow:var(--shadow);border:1px solid var(--border);display:flex;align-items:flex-start;gap:12px}
.mc-icon{width:36px;height:36px;border-radius:9px;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0}
.mc-icon.blue{background:#eff6ff}.mc-icon.green{background:#f0fdf4}.mc-icon.orange{background:#fff7ed}.mc-icon.purple{background:#f5f3ff}
.mc-num{font-size:20px;font-weight:700;line-height:1;color:var(--text)}
.mc-label{font-size:11px;color:var(--text2);margin-top:2px}
.mc-sub{font-size:10px;margin-top:4px;color:var(--text3)}
.mc-sub.up{color:var(--green)}.mc-sub.warn{color:var(--orange)}

/* Filter strip */
.filter-strip{padding:10px 20px 0;flex-shrink:0;display:flex;align-items:center;gap:7px;flex-wrap:wrap}
.fs{height:30px;padding:0 9px;border:1px solid var(--border);border-radius:7px;font-size:11.5px;color:var(--text);background:var(--card-bg);cursor:pointer;outline:none;appearance:none;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='10' viewBox='0 0 12 12'%3E%3Cpath fill='%23888' d='M6 8L1 3h10z'/%3E%3C/svg%3E");background-repeat:no-repeat;background-position:right 7px center;padding-right:24px}
.fs:focus{border-color:var(--blue);box-shadow:0 0 0 3px rgba(59,130,246,.1)}
.fd{height:30px;padding:0 9px;border:1px solid var(--border);border-radius:7px;font-size:11.5px;color:var(--text);background:var(--card-bg);outline:none}
.filter-divider{width:1px;height:18px;background:var(--border);flex-shrink:0}

/* Buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;height:30px;padding:0 13px;border:none;border-radius:7px;cursor:pointer;font-size:11.5px;font-weight:500;white-space:nowrap;transition:all .12s;border:1px solid transparent}
.btn:hover{opacity:.88}
.btn:active{transform:scale(.98)}
.btn:disabled{opacity:.35;pointer-events:none}
.btn-red{background:var(--red);color:#fff;border-color:var(--red)}
.btn-orange{background:var(--orange);color:#fff;border-color:var(--orange)}
.btn-gray{background:#f3f4f6;color:var(--text2);border-color:var(--border)}
.btn-dark{background:var(--text);color:#fff}
.btn-outline{background:var(--card-bg);border-color:var(--border);color:var(--text2)}
.btn-outline:hover{border-color:var(--text3);color:var(--text)}
.btn-sm{height:26px;padding:0 10px;font-size:11px;border-radius:6px}
.btn-xs{height:22px;padding:0 8px;font-size:10px;border-radius:5px}

/* Section titles */
.sec-title{font-size:13px;font-weight:600;letter-spacing:-.01em;display:flex;align-items:center;gap:6px}

/* Inputs */
input,select,textarea{font:inherit;outline:none;transition:border-color .15s,box-shadow .15s;color:var(--text)}
input:focus,select:focus{border-color:var(--accent)!important;box-shadow:0 0 0 3px rgba(59,130,246,.12)}
input[type=text],input[type=date],input[type=datetime-local],input[type=number],select{padding:6px 10px;border:1px solid var(--border);border-radius:7px;font-size:12px;background:#fff}
input[type=date],input[type=datetime-local]{width:135px}
.toolbar{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.sep{width:1px;height:20px;background:var(--border);flex-shrink:0}

/* Fetch drawer */
.fetch-drawer{display:none;background:var(--card-bg);border-bottom:1px solid var(--border);padding:14px 20px;flex-shrink:0}
.fetch-drawer.open{display:block}
.side-panel{display:none;background:#fafafa;border:1px solid var(--border);border-radius:8px;padding:14px;margin-top:10px}
.side-panel.open{display:block}
.side-panel-header{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.side-panel-header span{font-size:12px;font-weight:700}

/* Action bar */
.action-bar-wrap{flex-shrink:0;padding:0 20px}
.action-bar{display:none;background:var(--sidebar-bg);color:#fff;padding:8px 14px;border-radius:8px;gap:8px;align-items:center;margin-bottom:8px}
.action-bar.active{display:flex}
.action-bar .lbl{font-size:11.5px;font-weight:600}
.action-bar .sp{flex:1}
.action-bar .btn-ghost{height:26px;padding:0 10px;font-size:11px;border-radius:5px;background:rgba(255,255,255,.1);border:1px solid rgba(255,255,255,.15);color:#fff;cursor:pointer}
.action-bar .btn-ghost:hover{background:rgba(255,255,255,.18)}

/* Table */
.table-wrap-outer{flex:1;overflow:hidden;padding:8px 20px 16px;display:flex;flex-direction:column}
.table-card{flex:1;background:var(--card-bg);border-radius:var(--radius);box-shadow:var(--shadow);border:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
.table-hdr{padding:10px 14px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:8px;flex-shrink:0}
.table-hdr-title{font-size:12px;font-weight:600;color:var(--text)}
.table-hdr-count{font-size:11px;color:var(--text3)}
.table-scroll{flex:1;overflow-y:auto}
table{width:100%;border-collapse:collapse}
thead th{position:sticky;top:0;z-index:1;background:#fafafa;border-bottom:1px solid var(--border);padding:8px 12px;text-align:left;font-size:10px;font-weight:600;color:var(--text2);text-transform:uppercase;letter-spacing:.06em;white-space:nowrap;cursor:pointer;user-select:none}
thead th:hover{color:var(--text)}
tbody tr{border-bottom:1px solid #f3f4f6;transition:background .08s}
tbody tr:hover{background:#fafbff}
tbody td{padding:8px 12px;vertical-align:middle;font-size:12.5px}

/* Thumb */
.thumb-img{width:42px;height:42px;border-radius:6px;object-fit:cover;display:block}
.thumb-empty{width:42px;height:42px;border-radius:6px;background:linear-gradient(135deg,#f0f0f5,#e5e5ea);display:flex;align-items:center;justify-content:center;font-size:17px}

/* Title cell */
.tc{display:flex;align-items:flex-start;gap:10px}
.tc-body{min-width:0}
.tc-kw{display:inline-block;background:#f0f0ff;color:var(--purple);font-size:9.5px;font-weight:600;padding:1px 6px;border-radius:4px;margin-bottom:3px;letter-spacing:.02em}
.tc-title{font-size:12.5px;font-weight:600;color:var(--text);line-height:1.4;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;max-width:380px}
.tc-title:hover{color:var(--red)}
.tc-snip{font-size:11px;color:var(--text3);margin-top:2px;overflow:hidden;white-space:nowrap;text-overflow:ellipsis;max-width:240px}
.tc-snip:hover{overflow:visible;white-space:normal;max-width:none;background:var(--card-bg);padding:4px 8px;border-radius:6px;box-shadow:0 4px 16px rgba(0,0,0,.12);position:relative;z-index:10}

/* Badges */
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10.5px;font-weight:600}
.badge-green{background:#f0fdf4;color:#15803d}
.badge-red{background:#fef2f2;color:#b91c1c}
.badge-gray{background:#f3f4f6;color:#6b7280}

/* Score */
.score{display:inline-flex;align-items:center;justify-content:center;padding:2px 8px;border-radius:20px;font-size:11px;font-weight:700}
.score-hi{background:#f0fdf4;color:#15803d}
.score-mid{background:#fffbeb;color:#92400e}
.score-lo{background:#fef2f2;color:#b91c1c}

/* Format */
.fmt{padding:2px 7px;border-radius:5px;font-size:10px;font-weight:600}
.fmt-news{background:#eff6ff;color:#2563eb}
.fmt-story{background:#f5f3ff;color:#7c3aed}
.fmt-ranking{background:#f0fdf4;color:#15803d}
.fmt-other{background:#f3f4f6;color:#6b7280}

/* Tags */
.tag{display:inline-block;background:#f0f0ff;color:#5856d6;padding:1px 7px;border-radius:20px;font-size:10.5px;margin:1px 2px;font-weight:500}

/* Pub toggle */
.pub-toggle{width:30px;height:17px;border-radius:9px;background:var(--border);cursor:pointer;position:relative;transition:background .2s;flex-shrink:0}
.pub-toggle.on{background:var(--green)}
.pub-toggle::after{content:'';position:absolute;width:13px;height:13px;background:#fff;border-radius:50%;top:2px;left:2px;transition:left .2s;box-shadow:0 1px 2px rgba(0,0,0,.2)}
.pub-toggle.on::after{left:15px}

/* Pagination */
.table-foot{padding:8px 14px;border-top:1px solid var(--border);display:flex;align-items:center;gap:5px;flex-shrink:0}
.table-foot-info{font-size:11px;color:var(--text3);flex:1}
.pg-btn{width:26px;height:26px;border:1px solid var(--border);border-radius:6px;background:var(--card-bg);cursor:pointer;font-size:12px;display:flex;align-items:center;justify-content:center;color:var(--text2)}
.pg-btn.cur{background:var(--text);color:#fff;border-color:var(--text)}
.pg-btn:hover:not(.cur){border-color:var(--text3)}

/* Modal */
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.35);z-index:200;justify-content:center;align-items:center;backdrop-filter:blur(4px)}
.modal.active{display:flex}
.modal-card{background:var(--card-bg);border-radius:14px;max-width:700px;width:92%;max-height:82vh;overflow-y:auto;padding:26px;box-shadow:0 20px 60px rgba(0,0,0,.18)}
.modal img.preview-img{max-width:100%;max-height:300px;border-radius:10px;margin-bottom:14px}
.modal h2{font-size:17px;margin-bottom:8px;letter-spacing:-.01em}
.modal .meta{color:var(--text2);font-size:12px;margin-bottom:14px}
.modal .section{margin:12px 0;padding:10px 0;border-top:1px solid var(--border)}
.modal .section h4{font-size:11px;color:var(--text2);margin-bottom:6px;text-transform:uppercase;letter-spacing:.04em}
.link{color:var(--text);text-decoration:none}
.link:hover{color:var(--red)}
</style>
</head>
<body>

<!-- ── Sidebar ─────────────────────────────────── -->
<div class="sidebar">
  <div class="sidebar-logo">
    <div class="sidebar-logo-icon" style="background:none;padding:0">
      <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" width="30" height="30">
        <defs>
          <linearGradient id="lg1" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#6c63ff"/>
            <stop offset="100%" stop-color="#a78bfa"/>
          </linearGradient>
          <linearGradient id="lg2" x1="0%" y1="0%" x2="100%" y2="100%">
            <stop offset="0%" stop-color="#ef4444"/>
            <stop offset="100%" stop-color="#f97316"/>
          </linearGradient>
        </defs>
        <rect width="40" height="40" rx="10" fill="url(#lg1)"/>
        <line x1="11" y1="11" x2="29" y2="29" stroke="white" stroke-width="3.5" stroke-linecap="round" opacity="0.9"/>
        <line x1="29" y1="11" x2="11" y2="29" stroke="white" stroke-width="3.5" stroke-linecap="round" opacity="0.9"/>
        <circle cx="29" cy="11" r="5" fill="url(#lg2)"/>
        <circle cx="29" cy="11" r="3" fill="white" opacity="0.9"/>
      </svg>
    </div>
    XHS 运营
  </div>

  <div class="sidebar-section">
    <div class="sidebar-label">内容管理</div>
    <div class="nav-item active" id="newsNavItem" onclick="switchToNewsView()">
      <span class="ni">📋</span> 文章列表
      <span class="nav-badge g" id="sidebarPending" style="display:none">0</span>
    </div>
  </div>

  <div class="sidebar-divider"></div>

  <div class="sidebar-section">
    <div class="sidebar-label">运营工具</div>
    <div class="nav-item" onclick="switchToNewsView();toggleFetchDrawer()">
      <span class="ni">🔍</span> 抓取管理
    </div>
    <div class="nav-item" onclick="switchToNewsView();toggleConfigPanel()">
      <span class="ni">⚙️</span> 策略配置
    </div>
    <div class="nav-item" onclick="switchToNewsView();toggleTagPanel()">
      <span class="ni">🏷️</span> 标签配置
    </div>
    <div class="nav-item" id="wechatNavItem" onclick="switchToWechatView()">
      <span class="ni">💬</span> 公众号
      <span class="nav-badge g" id="wechatBadge" style="display:none">0</span>
    </div>
    <div class="nav-item" id="topicNavItem" onclick="switchToQueueView('topic')">
      <span class="ni">#️⃣</span> 话题缓存
    </div>
    <div class="nav-item" id="feedbackNavItem" onclick="switchToQueueView('feedback')">
      <span class="ni">📈</span> 发布规律
    </div>
  </div>

  <div class="sidebar-divider"></div>

  <div class="sidebar-section">
    <div class="sidebar-label">运营队列</div>
    <div class="nav-item" id="manualNavItem" onclick="switchToQueueView('manual')">
      <span class="ni">🚩</span> 待人工
      <span class="nav-badge g" id="navBadgeManual" style="display:none">0</span>
    </div>
    <div class="nav-item" id="failNavItem" onclick="switchToQueueView('fail')">
      <span class="ni">❌</span> 失败稿
      <span class="nav-badge g" id="navBadgeFail" style="display:none">0</span>
    </div>
    <div class="nav-item" id="splitNavItem" onclick="switchToQueueView('split')">
      <span class="ni">🧩</span> 拆篇预览
    </div>
  </div>

  <div class="sidebar-section">
    <div class="sidebar-label">术语</div>
    <div class="nav-item" id="lexiconNavItem" onclick="switchToQueueView('lexicon')">
      <span class="ni">📚</span> 术语表
    </div>
  </div>

  <div class="sidebar-section">
    <div class="sidebar-label">系统</div>
    <div class="nav-item" id="tokenNavItem" onclick="switchToQueueView('token')">
      <span class="ni">💰</span> Token 用量
    </div>
  </div>

  <div class="sidebar-divider"></div>

  <div class="sidebar-stats">
    <div class="sidebar-stat-label">账号概况</div>
    <div class="sidebar-stat-row">
      <div class="sidebar-stat-item">
        <div class="sidebar-stat-num" id="sidebarTotal">—</div>
        <div class="sidebar-stat-name">总入库</div>
      </div>
      <div class="sidebar-stat-item">
        <div class="sidebar-stat-num" id="sidebarToday" style="color:#10b981">—</div>
        <div class="sidebar-stat-name">今日</div>
      </div>
      <div class="sidebar-stat-item">
        <div class="sidebar-stat-num" id="sidebarPublished" style="color:#8b5cf6">—</div>
        <div class="sidebar-stat-name">已发</div>
      </div>
    </div>
  </div>
</div>

<!-- ── Main ───────────────────────────────────── -->
<div class="main">

  <!-- Topbar -->
  <div class="topbar">
    <div class="topbar-title">文章列表</div>
    <span id="taskBar" style="display:none;font-size:11.5px;cursor:pointer;color:var(--orange);font-weight:600;background:#fff7ed;padding:4px 10px;border-radius:6px;border:1px solid #fed7aa" onclick="showTaskModal()"></span>
    <div class="topbar-spacer"></div>
    <button class="btn btn-outline" onclick="location.reload()">🔄 刷新</button>
  </div>

<div id="newsView" style="flex:1;overflow:hidden;display:flex;flex-direction:column;min-height:0">
  <!-- Fetch drawer (hidden by default, toggled from sidebar) -->
  <div class="fetch-drawer" id="fetchDrawer">
    <div style="display:flex;gap:8px;align-items:center;margin-bottom:10px">
      <div class="sec-title">🔍 关键词抓取</div>
      <span style="flex:1"></span>
      <button class="btn btn-xs btn-gray" onclick="selectAllKw(true)">全选</button>
      <button class="btn btn-xs btn-gray" onclick="selectAllKw(false)">全不选</button>
      <button class="btn btn-xs btn-gray" onclick="addKeyword()">+ 自定义</button>
      <button class="btn btn-xs btn-gray" onclick="resetKeywords()">重置预置</button>
      <span style="font-size:11px;color:var(--text2)" id="kwSummary"></span>
      <button class="btn btn-red btn-sm" onclick="triggerFetch('keywords')" id="kwBtn">🔍 开始抓取</button>
    </div>
    <div id="kwGrid" style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px"></div>
    <div style="border-top:1px solid var(--border);margin:10px 0 8px"></div>
    <div style="display:flex;align-items:center;gap:10px">
      <div class="sec-title" style="margin:0">📰 推荐抓取</div>
      <span style="font-size:11px;color:var(--text2)">Yahoo 首页推荐流</span>
      <span style="flex:1"></span>
      <span style="font-size:11px;color:var(--text2)">条数</span>
      <input type="number" id="recomMax" value="10" min="1" max="50" style="width:52px;padding:5px;border:1px solid var(--border);border-radius:7px;font-size:12px;text-align:center">
      <button class="btn btn-red btn-sm" onclick="triggerFetch('recom')" id="recomBtn">📰 开始抓取</button>
    </div>
  </div>

  <!-- Metric cards -->
  <div class="metrics">
    <div class="mc">
      <div class="mc-icon blue">📰</div>
      <div><div class="mc-num" id="mcTotal">—</div><div class="mc-label">总文章数</div><div class="mc-sub up" id="mcToday">今日 —</div></div>
    </div>
    <div class="mc">
      <div class="mc-icon green">✅</div>
      <div><div class="mc-num" id="mcPublished">—</div><div class="mc-label">已发布</div><div class="mc-sub">累计发布</div></div>
    </div>
    <div class="mc">
      <div class="mc-icon orange">⏳</div>
      <div><div class="mc-num" id="mcPending">—</div><div class="mc-label">待发布</div><div class="mc-sub warn" id="mcPendingSub"></div></div>
    </div>
    <div class="mc">
      <div class="mc-icon purple">⭐</div>
      <div><div class="mc-num" id="mcScore">—</div><div class="mc-label">今日平均分</div><div class="mc-sub">标题评分</div></div>
    </div>
  </div>

  <!-- Filter strip -->
  <div class="filter-strip">
    <div class="topbar-search" style="width:200px;background:#fff;border:1px solid var(--border);border-radius:7px;height:30px;padding:0 10px">
      <span style="color:var(--text3);font-size:12px">🔍</span>
      <input id="search" placeholder="搜索..." oninput="clearTimeout(_st);_st=setTimeout(()=>{page=0;loadList()},400)" style="border:none;background:none;font-size:12px;outline:none;width:100%">
    </div>
    <div class="filter-divider"></div>
    <input type="date" id="dateFrom" class="fd" title="开始日期" onchange="page=0;loadList()">
    <span style="font-size:11px;color:var(--text3)">→</span>
    <input type="date" id="dateTo" class="fd" title="结束日期" onchange="page=0;loadList()">
    <div class="filter-divider"></div>
    <select id="fetchBy" class="fs" onchange="page=0;loadList()"><option value="">全部来源</option></select>
    <select id="status" class="fs" onchange="page=0;loadList()"><option value="active">活跃</option><option value="discarded">已丢弃</option><option value="archived">已归档</option></select>
    <select id="publishXhs" class="fs" onchange="page=0;loadList()"><option value="">发布状态</option><option value="published">已发布</option><option value="pending">待发布</option><option value="unpublished">未发布</option></select>
    <select id="fmtFilter" class="fs" onchange="page=0;loadList()"><option value="">全部体裁</option><option value="news">news</option><option value="story">story</option><option value="ranking">ranking</option><option value="comparison">comparison</option></select>
    <select id="scoreFilter" class="fs" onchange="page=0;loadList()"><option value="">全部评分</option><option value="5">≥5</option><option value="6">≥6</option><option value="7">≥7</option><option value="8">≥8</option></select>
    <div class="filter-divider"></div>
    <select id="preselectedFilter" class="fs" onchange="page=0;loadList()"><option value="">全部</option><option value="1">★ 已预选</option><option value="0">☆ 未预选</option></select>
    <select id="gradeFilter" class="fs" onchange="page=0;loadList()"><option value="">全部评级</option><option value="S">S</option><option value="A">A</option><option value="AKB大TOP">AKB大TOP</option><option value="B">B</option><option value="C">C</option><option value="__none__">未评级</option></select>
    <div class="filter-divider"></div>
    <button class="btn-ghost btn-sm" onclick="openLogin()" title="小红书扫码登录">🔑 小红书登录<span id="loginDot" style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#999;margin-left:6px;vertical-align:middle"></span><span id="loginAcct" style="margin-left:6px;color:#9fb0c8;font-size:11px"></span></button>
    <button class="btn-ghost btn-sm" onclick="openChrome()" title="查看容器内 Chrome 画面">🖥 查看 Chrome</button>
  </div>

  <!-- Action bars -->
  <div class="action-bar-wrap" style="padding-top:8px">
    <div class="action-bar" id="archiveBar">
      <span class="lbl" id="archiveCount">已选 0 条</span>
      <span class="sp"></span>
      <button class="btn-ghost" onclick="archiveSelected()">📦 归档</button>
      <button class="btn-ghost" onclick="collectBatchMetrics()">🔄 回收数据</button>
    </div>
    <div class="action-bar" id="publishBar">
      <span class="lbl"><b id="pendingCount">0</b> 条待发布</span>
      <span class="sp"></span>
      <button class="btn btn-red btn-sm" onclick="triggerPublish()" id="pubBtn">📤 发布小红书</button>
    </div>
  </div>

  <!-- Table -->
  <div class="table-wrap-outer">
    <div class="table-card">
      <div class="table-hdr">
        <input type="checkbox" onclick="selectAllRows(this.checked)" title="全选" style="width:14px;height:14px">
        <button class="btn btn-xs btn-outline" onclick="setQuickTime(8,0)">今8</button>
        <button class="btn btn-xs btn-outline" onclick="setQuickTime(12,0)">今12</button>
        <button class="btn btn-xs btn-outline" onclick="setQuickTime(18,0)">今18</button>
        <button class="btn btn-xs btn-outline" onclick="setQuickTime(8,1)">明8</button>
        <button class="btn btn-xs btn-outline" onclick="setQuickTime(12,1)">明12</button>
        <button class="btn btn-xs btn-outline" onclick="setQuickTime(18,1)">明18</button>
        <button class="btn btn-xs btn-outline" onclick="clearPostTime()" style="color:var(--red)">✕</button>
        <span class="table-hdr-title">文章列表</span>
        <span class="table-hdr-count" id="tableCount"></span>
        <div style="flex:1"></div>
        <select class="fs" style="height:26px;font-size:11px" onchange="pageSize=parseInt(this.value);page=0;loadList()" id="pageSizeSelect">
          <option value="50">50条/页</option><option value="100">100条/页</option><option value="200">200条/页</option>
        </select>
      </div>
      <div class="table-scroll">
        <table>
          <thead><tr>
            <th style="width:28px"></th>
            <th style="width:30px"></th>
            <th style="width:50px">封面</th>
            <th>标题</th>
            <th style="width:70px">体裁</th>
            <th style="width:56px">评级</th>
            <th onclick="setSort('title_score')" style="width:64px">评分 ↕</th>
            <th style="width:60px">状态</th>
            <th style="width:50px">发布</th>
            <th style="width:60px">内容</th>
            <th style="width:50px">发布</th>
            <th style="width:140px">预发布</th>
            <th onclick="setSort('publish_time')" style="width:88px">XHS发布 ↕</th>
            <th onclick="setSort('created_at')" style="width:88px">入库 ↕</th>
            <th onclick="setSort('pub_time')" style="width:88px">新闻 ↕</th>
          </tr></thead>
          <tbody id="tbody"></tbody>
        </table>
      </div>
      <div class="table-foot" id="pager"></div>
    </div>
  </div>

</div><!-- /newsView -->
<div id="wechatView" style="display:none;flex:1;overflow:hidden;flex-direction:column">
  <div id="wdBody" style="flex:1;overflow-y:auto;padding:8px 20px"></div>
</div>
<div id="queueView" style="display:none;flex:1;overflow:hidden;flex-direction:column">
  <div id="queueBody" style="flex:1;overflow-y:auto;padding:8px 20px 24px"></div>
</div>
</div><!-- /main -->

<!-- Config modal -->
<div class="modal" id="configModal" onclick="if(event.target===this)closeConfigModal()">
  <div class="modal-card" style="max-width:560px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
      <h2 style="margin:0">⚙️ 策略配置</h2>
      <div style="display:flex;gap:8px">
        <button class="btn btn-red btn-sm" onclick="saveConfig()">💾 保存</button>
        <button class="btn btn-gray btn-sm" onclick="closeConfigModal()">关闭</button>
      </div>
    </div>
    <div id="configRows" style="display:flex;flex-direction:column;gap:8px;margin-bottom:14px"></div>
    <div style="display:flex;gap:20px;font-size:12px;color:var(--text2);flex-wrap:wrap;padding-top:10px;border-top:1px solid var(--border)">
      <label>发布阈值 <input type="number" id="cfgPublishTh" step="0.5" style="width:52px;padding:4px 6px;border:1px solid var(--border);border-radius:6px;font-size:11px;text-align:center;margin-left:4px"></label>
      <label>重试阈值 <input type="number" id="cfgRetryTh" step="0.5" style="width:52px;padding:4px 6px;border:1px solid var(--border);border-radius:6px;font-size:11px;text-align:center;margin-left:4px"></label>
      <label>默认配额 <input type="number" id="cfgDailyQuota" style="width:52px;padding:4px 6px;border:1px solid var(--border);border-radius:6px;font-size:11px;text-align:center;margin-left:4px"></label>
    </div>
  </div>
</div>

<!-- Tag modal -->
<div class="modal" id="tagModal" onclick="if(event.target===this)closeTagModal()">
  <div class="modal-card" style="max-width:600px">
    <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:16px">
      <h2 style="margin:0">🏷️ 标签配置</h2>
      <div style="display:flex;gap:8px">
        <button class="btn btn-red btn-sm" onclick="saveTagConfig()">💾 保存</button>
        <button class="btn btn-gray btn-sm" onclick="closeTagModal()">关闭</button>
      </div>
    </div>
    <div id="tagConfigRows" style="display:flex;flex-direction:column;gap:8px;font-size:11px"></div>
  </div>
</div>

<!-- Confirm modal -->
<div class="modal" id="confirmModal" style="z-index:300">
  <div class="modal-card" style="max-width:380px;text-align:center">
    <div style="font-size:32px;margin-bottom:12px" id="confirmIcon">⚠️</div>
    <p id="confirmMsg" style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:6px"></p>
    <p id="confirmSub" style="font-size:12px;color:var(--text2);margin-bottom:20px"></p>
    <div style="display:flex;gap:10px;justify-content:center">
      <button class="btn btn-gray" style="min-width:80px" onclick="_confirmResolve(false)">取消</button>
      <button class="btn btn-red" style="min-width:80px" id="confirmOkBtn" onclick="_confirmResolve(true)">确定</button>
    </div>
  </div>
</div>

<!-- Queue edit modal (话题缓存/发布规律 编辑) -->
<div class="modal" id="qEditModal" onclick="if(event.target===this)_qEditClose()">
  <div class="modal-card" style="max-width:900px">
    <h2 id="qEditTitle">编辑</h2>
    <div id="qEditBody"></div>
    <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:16px;align-items:center">
      <span id="qEditMsg" style="flex:1;font-size:12px"></span>
      <button class="btn btn-gray" onclick="_qEditClose()">取消</button>
      <button class="btn btn-red" id="qEditSaveBtn" onclick="_qEditSave()">保存</button>
    </div>
  </div>
</div>

<!-- Preview modal -->
<div class="modal" id="modal" onclick="if(event.target===this)closeModal()"><div class="modal-card" id="modalContent"></div></div>

<!-- Terminal modal -->
<div class="modal" id="taskModal" onclick="if(event.target===this)closeTaskModal()">
  <div class="modal-card" style="max-width:750px;background:#1e1e1e;color:#0f0">
    <h3 id="taskModalTitle" style="color:#fff;margin-bottom:12px">🖥️ 终端</h3>
    <pre id="taskLog" style="font:12px Menlo,monospace;white-space:pre-wrap;min-height:300px;max-height:60vh;overflow-y:auto;margin:0">等待中...</pre>
    <div style="margin-top:12px;text-align:right"><button class="btn" style="background:#dc3545;color:#fff" onclick="stopTask()">🛑 终止</button> <button class="btn" style="background:#555;color:#fff" onclick="closeTaskModal()">关闭</button></div>
  </div>
</div>

<script>
let sortBy='created_at',sortDir='DESC',page=0,pageSize=50,_st=null;
let _confirmResolve=null;
function showConfirm(msg,sub='',okLabel='确定',okClass='btn-red',icon='⚠️'){
  return new Promise(resolve=>{
    _confirmResolve=v=>{document.getElementById('confirmModal').classList.remove('active');resolve(v)};
    document.getElementById('confirmMsg').textContent=msg;
    document.getElementById('confirmSub').textContent=sub;
    document.getElementById('confirmSub').style.display=sub?'':'none';
    document.getElementById('confirmIcon').textContent=icon;
    document.getElementById('confirmOkBtn').textContent=okLabel;
    document.getElementById('confirmOkBtn').className='btn '+okClass+' min-width:80px';
    document.getElementById('confirmModal').classList.add('active');
  });
}
let activeTaskId=null,activeTaskLabel='';
const S=id=>document.getElementById(id);

function toggleFetchDrawer(){
  const d=S('fetchDrawer');
  d.classList.toggle('open');
}
function closeConfigModal(){S('configModal').classList.remove('active')}
function closeTagModal(){S('tagModal').classList.remove('active')}
function toggleConfigPanel(){
  S('configModal').classList.add('active');
  fetch('/api/agent-config').then(r=>r.json()).then(d=>{
      const map=d.yahoo_keyword_map||{},topics=d.focus_topics||[];
      let html='';
      for(const t of topics){const cfg=map[t]||{keyword:t,max:d.daily_quota||5};
        html+=`<div style="display:flex;gap:8px;align-items:center;font-size:12px"><span style="width:80px;color:var(--text2)">${esc(t)}</span><span style="color:var(--text3)">→</span><input value="${esc(typeof cfg==='object'?cfg.keyword:t)}" oninput="updateConfigMap()" style="width:80px;padding:2px 4px;border:1px solid #ddd;border-radius:3px;font-size:11px"><span style="color:var(--text3)">×</span><input type="number" value="${typeof cfg==='object'?cfg.max:d.daily_quota||5}" style="width:45px;padding:2px 4px;border:1px solid #ddd;border-radius:3px;font-size:11px;text-align:center"></div>`;}
      S('configRows').innerHTML=html;
      S('cfgPublishTh').value=d.publish_threshold||3;
      S('cfgRetryTh').value=d.retry_threshold||2;
      S('cfgDailyQuota').value=d.daily_quota||5;
    });
}
function toggleTagPanel(){
  S('tagModal').classList.add('active');
  fetch('/api/agent-config').then(r=>r.json()).then(d=>{
      const tc=d.tag_config||{};
      const must=tc.must_tags||['日本娱乐','日本文化','日本新闻'];
      const pools=tc.random_tag_pools||{fashion:['日系穿搭'],beauty:['日本化妆']};
      const kwMap=tc.keyword_tag_map||{};
      let html='';
      html+='<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:6px"><span style="width:80px;color:var(--text2);padding-top:4px">必选标签</span><div>'+_chipInput('cfgMustTags',must)+'</div></div>';
      html+='<div style="font-size:10px;color:var(--text3);margin:4px 0">随机标签池</div>';
      html+='<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:4px;margin-left:12px"><span style="width:56px;color:var(--text2);padding-top:4px;font-size:10px">时尚</span><div>'+_chipInput('cfgFashionTags',pools.fashion||[])+'</div></div>';
      html+='<div style="display:flex;align-items:flex-start;gap:8px;margin-bottom:6px;margin-left:12px"><span style="width:56px;color:var(--text2);padding-top:4px;font-size:10px">美妆</span><div>'+_chipInput('cfgBeautyTags',pools.beauty||[])+'</div></div>';
      html+='<div style="font-size:10px;color:var(--text3);margin:4px 0">keyword → 发布标签</div>';
      html+='<div id="cfgKwRows" style="display:flex;flex-direction:column;gap:4px">';
      Object.keys(kwMap).sort().forEach(kw=>{html+=`<div style="display:flex;align-items:flex-start;gap:6px" data-kw="${esc(kw)}"><input class="cfgKwKey" value="${esc(kw)}" style="width:90px;padding:2px 4px;border:1px solid #ddd;border-radius:3px;font-size:11px"><span style="color:var(--text3);padding-top:4px">→</span><div>${_chipInput('kwtags_'+esc(kw),kwMap[kw]||[])}</div><button class="btn btn-gray btn-xs" onclick="this.parentElement.remove()" style="flex-shrink:0">✕</button></div>`;});
      html+='</div><button class="btn btn-gray btn-xs" onclick="addKwRow()" style="margin-top:4px">+ 添加</button>';
      S('tagConfigRows').innerHTML=html;
      document.querySelectorAll('.chip-area').forEach(area=>{const hidden=area.parentElement.querySelector('.chip-hidden');const tags=(hidden.value||'').split(/\s+/).filter(Boolean);_renderTagChips(area,tags);});
    });
}

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

function _gradeBadge(g){
  if(!g) return '';
  var m={'S':['#fee2e2','#b91c1c'],'A':['#ffedd5','#c2410c'],'AKB大TOP':['#ede9fe','#6d28d9'],'B':['#dbeafe','#1d4ed8'],'C':['#f3f4f6','#6b7280']}[g]||['#f3f4f6','#6b7280'];
  return '<span class="badge" style="background:'+m[0]+';color:'+m[1]+';font-weight:700">'+esc(g)+'</span>';
}
function _fmtBadge(fs,cat){
  const f=fs||cat||'';
  if(f==='story')return`<span class="fmt fmt-story">长文</span>`;
  if(f==='news')return`<span class="fmt fmt-news">资讯</span>`;
  if(f==='ranking')return`<span class="fmt fmt-ranking">盘点</span>`;
  if(f==='comparison')return`<span class="fmt fmt-other">对比</span>`;
  return`<span class="fmt fmt-other">—</span>`;
}
async function loadList(){
  const p=new URLSearchParams({sort_by:sortBy,sort_dir:sortDir,limit:pageSize,offset:page*pageSize,
    search:S('search').value,date_from:S('dateFrom').value,date_to:S('dateTo').value,
    fetch_by:S('fetchBy').value,status:S('status').value,publish_xhs:S('publishXhs').value,
    fmt:S('fmtFilter').value,score_min:S('scoreFilter').value,preselected:S('preselectedFilter').value,grade:S('gradeFilter').value});
  const r=await fetch('/api/news?'+p);const d=await r.json();
  S('tbody').innerHTML=d.rows.map((n,i)=>{
    const imgSrc=n.image_url?(n.image_url.startsWith('/')?'/local-image?path='+encodeURIComponent(n.image_url):n.image_url):'';
    const thumb=imgSrc
      ?`<img src="${imgSrc}" class="thumb-img" onerror="this.className='thumb-empty';this.removeAttribute('src');this.removeAttribute('onerror')">`
      :`<div class="thumb-empty">📰</div>`;
    const badgeCls=n.status==='archived'?'badge-gray':n.status==='discarded'?'badge-red':'badge-green';
    const badgeTxt=n.status==='archived'?'归档':n.status==='discarded'?'丢弃':'活跃';
    const ts=n.title_score||0,cs=n.content_score||0,sc=ts+cs;const scCls=sc>6?'score-hi':sc>3?'score-mid':'score-lo';
    return`<tr>
    <td style="text-align:center;cursor:pointer;font-size:15px;user-select:none" onclick="event.stopPropagation();togglePreselect('${n.key}',this)" title="${n.preselected?'取消预选':'预选标记'}">${n.preselected?'★':'☆'}</td>
    <td><input type="checkbox" class="rowSel" value="${n.key}" onclick="event.stopPropagation()" onchange="updateArchiveBar()" style="width:14px;height:14px"></td>
    <td>${thumb}</td>
    <td><div class="tc"><div class="tc-body">
      ${n.fetch_by?`<div class="tc-kw">${esc(n.fetch_by)}</div>`:''}
      <a href="/detail/${n.key}" class="link tc-title" onclick="event.stopPropagation()">${esc(n.title||'')}</a>
      <div class="tc-snip">${esc((n.content||'').substring(0,60))}</div>
    </div></div></td>
    <td>${_fmtBadge(n.format,n.category)}</td>
    <td>${_gradeBadge(n.grade)}</td>
    <td><span class="score ${scCls}">${ts.toFixed(1)}/${cs.toFixed(1)}</span></td>
    <td><span class="badge ${badgeCls}">${badgeTxt}</span></td>
    <td>${(()=>{
      const locked=n.publish_xhs&&n.publish_time, pending=n.publish_xhs&&!n.publish_time;
      const cls='pub-toggle'+(n.publish_xhs?' on':'');
      const sty=locked?'opacity:.4;cursor:not-allowed':'';
      const fn=locked?'':'togglePublish(\''+n.key+'\','+(n.publish_xhs?0:1)+',this)';
      const ttl=locked?'已发布，不可撤销':(n.publish_xhs?'取消发布':'标记发布');
      return`<div class="${cls}" style="${sty}" onclick="event.stopPropagation();${fn}" title="${ttl}"></div>`;
    })()}</td>
    <td>${(()=>{
      const pm=n.publish_mode||'normal';
      const bg=pm==='free'?'#fef2f2':pm==='caption'?'#fefce8':pm==='rewritten'?'#eff6ff':'#f0fdf4';
      const fg=pm==='free'?'#b91c1c':pm==='caption'?'#a16207':pm==='rewritten'?'#2563eb':'#15803d';
      const method=n.publish_method||'post';
      return`<select onchange="event.stopPropagation();fetch('/api/news/${n.key}',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_mode:this.value})})" style="font-size:10px;padding:1px 3px;border:1px solid var(--border);border-radius:4px;background:${bg};color:${fg};cursor:pointer">
        <option value="normal" ${pm==='normal'?'selected':''}>默认</option>
        <option value="caption" ${pm==='caption'?'selected':''}>短配文</option>
        <option value="free" ${pm==='free'?'selected':''}>自由</option>
        <option value="rewritten" ${pm==='rewritten'?'selected':''}>改写文</option>
      </select>`;
    })()}</td>
    <td>${(()=>{
      const method=n.publish_method||'post';
      return`<select onchange="event.stopPropagation();fetch('/api/news/${n.key}',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_method:this.value})})" style="font-size:10px;padding:1px 3px;border:1px solid var(--border);border-radius:4px;background:${method==='export'?'#f3e8ff':'#f0fdf4'};color:${method==='export'?'#7c3aed':'#15803d'};cursor:pointer">
        <option value="post" ${method==='post'?'selected':''}>发帖</option>
        <option value="export" ${method==='export'?'selected':''}>导出</option>
      </select>`;
    })()}</td>
    <td>${(()=>{
      const locked=n.publish_xhs&&n.publish_time, pending=n.publish_xhs&&!n.publish_time;
      if(locked)return`<input type="datetime-local" value="${n.xhs_pub_time||''}" disabled style="width:130px;padding:2px 4px;border:1px solid var(--border);border-radius:5px;font-size:10px;background:#f5f5f5;color:var(--text2);opacity:.6" title="已发布">`;
      if(pending)return`<input type="datetime-local" value="${n.xhs_pub_time||''}" onchange="setPostTime('${n.key}',this.value)" style="width:130px;padding:2px 4px;border:1px solid var(--border);border-radius:5px;font-size:10px;background:var(--card-bg);color:var(--text)" title="${n.xhs_pub_time?'定时: '+n.xhs_pub_time:'不设=立即发布'}">`;
      return`<span style="font-size:10px;color:var(--text3)">—</span>`;
    })()}</td>
    <td style="font-size:11px;color:${n.publish_time?'var(--green)':'var(--text3)'};white-space:nowrap;font-weight:${n.publish_time?600:400}">${n.publish_time||'—'}</td>
    <td style="font-size:11px;color:var(--text3);white-space:nowrap">${(n.created_at||'').substring(0,16)}</td>
    <td style="font-size:11px;color:var(--text3);white-space:nowrap">${n.pub_time||'—'}</td>
  </tr>`;}).join('');

  // Update metrics
  const tot=d.total,tod=d.today,pend=d.pending,pub=d.published||0;
  S('mcTotal').textContent=tot;S('mcToday').textContent='今日 +'+tod;S('mcToday').className='mc-sub up';
  S('mcPublished').textContent=pub;
  S('mcPending').textContent=pend;
  S('mcPendingSub').textContent=pend>0?'有待发布文章':'全部已处理';
  S('mcPendingSub').className='mc-sub'+(pend>0?' warn':'');
  // Sidebar stats
  S('sidebarTotal').textContent=tot;S('sidebarToday').textContent=tod;S('sidebarPublished').textContent=pub;
  if(pend>0){S('sidebarPending').textContent=pend;S('sidebarPending').style.display='';}
  else{S('sidebarPending').style.display='none';}
  // Table count
  S('tableCount').textContent='共 '+tot+' 条';
  // Publish bar
  if(pend>0){S('publishBar').classList.add('active');S('pendingCount').textContent=pend}
  else S('publishBar').classList.remove('active');
  // Avg score
  const scores=d.rows.filter(n=>n.title_score>0).map(n=>n.title_score+n.content_score);
  const avg=scores.length?scores.reduce((a,b)=>a+b,0)/scores.length:0;
  S('mcScore').textContent=avg>0?avg.toFixed(1):'—';

  // Pagination
  const totalPages=Math.ceil(d.total/pageSize);let pager='';
  if(totalPages>1){
    pager+=`<button class="pg-btn" onclick="goPage(${page-1})" ${page<=0?'disabled':''}>‹</button>`;
    const maxPages=7;const half=Math.floor(maxPages/2);
    let start=Math.max(0,page-half),end=Math.min(totalPages,start+maxPages);
    if(end-start<maxPages)start=Math.max(0,end-maxPages);
    for(let i=start;i<end;i++){
      pager+=`<button class="pg-btn ${i===page?'cur':''}" onclick="goPage(${i})">${i+1}</button>`;
    }
    pager+=`<button class="pg-btn" onclick="goPage(${page+1})" ${page>=totalPages-1?'disabled':''}>›</button>`;
  }
  pager+=`<span class="table-foot-info">显示 ${page*pageSize+1}–${Math.min((page+1)*pageSize,d.total)} / 共 ${d.total} 条</span>`;
  S('pager').innerHTML=pager;
  // Push state to URL so browser back button restores filters
  const up=new URLSearchParams({sort_by:sortBy,sort_dir:sortDir,page:page,
    date_from:S('dateFrom').value,date_to:S('dateTo').value,
    search:S('search').value,fetch_by:S('fetchBy').value,
    status:S('status').value,publish_xhs:S('publishXhs').value,
    fmt:S('fmtFilter').value,score_min:S('scoreFilter').value,preselected:S('preselectedFilter').value,grade:S('gradeFilter').value});
  history.replaceState(null,'','/?'+up.toString());
  // Restore scroll position when returning from detail page
  const sy=sessionStorage.getItem('listScrollY');
  if(sy){const ts=document.querySelector('.table-scroll');requestAnimationFrame(()=>{if(ts)ts.scrollTop=parseInt(sy);sessionStorage.removeItem('listScrollY')})}
}
function goPage(n){page=n;loadList();const ts=document.querySelector('.table-scroll');if(ts)ts.scrollTop=0;}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
function setSort(col){if(sortBy===col){sortDir=sortDir==='DESC'?'ASC':'DESC'}else{sortBy=col;sortDir='DESC'}loadList()}

async function preview(key){
  const r=await fetch('/api/news/'+key);const n=await r.json();
  let imgs='';
  if(n.image_url){const s=n.image_url.startsWith('/')?'/local-image?path='+encodeURIComponent(n.image_url):n.image_url;imgs+=`<img class="preview-img" src="${s}">`}
  if(n.gallery_images){try{
    const g=typeof n.gallery_images==='string'?JSON.parse(n.gallery_images):n.gallery_images;
    g.forEach(p=>{imgs+=`<img class="preview-img" src="/local-image?path=${encodeURIComponent(p)}">`});
  }catch(e){}}
  const isStory=n.is_long_form||(n.format||'')==='story';
  S('modalContent').innerHTML=`
    ${imgs}
    <h2>${esc(n.title)}</h2>
    <div class="meta">${n.pub_time} | ${n.source} | ${n.category} | 📊标题${(n.title_score||0).toFixed(1)} 内容${(n.content_score||0).toFixed(1)}</div>
    ${n.summary?`<div class="section"><h4>${isStory?'导语':'引流摘要'}</h4><p>${esc(n.summary).replace(/\\n/g,'<br>')}</p></div>`:''}
    <div class="section"><h4>${isStory?'正文':'新闻要点'}</h4><p>${esc(n.content||'').replace(/\\n/g,'<br>')}</p></div>
    ${n.comment?`<div class="section"><h4>${isStory?'结语':'我的解读'}</h4><p>${esc(n.comment||'').replace(/\\n/g,'<br>')}</p></div>`:''}
    ${n.video_caption?`<div class="section"><h4>🎬 短配文</h4><p>${esc(n.video_caption||'')}</p></div>`:''}
    <div class="section"><h4>标签</h4>${(n.tags||[]).map(t=>`<span class="tag">${esc(t)}</span>`).join(' ')}</div>
    <div style="margin-top:16px"><a href="/detail/${n.key}" class="btn btn-red">编辑详情</a> <button class="btn btn-gray" onclick="closeModal()">关闭</button></div>`;
  S('modal').classList.add('active');
}
function closeModal(){S('modal').classList.remove('active')}
function closeTaskModal(){S('taskModal').classList.remove('active')}
async function stopTask(){
  if(!activeTaskId)return;
  if(!await showConfirm('确定终止当前任务？','','终止','btn-red','🛑'))return;
  await fetch('/api/task/'+activeTaskId+'/stop',{method:'POST'});
}

// Keyword management
let keywords=[],presetCount=0;
async function loadKeywords(){
  try{
    var r1=await fetch('/api/keywords');var d1=await r1.json();
    keywords=d1.keywords;presetCount=keywords.length;
    var r2=await fetch('/api/custom-keywords');var d2=await r2.json();
    d2.keywords.forEach(function(k){k.custom=true;keywords.push(k)});
  }catch(e){keywords=[];presetCount=0}
  renderKeywords();
}
function renderKeywordChip(k,i,canDelete){
  return `<div class="kw-chip" style="display:flex;align-items:center;gap:4px;background:#fff;border:1px solid var(--border);border-radius:6px;padding:4px 8px;font-size:12px">
    <input type="checkbox" checked onchange="updateKwSummary()" style="width:14px;height:14px;accent-color:var(--red)">
    <input value="${esc(k.keyword)}" oninput="keywords[${i}].keyword=this.value;saveCustomKwDebounced()" style="border:none;background:transparent;width:${Math.max(40,k.keyword.length*14)}px;font-size:12px;font-weight:500;outline:none;padding:2px">
    <span style="color:var(--text3)">×</span>
    <input type="number" value="${k.max}" min="1" max="50" oninput="keywords[${i}].max=parseInt(this.value)||5;updateKwSummary();saveCustomKwDebounced()" style="width:38px;padding:2px;border:1px solid #eee;border-radius:4px;font-size:11px;text-align:center">
    ${canDelete?`<span style="cursor:pointer;color:var(--text3);font-size:14px" onclick="deleteKeyword(${i})" title="删除">×</span>`:''}
  </div>`;
}
function renderKeywords(){
  var grid=document.getElementById('kwGrid'),html='';
  for(var i=0;i<keywords.length;i++){
    if(i===0)html+='<span style="font-size:10px;color:var(--text3);width:100%">预置</span>';
    if(i===presetCount && i<keywords.length)html+='<span style="font-size:10px;color:var(--text3);width:100%;margin-top:4px">自定义</span>';
    html+=renderKeywordChip(keywords[i],i,i>=presetCount);
  }
  grid.innerHTML=html;
  updateKwSummary();
}
function selectAllKw(val){document.querySelectorAll('#kwGrid .kw-chip input[type=checkbox]').forEach(function(cb){cb.checked=val});updateKwSummary()}
async function addKeyword(){keywords.push({keyword:'新词',max:5,custom:true});renderKeywords();await saveCustomKw()}
async function deleteKeyword(i){keywords.splice(i,1);renderKeywords();await saveCustomKw()}
var saveCustomKwTimer=null;
function saveCustomKwDebounced(){clearTimeout(saveCustomKwTimer);saveCustomKwTimer=setTimeout(saveCustomKw,500)}
async function saveCustomKw(){
  var custom=keywords.slice(presetCount).map(function(k){return{keyword:k.keyword,max:k.max}});
  await fetch('/api/custom-keywords',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keywords:custom})});
}
function resetKeywords(){loadKeywords()}
function updateConfigMap(){}
async function saveConfig(){
  var rows=S('configRows').children, map={};
  for(var i=0;i<rows.length;i++){
    var ins=rows[i].querySelectorAll('input');
    var topic=rows[i].querySelector('span').textContent;
    map[topic]={keyword:ins[0].value,max:parseInt(ins[1].value)||5};
  }
  var body={
    yahoo_keyword_map:map,
    publish_threshold:parseFloat(S('cfgPublishTh').value)||3,
    retry_threshold:parseFloat(S('cfgRetryTh').value)||2,
    daily_quota:parseInt(S('cfgDailyQuota').value)||5
  };
  var r=await fetch('/api/agent-config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  if(r.ok){alert('配置已保存');loadKeywords()}else{alert('保存失败')}
}
function _renderTagChips(container, tags){
  container.innerHTML='';
  (tags||[]).forEach(function(t){
    if(!t)return;
    var chip=document.createElement('span');
    chip.className='tag-chip';
    chip.style.cssText='display:inline-flex;align-items:center;gap:2px;background:#e8ecf1;color:#333;border-radius:10px;padding:1px 6px;font-size:10px;margin:1px 2px';
    chip.innerHTML=esc(t)+'<button onclick="this.parentElement.remove();_syncChipInput(this)" style="background:none;border:none;cursor:pointer;font-size:10px;color:#999;padding:0 2px;line-height:1">×</button>';
    container.appendChild(chip);
  });
  var inp=document.createElement('input');
  inp.placeholder='+ 添加';
  inp.style.cssText='width:60px;border:none;outline:none;font-size:10px;padding:1px 4px;background:transparent';
  inp.onkeydown=function(e){
    if(e.key==='Enter'){
      e.preventDefault();
      var v=inp.value.trim();
      if(v){
        var hidden=container.parentElement.querySelector('.chip-hidden');
        var arr=(hidden.value||'').split(/\s+/).filter(Boolean);
        arr.push(v);
        hidden.value=arr.join(' ');
        _renderTagChips(container, arr);
        setTimeout(function(){
          var next=container.querySelector('input');
          if(next)next.focus();
        },50);
      }
    }
  };
  container.appendChild(inp);
}
function _syncChipInput(btn){
  var container=btn.closest('.chip-area');
  if(!container)return;
  var hidden=container.parentElement.querySelector('.chip-hidden');
  var tags=[];
  container.querySelectorAll('.tag-chip').forEach(function(c){tags.push(c.textContent.replace('×','').trim())});
  hidden.value=tags.join(' ');
}
function _chipInput(name, tags){
  return '<input type="hidden" class="chip-hidden" id="'+name+'" value="'+esc((tags||[]).join(' '))+'"><span class="chip-area"></span>';
}

function addKwRow(){
  var container=S('cfgKwRows');
  var row=document.createElement('div');
  row.style.cssText='display:flex;align-items:flex-start;gap:6px';
  var id='kwtags_new_'+Date.now();
  row.innerHTML='<input class="cfgKwKey" value="" style="width:90px;padding:2px 4px;border:1px solid #ddd;border-radius:3px;font-size:11px"><span style="color:var(--text3);padding-top:4px">→</span><div>'+_chipInput(id,[])+'</div><button class="btn btn-gray btn-sm" onclick="this.parentElement.remove()" style="font-size:10px;padding:1px 4px;flex-shrink:0">✕</button>';
  container.appendChild(row);
  var area=row.querySelector('.chip-area');
  _renderTagChips(area, []);
}
async function saveTagConfig(){
  var tc={};
  tc.must_tags=getChipTags('cfgMustTags');
  tc.random_tag_pools={};
  var ft=getChipTags('cfgFashionTags'),bt=getChipTags('cfgBeautyTags');
  if(ft.length)tc.random_tag_pools.fashion=ft;
  if(bt.length)tc.random_tag_pools.beauty=bt;
  tc.keyword_tag_map={};
  document.querySelectorAll('#cfgKwRows > div').forEach(function(row){
    var key=row.querySelector('.cfgKwKey');
    var kw=(key.value||'').trim();
    var hidden=row.querySelector('.chip-hidden');
    var tags=(hidden.value||'').split(/\s+/).filter(Boolean);
    if(kw&&tags.length)tc.keyword_tag_map[kw]=tags;
  });
  var r=await fetch('/api/agent-config',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({tag_config:tc})});
  if(r.ok){alert('标签配置已保存')}else{alert('保存失败')}
}
function getChipTags(id){
  var el=S(id);
  return el? (el.value||'').split(/\s+/).filter(Boolean) : [];
}
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
  runTask({title:'📤 发布到小红书',apiUrl:'/api/trigger-publish',apiBody:{},btn:b,origText:orig,taskLabel:'发布',onDone:null});
}
async function togglePublish(key,val,el){
  if(el)el.classList.toggle('on',!!val);
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_xhs:val?1:0})});
  loadList();
}
async function togglePreselect(key,el){
  const cur=el.textContent==='★';
  el.textContent=cur?'☆':'★';
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({preselected:cur?0:1})});
}
async function setPostTime(key,val){
  const fmt=val?val.replace('T',' '):null;
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({xhs_pub_time:fmt})});
}
function clearPostTime(){
  document.querySelectorAll('.rowSel:checked').forEach(cb=>{
    const tr=cb.closest('tr');if(!tr)return;
    const inp=tr.querySelector('input[type=datetime-local]');
    if(inp&&!inp.disabled){inp.value='';setPostTime(cb.value,'');}
  });
}
function selectAllRows(val){document.querySelectorAll('.rowSel').forEach(cb=>{cb.checked=val});updateArchiveBar()}
function updateArchiveBar(){
  const n=document.querySelectorAll('.rowSel:checked').length;
  const bar=document.getElementById('archiveBar');
  if(n>0){bar.classList.add('active');document.getElementById('archiveCount').textContent='已选 '+n+' 条'}
  else bar.classList.remove('active');
}
async function archiveSelected(){
  var keys=[];document.querySelectorAll('.rowSel:checked').forEach(cb=>{keys.push(cb.value)});
  if(!keys.length){alert('请先勾选新闻');return}
  if(!await showConfirm('确定归档 '+keys.length+' 条文章？','归档后不可撤销','归档','btn-red','📦'))return;
  await fetch('/api/archive-bulk',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({keys:keys})});
  document.getElementById('archiveBar').classList.remove('active');
  loadList();
}
async function collectBatchMetrics(){
  var btn=document.querySelector('#archiveBar .btn-orange');
  btn.disabled=true;btn.textContent='⏳ 回收中...';
  try{
    var r=await fetch('/api/collect-metrics',{method:'POST'});
    var d=await r.json();
    if(d.ok){alert('回收完成: '+d.collected+' 篇');loadList()}
    else{alert('回收失败: '+(d.error||'未知'))}
  }catch(e){alert('请求失败: '+e.message)}
  btn.disabled=false;btn.textContent='🔄 回收数据';
}
async function loadCategories(){
  const fbs=[...new Set((await(await fetch('/api/news?limit=500&status=active')).json()).rows.map(r=>r.fetch_by).filter(Boolean))];
  S('fetchBy').innerHTML='<option value="">全部来源</option>'+fbs.sort().map(f=>`<option>${esc(f)}</option>`).join('');
}
	// Read state from URL params (set by loadList via history.replaceState)
	const d=new Date();
	const today=d.getFullYear()+"-"+String(d.getMonth()+1).padStart(2,"0")+"-"+String(d.getDate()).padStart(2,"0");
	const qp=new URLSearchParams(location.search);
	sortBy=qp.get("sort_by")||"created_at";
	sortDir=qp.get("sort_dir")||"DESC";
	page=parseInt(qp.get("page"))||0;
	S("search").value=qp.get("search")||"";
	S("fetchBy").value=qp.get("category")||qp.get("fetch_by")||"";
	S("status").value=qp.get("status")||"active";
	S("publishXhs").value=qp.get("publish_xhs")||"";
	S("fmtFilter").value=qp.get("fmt")||"";
	S("scoreFilter").value=qp.get("score_min")||"";
		S("preselectedFilter").value=qp.get("preselected")||"";
	S("gradeFilter").value=qp.get("grade")||"";
	S("dateFrom").value=qp.has("date_from")?qp.get("date_from"):today;
	S("dateTo").value=qp.has("date_to")?qp.get("date_to"):today;
	if(S("pageSizeSelect"))S("pageSizeSelect").value=pageSize;
	loadList();loadCategories();checkActiveTasks();
window.addEventListener('pageshow',e=>{if(e.persisted)loadList()});
// Save scroll position only when navigating to detail page
document.addEventListener('click',e=>{const a=e.target.closest('a[href^="/detail/"]');if(a){const ts=document.querySelector('.table-scroll');sessionStorage.setItem('listScrollY',ts?ts.scrollTop:0)}},true);
// Quick time buttons for publish schedule — set on checked rows
function setQuickTime(h,dayOffset){
  const d=new Date();d.setDate(d.getDate()+dayOffset);
  const ds=`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
  const dtVal=`${ds}T${String(h).padStart(2,'0')}:00`;
  document.querySelectorAll('.rowSel:checked').forEach(cb=>{
    const tr=cb.closest('tr');if(!tr)return;
    const inp=tr.querySelector('input[type=datetime-local]');
    if(!inp||inp.disabled)return;
    inp.value=dtVal;
    setPostTime(cb.value,dtVal);
  });
}
function updateQuickTimeBtns(){
  const now=new Date();const today=now.getDate();const h=now.getHours();
  ['T8','T12','T18'].forEach(id=>{
    const btn=document.getElementById('qt'+id);if(!btn)return;
    btn.disabled=today===new Date().getDate() && h>=parseInt(id.slice(1));
  });
  ['T8','T12','T18','M8','M12','M18'].forEach(id=>{
    const btn=document.getElementById('qt'+id);if(btn)btn.style.opacity=btn.disabled?'0.4':'1';
  });
}
updateQuickTimeBtns();setInterval(updateQuickTimeBtns,60000);

// ── WeChat view (公众号) ─────────────────────────────────────────────
var _wdAllItems=[];
function switchToWechatView(){
  var nv=document.getElementById('newsView'),wv=document.getElementById('wechatView'),n=document.getElementById('wechatNavItem');
  // close config/tag modals first
  document.querySelectorAll('.nav-item.active').forEach(function(e){e.classList.remove('active');});
  var qv=document.getElementById('queueView'); if(qv) qv.style.display='none';
  if(nv) nv.style.display='none';
  if(wv){wv.style.display='flex';wv.style.flexDirection='column';}
  if(n) n.classList.add('active');
  _buildWechatView();
  loadWechatList();
}
function switchToNewsView(){
  var nv=document.getElementById('newsView'),wv=document.getElementById('wechatView');
  var wn=document.getElementById('wechatNavItem'),nn=document.getElementById('newsNavItem');
  var qv=document.getElementById('queueView'); if(qv) qv.style.display='none';
  if(wv) wv.style.display='none';
  if(nv){ nv.style.display='flex'; nv.style.flexDirection='column'; }
  if(wn) wn.classList.remove('active');
  if(nn) nn.classList.add('active');
}
// ── 运营队列视图（待人工 / 失败稿 / 假名词表 / 拆篇预览）──────────────────
var _qKind='manual';
function _qEsc(x){return (x==null?'':(''+x)).replace(/[&<>"]/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];});}
function qToast(msg){
  var t=document.getElementById('qToast');
  if(!t){t=document.createElement('div');t.id='qToast';document.body.appendChild(t);
    t.style.cssText='position:fixed;left:50%;bottom:34px;transform:translateX(-50%);background:#1c1c2e;color:#fff;padding:9px 16px;border-radius:9px;font-size:12.5px;z-index:99999;opacity:0;transition:opacity .18s;box-shadow:0 6px 20px rgba(0,0,0,.3)';}
  t.textContent=msg;t.style.opacity='1';clearTimeout(window._qTT);window._qTT=setTimeout(function(){t.style.opacity='0';},2200);
}
function switchToQueueView(kind){
  _qKind=kind;
  document.querySelectorAll('.nav-item.active').forEach(function(e){e.classList.remove('active');});
  var nv=document.getElementById('newsView'),wv=document.getElementById('wechatView'),qv=document.getElementById('queueView');
  if(nv) nv.style.display='none';
  if(wv) wv.style.display='none';
  if(qv) qv.style.display='flex';
  var ids={manual:'manualNavItem',fail:'failNavItem',split:'splitNavItem',topic:'topicNavItem',feedback:'feedbackNavItem',lexicon:'lexiconNavItem',token:'tokenNavItem'};
  var el=document.getElementById(ids[kind]); if(el) el.classList.add('active');
  renderQueue();
}
function _qHead(title,sub){
  return '<div style="display:flex;align-items:baseline;gap:10px;padding:14px 2px 8px"><h2 style="margin:0;font-size:16px">'+title+'</h2><span style="color:var(--text3);font-size:12px">'+sub+'</span><span style="flex:1"></span><button class="btn btn-outline btn-sm" onclick="renderQueue()">🔄 刷新</button></div>';
}
function _qWait(){return '<div style="color:var(--text3);padding:24px;font-size:13px">加载中…</div>';}
function _qEmpty(m){return '<div style="color:var(--text3);padding:34px;text-align:center;font-size:13px">'+m+'</div>';}
function _qCard(inner){return '<div class="table-card" style="margin-bottom:12px;overflow:auto">'+inner+'</div>';}
function _qTh(cols){return '<thead><tr>'+cols.map(function(c){return '<th'+(c.w?' style="width:'+c.w+'"':'')+'>'+c.t+'</th>';}).join('')+'</tr></thead>';}
function _qSetBadge(id,n){var b=document.getElementById(id);if(b){b.textContent=n;b.style.display=n>0?'':'none';}}
function renderQueue(){
  var qb=document.getElementById('queueBody'); if(!qb) return;
  if(_qKind==='manual'){ qb.innerHTML=_qHead('🚩 待人工','成人产业/非赛道等内容门禁命中，需人工判断是否写、怎么写')+_qCard('<div id="qTable">'+_qWait()+'</div>'); _qLoadManual(); }
  else if(_qKind==='fail'){ qb.innerHTML=_qHead('❌ 失败稿','写稿失败/异常；含「门禁/评分豁免」warn 留痕')+_qCard('<div id="qTable">'+_qWait()+'</div>'); _qLoadFail(); }
  else if(_qKind==='lexicon'){ _qLoadLexicon(); }
  else if(_qKind==='token'){ _qLoadToken(); }
  else if(_qKind==='split'){
    qb.innerHTML=_qHead('🧩 拆篇预览','仅列出 cluster 体量超阈值的候选；拆篇结果单独立表（1 主稿对多篇）')
      +'<div style="display:flex;gap:10px;align-items:center;padding:0 2px 8px;font-size:12px;color:var(--text2)">'
      +'阈值 <input id="qSplitTh" type="number" value="3000" style="width:84px;padding:4px 6px;border:1px solid var(--border);border-radius:6px">'
      +'日期 <input id="qSplitDay" type="date" style="padding:3px 6px;border:1px solid var(--border);border-radius:6px">'
      +'<button class="btn btn-outline btn-sm" onclick="_qLoadSplit(1)">查询</button></div>'
      +_qCard('<div id="qTable">'+_qWait()+'</div>');
    _qLoadSplit(1);
  }
  else if(_qKind==='topic'){ _qLoadTopic(); }
  else if(_qKind==='feedback'){ _qLoadFeedback(); }
}
function _qLoadManual(){
  fetch('/api/manual-review').then(function(r){return r.json();}).then(function(d){
    var rows=d.rows||[]; _qSetBadge('navBadgeManual',rows.length);
    var tb=document.getElementById('qTable'); if(!tb) return;
    if(!rows.length){tb.innerHTML=_qEmpty('暂无待人工素材 🎉');return;}
    tb.innerHTML='<table>'+_qTh([{t:'标题'},{t:'时间',w:'130px'},{t:'命中原因'},{t:'操作',w:'70px'}])+'<tbody>'+
      rows.map(function(x){return '<tr><td><a href="/detail/'+x.key+'" target="_blank" title="'+_qEsc(x.key)+'">'+_qEsc(x.title||x.key)+'</a></td>'+
        '<td style="font-size:11px;color:#666">'+_qEsc(x.created_at||'')+'</td>'+
        '<td style="color:#b45309;font-size:12px">'+_qEsc(x.manual_reason||'')+'</td>'+
        '<td><button class="btn btn-outline btn-sm" onclick="_qManualClear(\''+x.key+'\')">已处理</button></td></tr>';}).join('')+'</tbody></table>';
  });
}
function _qManualClear(k){fetch('/api/manual-review/'+k+'/clear',{method:'POST'}).then(function(){qToast('已标记处理');_qLoadManual();});}
function _qLoadFail(){
  fetch('/api/write-failures').then(function(r){return r.json();}).then(function(d){
    var rows=d.rows||[]; var need=rows.filter(function(x){return x.status==='needs_manual';}).length;
    _qSetBadge('navBadgeFail',need);
    var tb=document.getElementById('qTable'); if(!tb) return;
    if(!rows.length){tb.innerHTML=_qEmpty('队列为空 🎉');return;}
    tb.innerHTML='<table>'+_qTh([{t:'标题',w:'180px'},{t:'时间',w:'128px'},{t:'级别',w:'56px'},{t:'原因'},{t:'状态',w:'72px'},{t:'下次重试',w:'120px'},{t:'操作',w:'132px'}])+'<tbody>'+
      rows.map(function(x){
        var lv=x.level||'error';
        var lvb=lv==='warn'?'<span class="badge" style="background:#fff7e6;color:#b45309">warn</span>':'<span class="badge" style="background:#fee2e2;color:#b91c1c">error</span>';
        var st=x.status==='needs_manual'?'待人工':(x.status==='resolved'?'已了结':'待重试');
        var act=(x.status==='resolved')?'<span style="color:#15803d;font-size:11px">✓</span>':'<button class="btn btn-outline btn-sm" onclick="_qFailRetry(\''+x.key+'\')">立即重试</button> <button class="btn btn-outline btn-sm" onclick="_qFailDismiss(\''+x.key+'\')">忽略</button>';
        return '<tr><td><a href="/detail/'+x.key+'" target="_blank" title="'+_qEsc(x.key)+'">'+_qEsc(x.title||x.key)+'</a></td>'+
          '<td style="font-size:11px;color:#666">'+_qEsc(x.created_at||x.updated_at||'')+'</td><td>'+lvb+'</td>'+
          '<td style="font-size:12px">'+_qEsc(x.reason||'')+'</td>'+
          '<td style="font-size:12px">'+st+(x.note?'<div style="color:#999;font-size:10px">'+_qEsc(x.note)+'</div>':'')+'</td>'+
          '<td style="font-size:11px;color:#666">'+_qEsc(x.next_retry_at||'—')+'</td><td>'+act+'</td></tr>';}).join('')+'</tbody></table>';
  });
}
function _qFailRetry(k){fetch('/api/write-failures/'+k+'/retry',{method:'POST'}).then(function(){qToast('已置为立即重试');_qLoadFail();});}
function _qFailDismiss(k){showConfirm('从失败队列移除该记录？','仅在确认不再处理时使用','移除','btn-red','🗑️').then(function(ok){if(!ok)return;fetch('/api/write-failures/'+k+'/dismiss',{method:'POST'}).then(function(){qToast('已移除');_qLoadFail();});});}
var _qSplitRows=[], _qSplitMeta={page:1,page_size:50,total:0};
function _qLoadSplit(page){
  if(page) _qSplitMeta.page=page;
  var day=(document.getElementById('qSplitDay')||{}).value||'';
  var th=(document.getElementById('qSplitTh')||{}).value||'3000';
  var tb=document.getElementById('qTable'); if(tb) tb.innerHTML=_qWait();
  var p=new URLSearchParams({day:day,threshold:th,page:_qSplitMeta.page,page_size:_qSplitMeta.page_size});
  fetch('/api/split-preview?'+p.toString()).then(function(r){return r.json();}).then(function(d){
    _qSplitRows=d.rows||[]; _qSplitMeta.total=d.total||0; _qSplitMeta.page=d.page||1; _qSplitMeta.page_size=d.page_size||50;
    var tb=document.getElementById('qTable'); if(!tb) return;
    if(!_qSplitRows.length){tb.innerHTML=_qEmpty('当前无 cluster 体量超阈值（'+d.threshold+' 字）的素材');return;}
    tb.innerHTML='<table>'+_qTh([{t:'标题'},{t:'体裁',w:'64px'},{t:'cluster体量',w:'100px'},{t:'已拆',w:'90px'},{t:'操作',w:'120px'}])+'<tbody>'+
      _qSplitRows.map(function(x,i){
        var cnt=x.split_count||0;
        var cntCell=cnt?('<a href="javascript:;" onclick="_qSplitView('+i+')" style="color:#15803d">已拆 '+cnt+' 篇</a>'):'<span style="color:#bbb">未拆</span>';
        return '<tr><td><a href="/detail/'+x.full_key+'" target="_blank" title="'+_qEsc(x.key)+'">'+_qEsc(x.title||x.key)+'</a></td>'+
          '<td style="font-size:12px">'+_qEsc(x.format||'')+'</td><td>'+x.len+' 字</td><td style="font-size:12px">'+cntCell+'</td>'+
          '<td><button class="btn btn-red btn-sm" onclick="_qSplitWrite(\''+x.full_key+'\')">'+(cnt?'重新拆':'拆篇写入')+'</button></td></tr>';
      }).join('')+'</tbody></table>';
    _qSplitPager(d);
  });
}
function _qSplitPager(d){
  var lp=Math.max(1,Math.ceil((d.total||0)/(d.page_size||50)));
  var pg=d.page||1;
  var host=document.getElementById('qTable'); if(!host) return;
  var div=document.createElement('div');
  div.className='table-foot';
  div.innerHTML='<span class="table-foot-info">第 '+pg+' / '+lp+' 页 · 共 '+(d.total||0)+' 个超阈值候选</span>'+
    '<button class="pg-btn" onclick="_qLoadSplit(1)"'+(pg<=1?' disabled style="opacity:.4"':'')+'>«</button>'+
    '<button class="pg-btn" onclick="_qLoadSplit('+(pg-1)+')"'+(pg<=1?' disabled style="opacity:.4"':'')+'>‹</button>'+
    '<button class="pg-btn" onclick="_qLoadSplit('+(pg+1)+')"'+(pg>=lp?' disabled style="opacity:.4"':'')+'>›</button>'+
    '<button class="pg-btn" onclick="_qLoadSplit('+lp+')"'+(pg>=lp?' disabled style="opacity:.4"':'')+'>»</button>';
  host.appendChild(div);
}
function _qSplitView(i){
  var x=(_qSplitRows||[])[i]; if(!x) return;
  fetch('/api/split-articles/'+encodeURIComponent(x.full_key)).then(function(r){return r.json();}).then(function(d){
    var rows=d.rows||[];
    var body='<div style="font-size:12px;color:var(--text2);margin-bottom:8px">主稿 '+x.key+'《'+_qEsc((x.title||'').slice(0,40))+'》 → 共 '+rows.length+' 篇：</div>';
    body+=rows.map(function(a){
      return '<div style="border:1px solid var(--border);border-radius:9px;padding:10px 12px;margin-bottom:8px">'
        +'<div style="display:flex;align-items:center;gap:8px;margin-bottom:4px"><span class="badge" style="background:#eef2ff;color:#4f46e5">第'+a.seq+'篇</span>'
        +'<b style="font-size:13px">'+_qEsc(a.title||'(无标题)')+'</b>'
        +'<span style="color:var(--text3);font-size:11px">'+_qEsc(a.channel||'')+(a.score!=null?' · 评分 '+a.score:'')+'</span></div>'
        +'<div style="font-size:11px;color:var(--text3);margin-bottom:4px">组内素材：'+_qEsc((a.group_keys||'').split(',').filter(Boolean).map(function(k){return k.slice(0,8);}).join(', '))+'</div>'
        +'<div style="white-space:pre-wrap;font-size:12px;max-height:240px;overflow:auto;background:#fafafe;border-radius:7px;padding:8px">'+_qEsc((a.content||'').slice(0,2000))+'</div></div>';
    }).join('');
    _qEditOpen('🧩 拆篇 · '+x.key, body, null);
  });
}
function _qSplitWrite(k){showConfirm('对该主稿触发「拆篇写入」？','按 cluster 体量拆多篇，写入 split_articles 表（1 主稿对多篇），不覆盖主稿改写文','确认拆篇','btn-red','🧩').then(function(ok){if(!ok)return;fetch('/api/split-write/'+k,{method:'POST'}).then(function(r){return r.json();}).then(function(d){qToast(d.ok?('已触发拆篇写入（后台运行，稍后刷新）'):('触发失败: '+(d.msg||'')));});});}


// ── 话题缓存（原生应用内视图）──────────────────────────
var _tc={page:1,size:50,sort_by:'name',sort_dir:'asc',q:'',total:0,rows:[]};
function _qIn(id,ph,w,val){
  var ws=(w>0)?('width:'+w+'px'):'width:100%';
  return '<input id="'+id+'" placeholder="'+_qEsc(ph)+'" value="'+_qEsc(val==null?'':val)+'" style="'+ws+';font-size:12px;padding:5px 9px;border:1px solid var(--border);border-radius:7px;outline:none;background:#fff;box-sizing:border-box">';
}
function _qLoadTopic(){
  var qb=document.getElementById('queueBody'); if(!qb) return;
  qb.innerHTML=_qHead('话题缓存','话题名 → 小红书话题 id 映射；发布时命中即用，免敲字')
    +'<div class="table-card" style="flex:none;padding:12px 14px;margin-bottom:12px"><div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">'
    +_qIn('tc_name','话题名（不含 #）',170,'')+_qIn('tc_id','topic_id（24位）',220,'')+_qIn('tc_link','link（可空）',250,'')
    +'<button class="btn btn-red btn-sm" onclick="_tcSave()">新增 / 更新</button><span id="tc_msg" style="font-size:12px"></span></div></div>'
    +'<div class="table-card" style="flex:none"><div class="table-hdr">'
    +_qIn('tc_q','搜索话题名 / topic_id…',240,'')
    +'<button class="btn btn-outline btn-sm" onclick="_tcSearch()">搜索</button>'
    +'<button class="btn btn-outline btn-sm" onclick="_tcClear()">清空</button>'
    +'<span class="table-hdr-count" id="tc_count"></span></div>'
    +'<div id="tc_table" style="overflow:auto">'+_qWait()+'</div>'
    +'<div class="table-foot" id="tc_pager"></div></div>';
  var qi=document.getElementById('tc_q'); if(qi){qi.value=_tc.q;qi.addEventListener('keydown',function(e){if(e.key==='Enter')_tcSearch();});}
  _tcRender();
}
function _tcRender(){
  var p=new URLSearchParams({page:_tc.page,page_size:_tc.size,sort_by:_tc.sort_by,sort_dir:_tc.sort_dir,q:_tc.q});
  fetch('/api/topic-cache?'+p.toString()).then(function(r){return r.json();}).then(function(d){
    _tc.total=d.total||0; _tc.rows=d.rows||[];
    var lp=Math.max(1,Math.ceil(_tc.total/_tc.size));
    if(_tc.page>lp){_tc.page=lp;return _tcRender();}
    var tb=document.getElementById('tc_table'); if(!tb) return;
    var sc=function(c){return _tc.sort_by===c?(_tc.sort_dir==='asc'?' ▲':' ▼'):'';};
    tb.innerHTML=(_tc.rows.length?'<table><thead><tr>'
      +'<th onclick="_tcSort(\'name\')">话题名'+sc('name')+'</th><th>topic_id</th>'
      +'<th onclick="_tcSort(\'source\')">来源'+sc('source')+'</th>'
      +'<th style="width:70px" onclick="_tcSort(\'hit_count\')">命中'+sc('hit_count')+'</th>'
      +'<th style="width:130px" onclick="_tcSort(\'updated_at\')">更新时间'+sc('updated_at')+'</th>'
      +'<th style="width:130px"></th></tr></thead><tbody>'
      +_tc.rows.map(function(x,i){return '<tr>'
        +'<td><b>'+_qEsc(x.name)+'</b></td>'
        +'<td><code style="font-size:11px;color:var(--text2)">'+_qEsc(x.topic_id)+'</code></td>'
        +'<td>'+_qEsc(x.source||'-')+'</td><td>'+_qEsc(x.hit_count||0)+'</td>'
        +'<td style="font-size:11px;color:var(--text3)">'+_qEsc((x.updated_at||'').slice(0,16))+'</td>'
        +'<td><button class="btn btn-outline btn-sm" onclick="_tcFill('+i+')">编辑</button> '
        +'<button class="btn btn-outline btn-sm" onclick="_tcDel('+i+')">删除</button></td></tr>';}).join('')
      +'</tbody></table>':_qEmpty('没有数据'));
    var cnt=document.getElementById('tc_count'); if(cnt)cnt.textContent=_tc.total+' 条';
    var pg=document.getElementById('tc_pager');
    if(pg)pg.innerHTML='<span class="table-foot-info">第 '+_tc.page+' / '+lp+' 页 · 共 '+_tc.total+' 条</span>'
      +'<button class="pg-btn" onclick="_tcGo(1)"'+(_tc.page<=1?' disabled style="opacity:.4"':'')+'>«</button>'
      +'<button class="pg-btn" onclick="_tcGo('+(_tc.page-1)+')"'+(_tc.page<=1?' disabled style="opacity:.4"':'')+'>‹</button>'
      +'<button class="pg-btn" onclick="_tcGo('+(_tc.page+1)+')"'+(_tc.page>=lp?' disabled style="opacity:.4"':'')+'>›</button>'
      +'<button class="pg-btn" onclick="_tcGo('+lp+')"'+(_tc.page>=lp?' disabled style="opacity:.4"':'')+'>»</button>'
      +'<select onchange="_tcSize(this.value)" style="font-size:12px;padding:4px 6px;border:1px solid var(--border);border-radius:6px">'
      +[50,100,200,500].map(function(n){return '<option value="'+n+'"'+(_tc.size===n?' selected':'')+'>'+n+' / 页</option>';}).join('')+'</select>';
  });
}
function _tcGo(p){var lp=Math.max(1,Math.ceil(_tc.total/_tc.size));_tc.page=Math.min(Math.max(1,p),lp);_tcRender();}
function _tcSize(s){_tc.size=parseInt(s,10);_tc.page=1;_tcRender();}
function _tcSort(c){if(_tc.sort_by===c)_tc.sort_dir=(_tc.sort_dir==='asc'?'desc':'asc');else{_tc.sort_by=c;_tc.sort_dir='asc';}_tc.page=1;_tcRender();}
function _tcSearch(){var e=document.getElementById('tc_q');_tc.q=e?e.value.trim():'';_tc.page=1;_tcRender();}
function _tcClear(){_tc.q='';var e=document.getElementById('tc_q');if(e)e.value='';_tc.page=1;_tcRender();}
function _tcFill(i){var x=_tc.rows[i];if(!x)return;var a=document.getElementById('tc_name'),b=document.getElementById('tc_id'),c=document.getElementById('tc_link');if(a)a.value=x.name||'';if(b)b.value=x.topic_id||'';if(c)c.value=x.link||'';qToast('已填入表单，可改后「新增 / 更新」');}
function _tcMsg(t,ok){var m=document.getElementById('tc_msg');if(!m)return;m.textContent=t;m.style.color=ok?'#15803d':'#b91c1c';clearTimeout(window._tcMT);window._tcMT=setTimeout(function(){if(m)m.textContent='';},3000);}
function _tcSave(){
  var name=((document.getElementById('tc_name')||{}).value||'').trim().replace(/^#/,'');
  var id=((document.getElementById('tc_id')||{}).value||'').trim();
  var link=((document.getElementById('tc_link')||{}).value||'').trim();
  if(!name||!id){_tcMsg('话题名和 topic_id 必填',false);return;}
  fetch('/api/topic-cache',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:name,topic_id:id,link:link})})
    .then(function(r){return r.json();}).then(function(d){
      if(d.ok){_tcMsg('已保存',true);var b=document.getElementById('tc_id'),c=document.getElementById('tc_link');if(b)b.value='';if(c)c.value='';_tcRender();}
      else _tcMsg(d.msg||'保存失败',false);
    });
}
function _tcDel(i){var x=_tc.rows[i];if(!x)return;showConfirm('删除话题「'+x.name+'」？','该操作不可撤销','删除','btn-red','🗑️').then(function(ok){if(!ok)return;fetch('/api/topic-cache/'+encodeURIComponent(x.name),{method:'DELETE'}).then(function(r){return r.json();}).then(function(d){if(d.ok){qToast('已删除');_tcRender();}else qToast('删除失败');});});}

// ── 发布规律（原生应用内视图）──────────────────────────
var _fb={rows:[],byNo:{}};
var _FB_FACETS=['category','genre','title_style','publish_mode','direction','action','confidence','entities'];
function _qLoadFeedback(){
  var qb=document.getElementById('queueBody'); if(!qb) return;
  qb.innerHTML=_qHead('发布规律','feedback_patterns 人工 review（facets 结构化，可编辑）')
    +'<div class="table-card" style="flex:none"><div class="table-hdr">'
    +_qIn('fb_q','搜索 标题/正文/题材/实体/对策…',300,'')
    +'<select id="fb_cat" style="font-size:12px;padding:6px 10px;border:1px solid var(--border);border-radius:7px;background:#fff"><option value="">全部类目</option></select>'
    +'<button class="btn btn-outline btn-sm" onclick="_fbLoad()">查询</button>'
    +'<span class="table-hdr-count" id="fb_cnt"></span></div>'
    +'<div id="fb_table" style="overflow:auto">'+_qWait()+'</div></div>';
  var e=document.getElementById('fb_q'); if(e)e.addEventListener('keydown',function(ev){if(ev.key==='Enter')_fbLoad();});
  _fbLoad();
}
function _fbLoad(){
  var q=(document.getElementById('fb_q')||{}).value||'';
  var cat=(document.getElementById('fb_cat')||{}).value||'';
  fetch('/api/feedback-patterns?q='+encodeURIComponent(q)+'&category='+encodeURIComponent(cat))
    .then(function(r){return r.json();}).then(function(d){
      var rows=d.rows||[]; _fb.rows=rows; _fb.byNo={}; rows.forEach(function(x){_fb.byNo[x.no]=x;});
      var sel=document.getElementById('fb_cat'); var v=sel?sel.value:cat;
      var cats=[]; rows.forEach(function(x){if(x.category&&cats.indexOf(x.category)<0)cats.push(x.category);});cats.sort();
      if(sel)sel.innerHTML='<option value="">全部类目</option>'+cats.map(function(c){return '<option'+(c===v?' selected':'')+'>'+_qEsc(c)+'</option>';}).join('');
      var cnt=document.getElementById('fb_cnt');if(cnt)cnt.textContent='共 '+(d.total!=null?d.total:rows.length)+' 条';
      var tb=document.getElementById('fb_table');if(!tb)return;
      if(!rows.length){tb.innerHTML=_qEmpty('没有数据');return;}
      tb.innerHTML='<table><thead><tr><th style="width:46px">#</th><th>类目</th><th>题材</th><th>标题类型</th><th>方向</th><th>对策</th><th>置信</th><th>实体</th><th>标题 / 正文</th><th style="width:70px"></th></tr></thead><tbody>'
        +rows.map(function(x){
          var dc=(x.direction==='零曝光')?'color:#b91c1c':((x.direction==='爆发')?'color:#15803d':'');
          var ac=(x.action==='跳过'||x.action==='降级')?'color:#b91c1c':((x.action==='优先')?'color:#15803d':'');
          return '<tr><td>'+x.no+'</td><td>'+_qEsc(x.category)+'</td><td>'+_qEsc(x.genre)+'</td><td>'+_qEsc(x.title_style)+'</td>'
            +'<td style="'+dc+'">'+_qEsc(x.direction)+'</td><td style="'+ac+'">'+_qEsc(x.action)+'</td><td>'+_qEsc(x.confidence)+'</td><td>'+_qEsc(x.entities)+'</td>'
            +'<td><b>'+_qEsc(x.title)+'</b><div style="color:var(--text3);margin-top:3px;white-space:pre-wrap;font-size:11px;max-width:420px">'+_qEsc((x.body||'').slice(0,220))+'</div></td>'
            +'<td><button class="btn btn-outline btn-sm" onclick="_fbEdit('+x.no+')">编辑</button></td></tr>';}).join('')+'</tbody></table>';
    });
}
function _fbEdit(no){
  var x=_fb.byNo[no];if(!x)return;window._fbCur=x;
  var html='<div style="display:grid;grid-template-columns:repeat(4,1fr);gap:10px">'
    +_FB_FACETS.map(function(f){return '<div><label style="display:block;font-size:11px;color:var(--text3);margin-bottom:3px">'+f+'</label>'+_qIn('_fb_f_'+f,'',0,x[f]||'')+'</div>';}).join('')+'</div>'
    +'<label style="display:block;font-size:11px;color:var(--text3);margin:10px 0 3px">tags</label>'+_qIn('_fb_f_tags','',0,x.tags||'')
    +'<label style="display:block;font-size:11px;color:var(--text3);margin:10px 0 3px">title</label>'+_qIn('_fb_f_title','',0,x.title||'')
    +'<label style="display:block;font-size:11px;color:var(--text3);margin:10px 0 3px">body (md)</label>'
    +'<textarea id="_fb_f_body" style="width:100%;min-height:170px;font-family:ui-monospace,Menlo,monospace;font-size:12px;padding:8px;border:1px solid var(--border);border-radius:7px;outline:none;box-sizing:border-box">'+_qEsc(x.body||'')+'</textarea>';
  _qEditOpen('编辑 #'+no,html,_fbSave);
}
function _fbSave(){
  var cur=window._fbCur;if(!cur)return;
  var g=function(id){var e=document.getElementById(id);return e?e.value:'';};
  var body={tags:g('_fb_f_tags'),title:g('_fb_f_title'),body:g('_fb_f_body')};
  _FB_FACETS.forEach(function(f){body[f]=g('_fb_f_'+f);});
  fetch('/api/feedback-patterns/'+cur.no,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
    .then(function(r){return r.json();}).then(function(d){
      var m=document.getElementById('qEditMsg');
      if(d.ok){if(m){m.textContent='✅ 已保存';m.style.color='#15803d';}qToast('已保存');_qEditClose();_fbLoad();}
      else if(m){m.textContent='❌ 保存失败';m.style.color='#b91c1c';}
    });
}

// ── 通用编辑 Modal ─────────────────────────────────────
function _qEditOpen(title,html,onSave){
  document.getElementById('qEditTitle').textContent=title;
  document.getElementById('qEditBody').innerHTML=html;
  document.getElementById('qEditMsg').textContent='';
  var sb=document.getElementById('qEditSaveBtn'); if(sb) sb.style.display=onSave?'':'none';
  window._qEditSaveFn=onSave||null;
  document.getElementById('qEditModal').classList.add('active');
}
function _qEditClose(){var e=document.getElementById('qEditModal');if(e)e.classList.remove('active');}
function _qEditSave(){if(window._qEditSaveFn)window._qEditSaveFn();}


function _lbl(t){return '<label style="display:block;font-size:11px;color:var(--text3);margin:8px 0 3px">'+t+'</label>';}
function _sel(id,opts,val){return '<select id="'+id+'" style="width:100%;font-size:12px;padding:5px 8px;border:1px solid var(--border);border-radius:7px;background:#fff">'+opts.map(function(o){return '<option value="'+o[0]+'"'+(o[0]===val?' selected':'')+'>'+o[1]+'</option>';}).join('')+'</select>';}
function _fsel(id,opts,val,w){return '<select id="'+id+'" onchange="_lexApply()" style="width:'+(w||130)+'px;font-size:12px;padding:5px 8px;border:1px solid var(--border);border-radius:7px;background:#fff">'+opts.map(function(o){return '<option value="'+o[0]+'"'+(o[0]===val?' selected':'')+'>'+o[1]+'</option>';}).join('')+'</select>';}
var _lexFilter={type:'',status:'',q:'',page:1,page_size:50,sort_by:'updated_at',sort_dir:'desc'};
function _qLoadLexicon(){
  var qb=document.getElementById('queueBody'); if(!qb) return;
  var types=[['','全部类型'],['person','人名'],['group','团体'],['work','作品'],['word','词汇'],['variant','形近字'],['font','新字体'],['keep','保留']];
  var stats=[['','全部状态'],['manual','人工'],['auto','自动'],['pending','待审'],['rejected','已撤回']];
  qb.innerHTML=_qHead('📚 术语表','日→中专名整体映射；自动判定 + 人工审（真源=DB，config 仅快照）')
    +'<div style="display:flex;gap:8px;align-items:center;flex-wrap:nowrap;overflow-x:auto;padding:0 2px 8px">'
    +_qIn('lex_q','搜索 term / 中译…',200,_lexFilter.q)
    +_fsel('lex_type',types,_lexFilter.type,120)
    +_fsel('lex_status',stats,_lexFilter.status,120)
    +'<button class="btn btn-outline btn-sm" onclick="_lexApply()" style="flex:none">查询</button>'
    +'<button class="btn btn-outline btn-sm" onclick="_lexNew()" style="flex:none">➕ 新增</button>'
    +'<button class="btn btn-outline btn-sm" onclick="_lexJudge()" style="flex:none">🤖 自动判定待审</button>'
    +'<button class="btn btn-outline btn-sm" onclick="_lexExport()" style="flex:none">⬇ 导出快照</button>'
    +'<span class="table-hdr-count" id="lex_cnt" style="margin-left:auto"></span></div>'
    +_qCard('<div id="lexTable">'+_qWait()+'</div>');
  var e=document.getElementById('lex_q'); if(e){e.value=_lexFilter.q;e.addEventListener('keydown',function(ev){if(ev.key==='Enter')_lexApply();});}
  _lexRender();
}
function _lexApply(){_lexFilter.q=(document.getElementById('lex_q')||{}).value||'';_lexFilter.type=(document.getElementById('lex_type')||{}).value||'';_lexFilter.status=(document.getElementById('lex_status')||{}).value||'';_lexFilter.page=1;_lexRender();}
function _lexSort(col){if(_lexFilter.sort_by===col)_lexFilter.sort_dir=(_lexFilter.sort_dir==='asc'?'desc':'asc');else{_lexFilter.sort_by=col;_lexFilter.sort_dir='asc';}_lexFilter.page=1;_lexRender();}
function _lexGo(p){_lexFilter.page=Math.max(1,p);_lexRender();}
function _lth(label,col,w){var ar=_lexFilter.sort_by===col?(_lexFilter.sort_dir==='asc'?' ▲':' ▼'):'';return '<th style="width:'+w+';cursor:pointer;user-select:none" onclick="_lexSort(\''+col+'\')">'+label+ar+'</th>';}
function _lexRender(){
  var p=new URLSearchParams({type:_lexFilter.type,status:_lexFilter.status,q:_lexFilter.q,page:_lexFilter.page,page_size:_lexFilter.page_size,sort_by:_lexFilter.sort_by,sort_dir:_lexFilter.sort_dir});
  fetch('/api/lexicon?'+p.toString()).then(function(r){return r.json();}).then(function(d){
    var rows=d.rows||[]; window._lexRows=rows;
    var total=d.total||0, ps=d.page_size||50, pg=d.page||1, lp=Math.max(1,Math.ceil(total/ps));
    var c=document.getElementById('lex_cnt'); if(c)c.textContent='共 '+total+' 条';
    var tb=document.getElementById('lexTable'); if(!tb) return;
    if(!rows.length){tb.innerHTML=_qEmpty('无匹配条目');return;}
    var badge=function(s){var m={manual:['#e8f5ee','#15803d','人工'],auto:['#eef2ff','#4f46e5','自动'],pending:['#fff7e6','#b45309','待审'],rejected:['#f3f4f6','#6b7280','撤回']}[s]||['#f3f4f6','#6b7280',s];return '<span class="badge" style="background:'+m[0]+';color:'+m[1]+'">'+m[2]+'</span>';};
    var html='<table><thead><tr>'+_lth('term','term','170px')+'<th style="width:180px">中译 / 动作</th>'+_lth('类型','type','86px')+_lth('状态','status','78px')+_lth('入库时间','created_at','132px')+'<th>来源</th><th style="width:200px">操作</th></tr></thead><tbody>';
    html+=rows.map(function(x,i){
        var zh=(x.action==='keep')?'<span style="color:#b45309">保留不译</span>':('<b style="color:#15803d">'+_qEsc(x.zh||'')+'</b>');
        var act='<button class="btn btn-outline btn-sm" onclick="_lexEdit('+i+')">编辑</button>';
        if(x.status==='pending'||x.status==='auto'){act+=' <button class="btn btn-outline btn-sm" onclick="_lexDo('+i+',\'promote\')">保留</button>';}
        if(x.status!=='rejected'){act+=' <button class="btn btn-outline btn-sm" onclick="_lexDo('+i+',\'revert\')">撤回</button>';}
        act+=' <button class="btn btn-outline btn-sm" onclick="_lexDo('+i+',\'delete\')">删除</button>';
        return '<tr><td style="font-family:ui-monospace,monospace;font-size:12px">'+_qEsc(x.term)+'</td><td>'+zh+'</td><td style="font-size:12px">'+_qEsc(x.type)+'</td>'+
          '<td>'+badge(x.status)+'</td><td style="font-size:11px;color:#999">'+_qEsc((x.created_at||'').slice(0,16))+'</td><td style="font-size:11px;color:#999">'+_qEsc(x.source||'')+'</td><td>'+act+'</td></tr>';
      }).join('');
    html+='</tbody></table>';
    tb.innerHTML=html;
    var foot=document.createElement('div'); foot.className='table-foot';
    foot.innerHTML='<span class="table-foot-info">第 '+pg+' / '+lp+' 页 · 共 '+total+' 条</span>'+
      '<button class="pg-btn" onclick="_lexGo(1)"'+(pg<=1?' disabled style="opacity:.4"':'')+'>«</button>'+
      '<button class="pg-btn" onclick="_lexGo('+(pg-1)+')"'+(pg<=1?' disabled style="opacity:.4"':'')+'>‹</button>'+
      '<button class="pg-btn" onclick="_lexGo('+(pg+1)+')"'+(pg>=lp?' disabled style="opacity:.4"':'')+'>›</button>'+
      '<button class="pg-btn" onclick="_lexGo('+lp+')"'+(pg>=lp?' disabled style="opacity:.4"':'')+'>»</button>';
    tb.appendChild(foot);
  });
}
function _lexNew(){_lexForm('',{});}
function _lexEdit(i){var x=(window._lexRows||[])[i];if(x)_lexForm(x.term,x);}
function _lexForm(term,x){
  x=x||{};
  var html='<div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">'
    +'<div>'+_lbl('term（原文，整体）')+_qIn('lex_f_term','',0,term||'')+'</div>'
    +'<div>'+_lbl('中译 zh')+_qIn('lex_f_zh','',0,x.zh||'')+'</div>'
    +'<div>'+_lbl('type')+_sel('lex_f_type',[['person','person'],['group','group'],['work','work'],['word','word'],['variant','variant'],['font','font'],['keep','keep']],x.type)+'</div>'
    +'<div>'+_lbl('action')+_sel('lex_f_action',[['translate','translate'],['keep','keep'],['flag','flag']],x.action)+'</div>'
    +'<div>'+_lbl('status')+_sel('lex_f_status',[['manual','manual'],['auto','auto'],['pending','pending'],['rejected','rejected']],x.status)+'</div>'
    +'<div>'+_lbl('aliases（逗号分隔）')+_qIn('lex_f_alias','',0,x.aliases||'')+'</div>'
    +'</div>'+_lbl('note')+_qIn('lex_f_note','',0,x.note||'');
  _qEditOpen((term?'编辑 ':'新增 ')+'术语', html, function(){
    var d={term:(document.getElementById('lex_f_term')||{}).value||'',
           zh:(document.getElementById('lex_f_zh')||{}).value||'',
           type:(document.getElementById('lex_f_type')||{}).value||'word',
           action:(document.getElementById('lex_f_action')||{}).value||'translate',
           status:(document.getElementById('lex_f_status')||{}).value||'manual',
           aliases:(document.getElementById('lex_f_alias')||{}).value||'',
           note:(document.getElementById('lex_f_note')||{}).value||''};
    fetch('/api/lexicon',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)})
      .then(function(r){return r.json();}).then(function(res){qToast(res.ok?'已保存':('保存失败 '+(res.msg||'')));_qEditClose();_lexRender();});
  });
}
function _lexDo(i,op){
  var x=(window._lexRows||[])[i]; if(!x) return; var t=x.term;
  if(op==='delete'){showConfirm('彻底删除「'+t+'」？','','删除','btn-red','🗑️').then(function(ok){if(ok)_lexPost('delete',t);});}
  else if(op==='revert'){showConfirm('撤回「'+t+'」？','将从生效表移除（留档）','撤回','btn-red','🚫').then(function(ok){if(ok)_lexPost('revert',t);});}
  else {_lexPost('promote',t,function(){qToast('已保留为人工');});}
}
function _lexPost(op,term,after){
  fetch('/api/lexicon/'+op,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({term:term})})
    .then(function(r){return r.json();}).then(function(d){if(after)after(d);else qToast(d.ok?'已处理':'失败');_lexRender();});
}
function _lexJudge(){qToast('自动判定中…（LLM）');fetch('/api/lexicon/judge',{method:'POST'}).then(function(r){return r.json();}).then(function(d){qToast('判定 '+(d.count||0)+' 条');_lexRender();});}
function _lexExport(){fetch('/api/lexicon/export',{method:'POST'}).then(function(r){return r.json();}).then(function(d){qToast('已导出快照 '+(d.count||0)+' 条');});}


function _tkToday(){var d=new Date();return d.getFullYear()+'-'+String(d.getMonth()+1).padStart(2,'0')+'-'+String(d.getDate()).padStart(2,'0');}
function _qLoadToken(){
  var qb=document.getElementById('queueBody'); if(!qb) return;
  qb.innerHTML=_qHead('💰 Token 用量','三层：按 run 汇总 / 按天汇总 / 明细（默认当天，点日期切换；write article 折叠在 write run 下）')
    +'<div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;padding:0 2px 8px;font-size:12px;color:var(--text2)">汇总近'
    +'<input id="tk_days" type="number" value="30" style="width:64px;padding:4px 6px;border:1px solid var(--border);border-radius:6px">天'
    +'　明细日期<input id="tk_date" type="date" value="'+_tkToday()+'" onchange="_tkFetch()" style="font-size:12px;padding:4px 6px;border:1px solid var(--border);border-radius:6px">'
    +'　run <select id="tk_runsel" onchange="_tkFetch()" style="font-size:12px;padding:4px 8px;border:1px solid var(--border);border-radius:6px"><option value="">全部</option><option>write</option><option>review</option><option>fetch</option><option>split</option></select>'
    +'<button class="btn btn-outline btn-sm" onclick="_tkFetch()">查询</button></div>'
    +_qCard('<div id="tk_run"></div>')
    +_qCard('<div id="tk_chart"></div>')
    +_qCard('<div id="tk_day"></div>')
    +_qCard('<div id="tk_rows">'+_qWait()+'</div>');
  _tkFetch();
}
function _tkPickDate(d){var e=document.getElementById('tk_date');if(e)e.value=d;_tkFetch();}
function _tkFmt(v){v=Math.round(v||0); if(v>=1000000) return (v/1000000).toFixed(1)+'M'; if(v>=1000) return (v/1000).toFixed(0)+'k'; return ''+v;}
function _tkChart(rows){
  if(!rows||!rows.length) return _qEmpty('暂无');
  var data=rows.slice().reverse(); window._tkData=data;
  var W=800,H=220,PL=54,PR=16,PT=16,PB=34, iw=W-PL-PR, ih=H-PT-PB, n=data.length;
  var max=Math.max.apply(null,data.map(function(x){return x.total_tokens||0;})); if(!max) max=1;
  var X=function(i){return PL+(n<=1?iw/2:iw*i/(n-1));};
  var Y=function(v){return PT+ih-(ih*(v||0)/max);};
  var grid='';
  for(var k=0;k<=4;k++){var gv=max*k/4, gy=Y(gv);
    grid+='<line x1="'+PL+'" y1="'+gy+'" x2="'+(W-PR)+'" y2="'+gy+'" stroke="#eee"/>'
      +'<text x="'+(PL-6)+'" y="'+(gy+3)+'" text-anchor="end" font-size="10" fill="#999">'+_tkFmt(gv)+'</text>';}
  var pts=data.map(function(x,i){return X(i)+','+Y(x.total_tokens);}).join(' ');
  var dots=data.map(function(x,i){return '<circle cx="'+X(i)+'" cy="'+Y(x.total_tokens)+'" r="3" fill="#2563eb"/>';}).join('');
  var hits=data.map(function(x,i){return '<circle cx="'+X(i)+'" cy="'+Y(x.total_tokens)+'" r="14" fill="transparent"/>';}).join('');
  var xl=data.map(function(x,i){return '<text x="'+X(i)+'" y="'+(H-10)+'" text-anchor="middle" font-size="9" fill="#999">'+x.d.slice(5)+'</text>';}).join('');
  return '<div style="position:relative"><svg id="tk_svg" viewBox="0 0 '+W+' '+H+'" style="width:100%;height:auto;max-height:240px;cursor:crosshair">'
    +grid+'<polyline points="'+pts+'" fill="none" stroke="#2563eb" stroke-width="2"/>'+dots+hits+xl+'</svg>'
    +'<div id="tk_tip" style="position:absolute;display:none;pointer-events:none;background:rgba(17,24,39,.92);color:#fff;font-size:11px;line-height:1.5;padding:4px 8px;border-radius:6px;white-space:nowrap;z-index:20;box-shadow:0 2px 8px rgba(0,0,0,.25)"></div></div>';
}
function _tkBind(){
  var svg=document.getElementById('tk_svg'), tip=document.getElementById('tk_tip');
  if(!svg||!tip||!window._tkData) return;
  var W=800,PL=54,PR=16,iw=W-PL-PR;
  svg.addEventListener('mousemove',function(e){
    var r=svg.getBoundingClientRect(), n=window._tkData.length;
    var vx=(e.clientX-r.left)/r.width*W;
    var i=Math.round(n<=1?0:(vx-PL)/iw*(n-1)); i=Math.max(0,Math.min(n-1,i));
    var x=window._tkData[i];
    tip.innerHTML='<b>'+x.d+'</b>　合计 '+x.total_tokens+'　'+x.calls+' 调用　'+x.n+' 条';
    tip.style.display='block';
    var rx=e.clientX-r.left, tw=tip.offsetWidth;
    tip.style.left=Math.max(0,Math.min(r.width-tw, rx-tw/2))+'px';
    tip.style.top=(e.clientY-r.top-40)+'px';
  });
  svg.addEventListener('mouseleave',function(){tip.style.display='none';});
}
function _tkToggle(rid){
  var rows=document.querySelectorAll('tr[data-parent="'+rid+'"]'); if(!rows.length) return;
  var open=rows[0].style.display!=='none';
  Array.prototype.forEach.call(rows,function(r){r.style.display=open?'none':'';});
  var c=document.querySelector('tr[data-rid="'+rid+'"] .tk-caret'); if(c) c.textContent=open?'▶':'▼';
}
function _tkCells(x){
  return '<td>'+_qEsc((x.ts||'').slice(0,19))+'</td><td>'+_qEsc(x.run)+'</td><td>'+_qEsc(x.scope)+'</td>'
    +'<td style="font-size:11px">'+(x.key?('<a href="/detail/'+_qEsc(x.key)+'" target="_blank" title="'+_qEsc(x.key)+'">'+_qEsc(x.title||x.key)+'</a>'):'')+'</td>'
    +'<td>'+x.calls+'</td><td>'+x.prompt_tokens+'</td><td>'+x.completion_tokens+'</td><td><b>'+x.total_tokens+'</b></td><td>'+x.cache_hit_tokens+'</td>';
}
function _tkFetch(){
  var days=(document.getElementById('tk_days')||{}).value||'30';
  var date=(document.getElementById('tk_date')||{}).value||_tkToday();
  var run=(document.getElementById('tk_runsel')||{}).value||'';
  fetch('/api/token-usage?days='+encodeURIComponent(days)+'&date='+encodeURIComponent(date)+'&run='+encodeURIComponent(run))
    .then(function(r){return r.json();}).then(function(d){
    var by=d.by_run||[], bd=d.by_day||[], gs=d.groups||[];
    var rs=document.getElementById('tk_run');
    if(rs) rs.innerHTML=(by.length?'<table>'+_qTh([{t:'run',w:'90px'},{t:'次数',w:'70px'},{t:'调用',w:'70px'},{t:'输入',w:'110px'},{t:'输出',w:'90px'},{t:'合计',w:'110px'},{t:'缓存命中',w:'110px'}])+'<tbody>'+
      by.map(function(x){return '<tr><td>'+_qEsc(x.run)+'</td><td>'+x.n+'</td><td>'+x.calls+'</td><td>'+x.prompt_tokens+'</td><td>'+x.completion_tokens+'</td><td><b>'+x.total_tokens+'</b></td><td>'+x.cache_hit_tokens+'</td></tr>';}).join('')+'</tbody></table>':_qEmpty('暂无'));
    var ds=document.getElementById('tk_day');
    if(ds) ds.innerHTML=(bd.length?'<table>'+_qTh([{t:'日期',w:'120px'},{t:'次数',w:'70px'},{t:'调用',w:'70px'},{t:'输入',w:'110px'},{t:'输出',w:'90px'},{t:'合计',w:'110px'},{t:'缓存命中',w:'110px'}])+'<tbody>'+
      bd.map(function(x){var sel=(x.d===date);return '<tr'+(sel?' style="background:#eef4ff"':'')+'><td><a href="#" data-d="'+x.d+'" onclick="_tkPickDate(this.getAttribute(\'data-d\'));return false" style="color:#2563eb">'+_qEsc(x.d)+'</a></td><td>'+x.n+'</td><td>'+x.calls+'</td><td>'+x.prompt_tokens+'</td><td>'+x.completion_tokens+'</td><td><b>'+x.total_tokens+'</b></td><td>'+x.cache_hit_tokens+'</td></tr>';}).join('')+'</tbody></table>':_qEmpty('暂无'));
    var cs=document.getElementById('tk_chart');
    if(cs){cs.innerHTML='<div style="font-size:12px;color:var(--text2);padding:0 2px 6px">近 '+days+' 天 · 每日 token 合计（悬停看明细）</div>'+_tkChart(bd);_tkBind();}
    var t=document.getElementById('tk_rows');
    if(t){
      var head='<table>'+_qTh([{t:'',w:'28px'},{t:'时间',w:'140px'},{t:'run',w:'64px'},{t:'scope',w:'64px'},{t:'标题'},{t:'调用',w:'56px'},{t:'输入',w:'96px'},{t:'输出',w:'80px'},{t:'合计',w:'96px'},{t:'缓存',w:'96px'}])+'<tbody>';
      var body='';
      gs.forEach(function(g){
        var p=g.row, kids=g.children||[], stages=g.stages||[];
        if(p){
          var nch=stages.length?stages.length:kids.length;
          body+='<tr data-rid="'+p.id+'" '+(nch?'onclick="_tkToggle('+p.id+')" style="cursor:pointer"':'')+'>'
            +'<td class="tk-caret" style="text-align:center;color:#888">'+(nch?'▶':'')+'</td>'+_tkCells(p)+'</tr>';
          if(stages.length){
            var rt=g.retry||{};
            var pct=rt.total?Math.round(rt.n*100/rt.total):0;
            body+='<tr data-parent="'+p.id+'" style="display:none;background:#fafafa"><td></td>'
              +'<td colspan="9" style="font-size:11px;color:'+(rt.n?'#b45309':'#888')+'">⚠️ 重试 '+rt.n+' / 共 '+rt.total+' 次 = '+pct+'%，浪费 ~'+rt.tokens+' token</td></tr>';
            stages.forEach(function(s){
              var warn=(s.stage==='score_retry'||s.stage==='regen');
              body+='<tr data-parent="'+p.id+'" style="display:none;background:#fafafa"><td></td>'
                +'<td style="font-size:11px;color:'+(warn?'#b45309':'#888')+'">'+(warn?'⚠️ ':'')+_qEsc(s.stage)+'</td>'
                +'<td style="font-size:11px;color:#888">'+s.n+' 次</td>'
                +'<td colspan="2"></td><td>'+s.calls+'</td><td>'+s.prompt_tokens+'</td><td>'+s.completion_tokens+'</td><td><b>'+s.total_tokens+'</b></td><td>'+s.cache_hit_tokens+'</td></tr>';
            });
          } else {
            kids.forEach(function(k){body+='<tr data-parent="'+p.id+'" style="display:none;background:#fafafa"><td></td>'+_tkCells(k)+'</tr>';});
          }
        } else {
          kids.forEach(function(k){body+='<tr><td></td>'+_tkCells(k)+'</tr>';});
        }
      });
      t.innerHTML=(gs.length?head+body+'</tbody></table>':_qEmpty('暂无'))+'<div class="table-foot"><span class="table-foot-info">'+_qEsc(date)+' · 共 '+(d.total||0)+' 条</span></div>';
    }
  });
}
function _tkGo(p){_tkPage=Math.max(1,p);_tkFetch();}
function _buildWechatView(){
  var wv=document.getElementById('wechatView');
  if(!wv||wv.querySelector('.wx-topbar')) return;
  wv.innerHTML='<div class="wx-topbar"><span class="wx-title">公众号</span><div class="wx-search"><span style="color:var(--text3);font-size:12px">&#128269;</span><input id="wdSearch" placeholder="搜索标题..." oninput="_wdDebounce()"></div><select id="wdSt" class="wx-sel" onchange="loadWechatList()" title="状态筛选"><option value="all">全部状态</option><option value="published">已发布</option><option value="draft">草稿箱</option><option value="pending">待发布</option><option value="none">未配置</option></select><select id="wdSrc" class="wx-sel" onchange="loadWechatList()" title="来源筛选"><option value="">全部来源</option></select><input type="date" id="wdDf" class="wx-date-in" onchange="loadWechatList()" title="入库起始日期"><span style="color:var(--text3);font-size:11px">~</span><input type="date" id="wdDt" class="wx-date-in" onchange="loadWechatList()" title="入库结束日期"><button class="wx-btn" onclick="_wdReset()" title="清空全部筛选">重置</button><div class="wx-stats"><b id="wdTotal">—</b> 总数 &nbsp;|&nbsp; <b id="wdDraft" style="color:var(--orange)">—</b> 草稿 <b id="wdPending" style="color:var(--blue)">—</b> 待发 <b id="wdPublished" style="color:var(--green)">—</b> 已发</div></div><div class="wx-wrap"><div class="wx-card"><div class="wx-thead"><div class="wx-th"></div><div class="wx-th" style="padding-left:12px">标题</div><div class="wx-th">状态</div><div class="wx-th wx-ths" data-k="created_at">入库时间 ↕</div><div class="wx-th wx-ths" data-k="updated_at">编辑时间 ↕</div><div class="wx-th wx-ths" data-k="pub_time">发布时间 ↕</div><div class="wx-th"></div></div><div class="wx-scroll" id="wdBody"><div class="wx-empty">加载中…</div></div></div></div>';
  // 表头点击排序。不用内联 onclick —— 这段 HTML 在 wv.innerHTML='...' 的单引号
  // JS 字符串里，内联的 'created_at' 会把字符串截断（SyntaxError，整段脚本挂掉）。
  Array.prototype.forEach.call(wv.querySelectorAll('.wx-ths'),function(el){
    el.onclick=function(){_wdSortBy(el.getAttribute('data-k'));};
  });
}
var _wdPage=0,_wdPageSize=50,_wdFiltered=[];
var _wdDir='desc',_wdSort='created_at',_wdTimer=null;
// 点表头排序：同一列再点一次切升/降序
function _wdSortBy(col){
  if(_wdSort===col){_wdDir=(_wdDir==='desc')?'asc':'desc';}
  else{_wdSort=col;_wdDir='desc';}
  loadWechatList();
}
function _wdToggleDir(){_wdSortBy(_wdSort);}
function _wdVal(id){var e=document.getElementById(id);return e?e.value:'';}
function _wdParams(){
  var p=new URLSearchParams();
  var q=_wdVal('wdSearch'); if(q)p.set('search',q);
  var st=_wdVal('wdSt');     if(st&&st!=='all')p.set('st',st);
  var src=_wdVal('wdSrc');   if(src)p.set('src',src);
  var df=_wdVal('wdDf');     if(df)p.set('df',df);
  var dt=_wdVal('wdDt');     if(dt)p.set('dt',dt);
  p.set('sort',_wdSort);
  p.set('dir',_wdDir);
  return p;
}
function _wdDebounce(){clearTimeout(_wdTimer);_wdTimer=setTimeout(loadWechatList,400);}
function _wdToggleDir(){
  _wdDir=(_wdDir==='desc')?'asc':'desc';
  var b=document.getElementById('wdDir'); if(b)b.textContent=(_wdDir==='desc'?'↓ 降序':'↑ 升序');
  loadWechatList();
}
function _wdReset(){
  ['wdSearch','wdDf','wdDt'].forEach(function(id){var e=document.getElementById(id);if(e)e.value='';});
  ['wdSt','wdSrc'].forEach(function(id){var e=document.getElementById(id);if(e)e.selectedIndex=0;});
  _wdSort='created_at';_wdDir='desc';
  loadWechatList();
}
function loadWechatList(){
  fetch('/api/wechat-list?'+_wdParams().toString()).then(function(r){return r.json();}).then(function(d){
    _wdAllItems=d.items||[];
    // 来源下拉只填一次，避免每次刷新重建、丢掉当前选择。
    // 变量名别叫 se —— 本函数下面有 function se(id,v)（设统计数字），
    // 函数声明会提升并覆盖同名 var，导致 se.options 报 TypeError 被 .catch 吞掉。
    var srcSel=document.getElementById('wdSrc');
    if(srcSel&&srcSel.options.length<=1&&d.sources){
      d.sources.forEach(function(s){var o=document.createElement('option');o.value=s;o.textContent=s;srcSel.appendChild(o);});
      srcSel.value=_wdVal('wdSrc');
    }
    // 三个日期表头各自显示箭头：当前排序列 ▲/▼，其余 ↕
    var _lab={created_at:'入库时间',updated_at:'编辑时间',pub_time:'发布时间'};
    Array.prototype.forEach.call(document.querySelectorAll('#wechatView .wx-ths'),function(el){
      var k=el.getAttribute('data-k');
      el.textContent=(_lab[k]||k)+' '+(_wdSort===k?(_wdDir==='asc'?'▲':'▼'):'↕');
    });
    // 统计卡片走服务端返回的**全量**统计，不随筛选变化（列表才跟着筛）
    var _st=d.stats||{};
    var tot=_st.total||0;
    function se(id,v){var e=document.getElementById(id);if(e)e.textContent=v;}
    se('wdTotal',tot);se('wdDraft',_st.draft||0);se('wdPending',_st.pending||0);se('wdPublished',_st.published||0);
    var badge=document.getElementById('wechatBadge');
    if(badge&&tot){badge.textContent=tot;badge.style.display='';}
    _wdPage=0;_wdFiltered=_wdAllItems;renderWechatItems(_wdFiltered);
  }).catch(function(){var b=document.getElementById('wdBody');if(b)b.innerHTML='<div class="wx-empty">加载失败</div>';});
}
function _wdGoPage(p){_wdPage=p;renderWechatItems(_wdFiltered);var s=document.querySelector('#wechatView .wx-scroll');if(s)s.scrollTop=0;}
function renderWechatItems(items){
  var body=document.getElementById('wdBody');if(!body)return;
  if(!items||!items.length){body.innerHTML='<div class="wx-empty"><span style="font-size:24px">&#128237;</span>暂无公众号文章</div>';return;}
  var total=items.length,pages=Math.ceil(total/_wdPageSize);
  if(_wdPage>=pages)_wdPage=Math.max(0,pages-1);
  var start=_wdPage*_wdPageSize,end=Math.min(start+_wdPageSize,total);
  var pageItems=items.slice(start,end);
  var html='';
  pageItems.forEach(function(n){
    var _c=n.wechat_image_url||n.image_url;
    var imgSrc=_c?(_c.startsWith('/')?'/local-image?path='+encodeURIComponent(_c):_c):'';
    var thumb=imgSrc?('<img src="'+imgSrc+'" class="thumb-img" onerror="this.className=\'thumb-empty\';this.removeAttribute(\'src\');this.removeAttribute(\'onerror\')">'):'<div class="thumb-empty">&#128240;</div>';
    var wt=esc(n.wechat_title||n.title||'');
    var sub=n.title&&n.wechat_title&&n.title!==n.wechat_title?esc(n.title):'';
    var bc='wx-bm',bl='未配置';
    if(n.wechat_pub_time){bc='wx-bg';bl='已发布';}
    else if(n.wechat_draft_id){bc='wx-by';bl='草稿';}
    else if(n.wechat_publish){bc='wx-bb';bl='待发布';}
    html+='<div class="wx-row" onclick="location.href=this.dataset.url" data-url="/wechat/'+n.key+'">';
    html+=thumb;
    html+='<div class="wx-col-title"><span class="wx-t">'+wt+'</span>';
    if(sub)html+='<span class="wx-sub">'+sub+'</span>';
    html+='</div>';
    html+='<div><span class="wx-b '+bc+'">'+bl+'</span></div>';
    html+='<span class="wx-date">'+((n.created_at||'').slice(0,10))+'</span>';
    html+='<span class="wx-date">'+((n.updated_at||'').slice(0,10))+'</span>';
    html+='<span class="wx-date">'+(((n.wechat_pub_time||'').slice(0,10))||'—')+'</span>';
    html+='<div><a class="wx-btn" href="/wechat/'+n.key+'" onclick="event.stopPropagation()">编辑</a></div>';
    html+='</div>';
  });
  // Pagination
  var total=items.length,pages=Math.ceil(total/_wdPageSize);
  if(pages>1){
    html+='<div style="display:flex;align-items:center;justify-content:center;gap:8px;padding:12px;border-top:1px solid var(--border);flex-shrink:0">';
    html+='<button class="btn btn-xs btn-outline" onclick="_wdGoPage('+(_wdPage-1)+')" '+((_wdPage===0)?'disabled':'')+'>上一页</button>';
    html+='<span style="font-size:11px;color:var(--text2)">'+(_wdPage+1)+' / '+pages+' 页（共'+total+'篇）</span>';
    html+='<button class="btn btn-xs btn-outline" onclick="_wdGoPage('+(_wdPage+1)+')" '+((_wdPage>=pages-1)?'disabled':'')+'>下一页</button>';
    html+='</div>';
  }
  body.innerHTML=html;
}
</script>
<style>
#wechatView .wx-topbar{display:flex;align-items:center;gap:10px;padding:14px 20px 10px;flex-shrink:0;border-bottom:1px solid var(--border);background:var(--card-bg)}
#wechatView .wx-title{font-size:13px;font-weight:600;color:var(--text)}
#wechatView .wx-search{display:flex;align-items:center;gap:7px;background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:5px 11px;width:200px}
#wechatView .wx-search input{border:none;background:none;font-size:12px;color:var(--text);outline:none;width:100%}
#wechatView .wx-sel,#wechatView .wx-date-in{font-size:11.5px;color:var(--text2);background:var(--bg);border:1px solid var(--border);border-radius:8px;padding:5px 8px;outline:none}
#wechatView .wx-sel{cursor:pointer;max-width:130px}
#wechatView .wx-date-in{padding:4px 6px;max-width:130px}
#wechatView .wx-stats{display:flex;gap:10px;align-items:center;margin-left:auto;font-size:11.5px;color:var(--text2)}
#wechatView .wx-stats b{font-weight:600;color:var(--text)}
#wechatView .wx-wrap{flex:1;overflow:hidden;padding:10px 20px 16px;display:flex;flex-direction:column}
#wechatView .wx-card{flex:1;background:var(--card-bg);border-radius:var(--radius);box-shadow:var(--shadow);border:1px solid var(--border);display:flex;flex-direction:column;overflow:hidden}
#wechatView .wx-thead{display:grid;grid-template-columns:42px 1fr 70px 78px 78px 78px 58px;padding:8px 14px;border-bottom:1px solid var(--border);background:#f9fafb;flex-shrink:0}
#wechatView .wx-th{font-size:10px;font-weight:600;color:var(--text3);text-transform:uppercase;letter-spacing:.05em;display:flex;align-items:center}
#wechatView .wx-ths{cursor:pointer;user-select:none}
#wechatView .wx-ths:hover{color:#07c160}
#wechatView .wx-scroll{flex:1;overflow-y:auto}
#wechatView .wx-scroll::-webkit-scrollbar{width:4px}
#wechatView .wx-scroll::-webkit-scrollbar-thumb{background:var(--border);border-radius:2px}
#wechatView .wx-row{display:grid;grid-template-columns:42px 1fr 70px 78px 78px 78px 58px;padding:9px 14px;border-bottom:1px solid var(--border);align-items:center;cursor:pointer;transition:background .08s}
#wechatView .wx-row:last-child{border-bottom:none}
#wechatView .wx-row:hover{background:#f9fafb}
#wechatView .wx-col-title{padding:0 12px;min-width:0}
#wechatView .wx-t{font-size:12.5px;font-weight:600;color:var(--text);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:block}
#wechatView .wx-sub{font-size:11px;color:var(--text3);overflow:hidden;text-overflow:ellipsis;white-space:nowrap;margin-top:1px}
#wechatView .wx-b{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10.5px;font-weight:500;white-space:nowrap}
#wechatView .wx-bg{background:#d1fae5;color:#065f46}
#wechatView .wx-by{background:#fef3c7;color:#92400e}
#wechatView .wx-bb{background:#dbeafe;color:#1d4ed8}
#wechatView .wx-bm{background:#f3f4f6;color:#6b7280}
#wechatView .wx-date{font-size:11px;color:var(--text3)}
#wechatView .wx-btn{height:26px;padding:0 10px;border:1px solid var(--border);border-radius:6px;background:var(--card-bg);cursor:pointer;font-size:11px;color:var(--text2);font-family:var(--font);text-decoration:none;display:inline-flex;align-items:center;transition:all .12s}
#wechatView .wx-btn:hover{border-color:#07c160;color:#07c160}
#wechatView .wx-empty{display:flex;flex-direction:column;align-items:center;justify-content:center;gap:8px;padding:48px 20px;color:var(--text3);font-size:12px}
</style>
<div id="loginModal" onclick="if(event.target===this)closeLogin()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);z-index:9999;align-items:center;justify-content:center">
  <div style="position:relative;background:#16161a;border:1px solid #2a2a2e;border-radius:12px;padding:10px">
    <button onclick="closeLogin()" style="position:absolute;right:10px;top:6px;background:none;border:0;color:#aaa;font-size:16px;cursor:pointer">✕</button>
    <iframe id="loginFrame" src="about:blank" style="width:380px;height:600px;border:0;border-radius:8px;background:#0f0f10"></iframe>
  </div>
</div>
<script>
function openLogin(){var f=document.getElementById('loginFrame');f.src='/login?ts='+Date.now();document.getElementById('loginModal').style.display='flex';}
function closeLogin(){document.getElementById('loginModal').style.display='none';document.getElementById('loginFrame').src='about:blank';refreshLoginDot();}
async function refreshLoginDot(){
  try{
    var c=await (await fetch('/api/login/status')).json();
    var w=await (await fetch('/api/login/www/status')).json();
    var cOn=!!c.logged_in, wOn=!!w.logged_in;
    var e=document.getElementById('loginDot');
    if(e){e.style.background=(cOn&&wOn)?'#07c160':((cOn||wOn)?'#ffc53d':'#ff4d4f');
           e.title='创作号:'+(cOn?'已登录':'未登录')+' / 浏览页:'+(wOn?'已登录':'未登录')
                  +(w.nickname?(' / 账户:'+w.nickname+(w.red_id?(' 小红书号:'+w.red_id):'')):'');}
    var a=document.getElementById('loginAcct');
    if(a){
      var parts=[];
      if(wOn&&w.nickname) parts.push('浏览:'+w.nickname+(w.red_id?('·'+w.red_id):''));
      if(cOn&&c.nickname) parts.push('创作:'+c.nickname+(c.red_id?('·'+c.red_id):''));
      var mism=(wOn&&cOn&&w.red_id&&c.red_id&&w.red_id!==c.red_id);
      a.textContent = parts.length? parts.join('  ') : ((wOn||cOn)?(wOn?'浏览页✓':'创作号✓'):'');
      a.style.color = mism? '#ff6b6b' : '#9fb0c8';
      a.title = mism? ('⚠️ 浏览/创作小红书号不一致：'+w.red_id+' / '+c.red_id)
                    : ('浏览 profileId:'+(w.profile_id||'')+'　小红书号:'+(w.red_id||'')
                       +(c.red_id?('\n创作 小红书号:'+c.red_id):''));
    }
  }catch(err){}
}
refreshLoginDot();setInterval(refreshLoginDot,8000);
</script>
<div id="chromeModal" onclick="if(event.target===this)closeChrome()" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.6);z-index:9999;align-items:center;justify-content:center">
  <div style="position:relative;background:#16161a;border:1px solid #2a2a2e;border-radius:12px;padding:10px">
    <button onclick="closeChrome()" style="position:absolute;right:10px;top:6px;background:none;border:0;color:#aaa;font-size:16px;cursor:pointer">✕</button>
    <iframe id="chromeFrame" src="about:blank" title="容器 Chrome 远程控制"
      style="width:920px;height:560px;max-width:94vw;max-height:80vh;border:0;border-radius:8px;background:#000;display:block"></iframe>
    <div style="margin-top:8px;display:flex;gap:6px;align-items:center">
      <input id="ctlText" placeholder="在此粘贴文本 → 发送到容器 Chrome（不依赖 noVNC 剪贴板）"
        style="flex:1;height:28px;font-size:12px;padding:0 8px;border-radius:6px;border:1px solid #2a2a2e;background:#1c1c20;color:#e8e8e8">
      <button onclick="sendTextToChrome(false)" style="height:28px;padding:0 10px;border-radius:6px;border:1px solid #2a2a2e;background:#1c1c20;color:#e8e8e8;cursor:pointer">发送</button>
      <button onclick="sendTextToChrome(true)" style="height:28px;padding:0 10px;border-radius:6px;border:1px solid #2a2a2e;background:#1c1c20;color:#e8e8e8;cursor:pointer">发送+回车</button>
    </div>
  </div>
</div>
<script>
function openChrome(){var f=document.getElementById('chromeFrame');f.src='http://'+location.hostname+':16080/vnc.html?autoconnect=1&resize=scale&reconnect=1&reconnect_delay=2000';document.getElementById('chromeModal').style.display='flex';}
function closeChrome(){document.getElementById('chromeModal').style.display='none';document.getElementById('chromeFrame').src='about:blank';}
async function sendTextToChrome(enter){try{var t=document.getElementById('ctlText').value;var r=await fetch('/api/creator/type',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text:t,enter:enter})});var d=await r.json();if(!d.ok)alert('发送失败: '+(d.error||''));}catch(e){alert('发送异常: '+e);}}
</script>
</body></html>"""

DETAIL_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{news.title}}</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#f4f4f8;--card-bg:#fff;--text:#1a1a2e;--text2:#6b7280;--text3:#9ca3af;
  --border:#e5e7eb;--red:#ef4444;--orange:#f97316;--green:#10b981;--blue:#3b82f6;--purple:#8b5cf6;
  --shadow:0 1px 3px rgba(0,0,0,.06),0 1px 2px rgba(0,0,0,.04);--radius:10px;
  --font:-apple-system,'Inter','PingFang SC','Segoe UI',sans-serif
}
body{font:13px/1.5 var(--font);background:var(--bg);color:var(--text);height:100vh;display:flex;flex-direction:column;overflow:hidden;-webkit-font-smoothing:antialiased}
/* topbar */
.topbar{background:var(--card-bg);border-bottom:1px solid var(--border);padding:0 20px;height:50px;display:flex;align-items:center;gap:10px;flex-shrink:0;z-index:100}
.topbar a{color:var(--text2);text-decoration:none;font-size:12.5px;font-weight:500;flex-shrink:0;display:flex;align-items:center;gap:4px}
.topbar a:hover{color:var(--text)}
.topbar-title{font-size:13px;font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0}
/* layout */
.detail-layout{flex:1;display:flex;overflow:hidden}
.detail-left{width:300px;min-width:300px;overflow-y:auto;border-right:1px solid var(--border);background:var(--card-bg);display:flex;flex-direction:column}
.detail-right{flex:1;overflow-y:auto;padding:20px 24px}.detail-right>*{margin-bottom:14px}
/* cards */
.card{background:var(--card-bg);border-radius:var(--radius);box-shadow:var(--shadow);border:1px solid var(--border);padding:16px 18px}
.card-section{padding:14px 18px;border-bottom:1px solid var(--border)}
.card-section:last-child{border-bottom:none}
.card-section-title{font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.06em;color:var(--text3);margin-bottom:10px}
/* meta key-value rows */
.meta-kv{display:flex;align-items:flex-start;gap:6px;margin-bottom:5px;font-size:11.5px;line-height:1.4}
.meta-kv .mk{color:var(--text3);width:50px;flex-shrink:0;font-size:10.5px;padding-top:1px}
.meta-kv .mv{color:var(--text);font-weight:500;flex:1;word-break:break-all}
/* settings 2x2 grid */
.sett-grid{display:grid;grid-template-columns:1fr 1fr;gap:7px 10px}
.sett-grid .field-label{margin-bottom:3px}
.sett-grid .meta-select{width:100%;font-size:11px;padding:3px 5px}
/* stat cards */
.stat-cards{display:grid;grid-template-columns:repeat(3,1fr);gap:4px}
.stat-card{display:flex;align-items:center;gap:4px;padding:4px 6px;background:var(--bg);border-radius:6px}
.stat-card .si{font-size:13px;line-height:1;flex-shrink:0}
.stat-card .sn{font-size:12px;font-weight:700;color:var(--text);line-height:1.2}
.stat-card .sl{font-size:9.5px;color:var(--text3)}
/* buttons */
.btn{display:inline-flex;align-items:center;justify-content:center;gap:5px;height:30px;padding:0 13px;border:1px solid transparent;border-radius:7px;cursor:pointer;font-size:11.5px;font-weight:500;white-space:nowrap;transition:all .12s;font-family:var(--font)}
.btn:hover{opacity:.88}
.btn:active{transform:scale(.98)}
.btn:disabled{opacity:.35;pointer-events:none}
.btn-red{background:var(--red);color:#fff;border-color:var(--red)}
.btn-orange{background:var(--orange);color:#fff}
.btn-gray{background:#f3f4f6;color:var(--text2);border-color:var(--border)}
.btn-outline{background:var(--card-bg);border-color:var(--border);color:var(--text2)}
.btn-sm{height:26px;padding:0 10px;font-size:11px;border-radius:6px}
.actions{display:flex;gap:6px;flex-wrap:wrap}
/* meta + badges */
.meta-item{font-size:11.5px;color:var(--text2);display:flex;align-items:center;gap:4px;margin-bottom:5px}
.meta-item b{color:var(--text);font-weight:600}
.badge{display:inline-flex;align-items:center;padding:2px 8px;border-radius:20px;font-size:10.5px;font-weight:600}
.badge-green{background:#f0fdf4;color:#15803d}
.badge-red{background:#fef2f2;color:#b91c1c}
.badge-gray{background:#f3f4f6;color:#6b7280}
.badge-purple{background:#f5f3ff;color:#7c3aed}
.badge-blue{background:#eff6ff;color:#2563eb}
/* form fields */
.field-label{font-size:11px;color:var(--text3);margin-bottom:4px;font-weight:500}
.inline-input,.inline-textarea{border:none;border-bottom:1px solid transparent;background:transparent;padding:5px 0;font:inherit;width:100%;outline:none;transition:border-color .15s;border-radius:0;color:var(--text)}
.inline-input:hover,.inline-textarea:hover{border-bottom-color:var(--border)}
.inline-input:focus,.inline-textarea:focus{border-bottom-color:var(--blue);border-bottom-style:solid}
.inline-textarea{resize:vertical;min-height:80px}
.auto-resize{resize:none;overflow:hidden}
.inline-textarea:focus{border:1px solid var(--blue);border-radius:6px;padding:8px;background:#fafbff}
.field-group{display:flex;flex-direction:column;gap:12px}
.field-row{display:flex;align-items:center;gap:10px}
.field-row-ta{align-items:flex-start}
.field-row-ta .field-label{padding-top:6px}
.field-row .field-label{width:60px;flex-shrink:0;text-align:right}
.field-row .value{flex:1;position:relative}
/* cover */
.cover-img{width:100%;max-height:200px;object-fit:contain;display:block;background:#f0f0f0}
/* url inputs */
.url-input{width:100%;padding:5px 8px;border:1px solid var(--border);border-radius:5px;font-size:11px;color:var(--text2);background:#fafafa;cursor:text;font-family:var(--font)}
/* select */
.meta-select{padding:4px 8px;border:1px solid var(--border);border-radius:6px;font-size:12px;background:var(--card-bg);color:var(--text);font-family:var(--font);outline:none}
.meta-select:focus{border-color:var(--blue)}
/* img strip */
.img-strip{display:flex;gap:8px;overflow-x:auto;padding:4px 0}
.img-strip .img-item{position:relative;flex-shrink:0;cursor:pointer;border-radius:6px;overflow:hidden;transition:opacity .15s}
.img-strip .img-item img{height:100px;border-radius:6px;display:block}
.img-strip .img-item .chk{position:absolute;top:5px;left:5px;width:16px;height:16px;accent-color:var(--red);cursor:pointer}
/* zoom */
#imgZoom{display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:9999;pointer-events:none}
#imgZoom img{max-width:500px;max-height:500px;border-radius:8px;box-shadow:0 12px 48px rgba(0,0,0,.4)}
/* score */
.score-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(80px,1fr));gap:4px}
.score-item{text-align:center;padding:5px 4px;border-radius:6px;font-size:11px;font-weight:500;cursor:pointer;transition:all .15s;position:relative}
.score-item:hover{filter:brightness(.9)}
.score-plus{background:#dcfce7;color:#15803d}
.score-minus{background:#fee2e2;color:#b91c1c}
.score-neutral{background:#f3f4f6;color:#888}
.score-override{border:2px solid #f59e0b}
.reason-tip{display:none;position:absolute;bottom:calc(100% + 4px);left:50%;transform:translateX(-50%);background:#1a1a2e;color:#fff;font-size:11px;padding:5px 9px;border-radius:5px;white-space:nowrap;z-index:10;max-width:240px;white-space:normal;text-align:left;line-height:1.4}
.score-item:hover .reason-tip{display:block}
/* tags */
.tag-row{display:flex;flex-wrap:wrap;align-items:center;gap:4px;min-height:32px;padding:5px 8px;border:1px solid var(--border);border-radius:6px;background:#fafafa}
.tag-bubble{display:inline-flex;align-items:center;background:#eef2ff;color:#4f46e5;padding:2px 8px;border-radius:20px;font-size:11px;gap:5px}
.tag-bubble .del{cursor:pointer;opacity:.5;font-weight:bold}
.tag-bubble .del:hover{opacity:1}
.tag-input{border:none;background:transparent;padding:2px 4px;font-size:11px;width:60px;outline:none;font-family:var(--font)}
/* toast / modal */
.toast{position:fixed;top:20px;right:20px;background:#10b981;color:#fff;padding:10px 18px;border-radius:8px;display:none;z-index:9999;font-weight:500;font-size:12.5px;box-shadow:0 4px 12px rgba(16,185,129,.3)}
.modal{display:none;position:fixed;inset:0;background:rgba(0,0,0,.4);z-index:200;justify-content:center;align-items:center;backdrop-filter:blur(3px)}
.modal.active{display:flex}
.modal-card{background:var(--card-bg);border-radius:12px;max-width:700px;width:90%;max-height:82vh;overflow-y:auto;padding:24px;box-shadow:0 8px 30px rgba(0,0,0,.15)}
.sep-line{border:none;border-top:1px solid var(--border);margin:12px 0}
/* accordion */
.accordion-hdr{display:flex;align-items:center;justify-content:space-between;cursor:pointer;padding:12px 18px;background:var(--bg);border-bottom:1px solid var(--border);user-select:none}
.accordion-hdr:hover{background:#eef0f4}
.accordion-hdr .ah-title{font-size:12px;font-weight:600;color:var(--text);display:flex;align-items:center;gap:6px}
.accordion-hdr .ah-arrow{font-size:11px;color:var(--text3);transition:transform .2s}
.accordion-hdr.open .ah-arrow{transform:rotate(180deg)}
.accordion-body{display:none;padding:14px 18px}
.accordion-body.open{display:block}
/* stats grid */
.stats-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.stat-cell{background:var(--bg);border-radius:7px;padding:8px 10px;text-align:center}
.stat-num{font-size:16px;font-weight:700;color:var(--text);line-height:1}
.stat-label{font-size:10px;color:var(--text3);margin-top:2px}
/* trend chart */
.trend-btn{height:22px;padding:0 8px;border:1px solid var(--border);border-radius:20px;background:var(--bg);font-size:10.5px;font-weight:500;color:var(--text2);cursor:pointer;transition:all .15s}
.trend-btn.active{background:var(--blue);color:#fff;border-color:var(--blue)}
.trend-btn:hover:not(.active){border-color:var(--text3);color:var(--text)}
/* Editor.js overrides */
#editorjs .ce-block__content, #editorjs-rewritten .ce-block__content{max-width:none}
#editorjs .codex-editor__redactor, #editorjs-rewritten .codex-editor__redactor{padding-bottom:20px!important}
#editorjs h2.ce-header, #editorjs-rewritten h2.ce-header{font-size:16px;font-weight:700;margin:16px 0 6px;color:var(--text)}
#editorjs h3.ce-header, #editorjs-rewritten h3.ce-header{font-size:14px;font-weight:600;margin:14px 0 4px;color:var(--text)}
#editorjs .image-tool__image-picture, #editorjs-rewritten .image-tool__image-picture{max-width:100%;border-radius:8px}
</style>
</head>
<body>

<!-- Topbar -->
<div class="topbar">
  <a href="javascript:history.back()">← 返回</a>
  {% if news.fetch_by %}<span class="badge badge-gray" style="flex-shrink:0">{{news.fetch_by}}</span>{% endif %}
  <span class="badge {% if news.primary_format=='story' %}badge-purple{% else %}badge-blue{% endif %}" style="flex-shrink:0">{{news.format_label}}{% if news.is_long_form %} 长文{% endif %}</span>
  {% if news.grade %}<span class="badge" style="flex-shrink:0;background:#eef2ff;color:#4338ca;font-weight:700">评级 {{news.grade}}</span>{% endif %}
  {% if channel_decision and channel_decision.channel %}<span class="badge" style="flex-shrink:0;background:#ede9fe;color:#6d28d9;font-weight:700" title="渠道判定依据：{{channel_decision.reason}}">🎯 {{channel_decision.channel}}</span>{% elif news.channel_hint %}<span class="badge" style="flex-shrink:0;background:#ede9fe;color:#6d28d9;font-weight:700">🎯 {{news.channel_hint}}</span>{% endif %}
  {% if news.material_type %}<span class="badge badge-gray" style="flex-shrink:0" title="素材类型（密度豁免判定：catalog/multi_artist/commentary 免密度）">{{news.material_type}}</span>{% endif %}
  {% if news.manual_review %}<span class="badge" style="flex-shrink:0;background:#fee2e2;color:#b91c1c" title="{{news.manual_reason}}">🚩 待人工</span>{% endif %}
  {% if scores and scores|length > 0 %}
  <span class="badge badge-green" style="flex-shrink:0">📊 {{("%.1f"|format(news.title_score or 0))}}</span>
  {% endif %}
  <span class="topbar-title">{{news.title}}</span>
  <span id="taskBar" style="display:none;font-size:11px;cursor:pointer;color:var(--orange);font-weight:600;background:#fff7ed;padding:3px 9px;border-radius:5px;border:1px solid #fed7aa;flex-shrink:0" onclick="showTaskModal()"></span>
  <button class="btn btn-outline btn-sm" id="regenBtn" onclick="regenerateContent()" style="flex-shrink:0">🔄 重新生成</button>
  <button class="btn btn-outline btn-sm" onclick="regenerateTitleOnly()" style="flex-shrink:0">📝 重拟标题</button>
  <button class="btn btn-red btn-sm" id="saveBtn" style="flex-shrink:0">💾 保存修改</button>
</div>

<!-- Two-column layout -->
<div class="detail-layout">

  <!-- Left panel: metadata + settings + gallery -->
  <div class="detail-left">

    <!-- Cover image -->
    {% if news.image_url %}
    <img src="{{ '/local-image?path=' + news.image_url if news.image_url.startswith('/') else news.image_url }}" class="cover-img">
    {% endif %}

    <!-- Meta info -->
    <div class="card-section">
      <div class="card-section-title">基本信息</div>
      <div class="meta-kv"><span class="mk">来源</span><span class="mv">{{news.source or '-'}}</span></div>
      <div class="meta-kv"><span class="mk">发布时间</span><span class="mv">{{news.pub_time or '-'}}</span></div>
      <div class="meta-kv"><span class="mk">入库时间</span><span class="mv">{{news.created_at[:16] if news.created_at else '-'}}</span></div>
      <div class="meta-kv">
        <span class="mk">XHS时间</span>
        <input type="datetime-local" name="publish_time" value="{{news.publish_time or ''}}" onchange="autoSaveField('publish_time',this.value)" style="flex:1;font-size:11px;padding:2px 4px;border:1px solid var(--border);border-radius:4px;background:var(--card-bg);color:{% if news.publish_time %}var(--green){% else %}var(--text2){% endif %}" title="手动设置XHS发布时间">
      </div>
      <div style="margin-top:8px">
        <a href="{{news.link or ''}}" target="_blank" style="font-size:11px;color:var(--blue);text-decoration:none;display:flex;align-items:center;gap:4px" title="{{news.link or ''}}">
          <span>🔗</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">查看原文</span>
        </a>
      </div>
      <div style="margin-top:8px">
        <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:4px">
          <span style="font-size:10.5px;color:var(--text3);font-weight:600;text-transform:uppercase;letter-spacing:.04em">关联文章</span>
          <button onclick="editRelatedKeys()" style="font-size:10px;padding:1px 6px;border:1px solid var(--border);border-radius:4px;background:none;cursor:pointer;color:var(--text3)">✏️</button>
        </div>
        {% if related_articles %}
          {% for r in related_articles %}
          <a href="/detail/{{r.key}}" target="_blank" style="display:block;font-size:11px;color:var(--blue);text-decoration:none;margin-bottom:3px;padding:4px 8px;border-radius:4px;background:#f5f7fa;line-height:1.5" title="{{r.title}}">{{r.title or r.key[:12]}}</a>
          {% endfor %}
        {% else %}
          <span style="color:var(--text3);font-size:11px">无</span>
        {% endif %}
      </div>
    </div>

    <!-- Settings -->
    <div class="card-section">
      <div class="card-section-title" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:10px">
        <span>发布设置</span>
        <span style="cursor:pointer;font-size:20px;user-select:none;line-height:1;color:{% if news.preselected %}#f59e0b{% else %}var(--text3){% endif %}" id="preselectStar" title="{{'取消预选' if news.preselected else '预选标记'}}" onclick="toggleDetailPreselect('{{news.key}}',this)">{{'★' if news.preselected else '☆'}}</span>
      </div>
      <div class="sett-grid">
        <div>
          <div class="field-label">发布</div>
          <select name="publish_xhs" onchange="autoSaveField('publish_xhs',this.value)" class="meta-select">
            <option value="0" {{'selected' if not news.publish_xhs else ''}}>不发布</option>
            <option value="1" {{'selected' if news.publish_xhs else ''}}>发布XHS</option>
          </select>
        </div>
        <div>
          <div class="field-label">模式</div>
          <select name="publish_mode" onchange="onModeChange(this.value)" class="meta-select">
            <option value="normal" {{'selected' if news.publish_mode == 'normal' or not news.publish_mode else ''}}>默认</option>
            <option value="caption" {{'selected' if news.publish_mode == 'caption' else ''}}>短配文</option>
            <option value="free" {{'selected' if news.publish_mode == 'free' else ''}}>自由</option>
            <option value="rewritten" {{'selected' if news.publish_mode == 'rewritten' else ''}}>改写文</option>
          </select>
        </div>
        <div>
          <div class="field-label">发布方式</div>
          <select name="publish_method" onchange="autoSaveField('publish_method',this.value)" class="meta-select">
            <option value="post" {{'selected' if news.publish_method == 'post' or not news.publish_method else ''}}>发帖</option>
            <option value="export" {{'selected' if news.publish_method == 'export' else ''}}>导出</option>
          </select>
        </div>
        <div>
          <div class="field-label">状态</div>
          <select name="status" onchange="autoSaveField('status',this.value)" class="meta-select">
            <option value="active" {{'selected' if news.status=='active' else ''}}>活跃</option>
            <option value="discarded" {{'selected' if news.status=='discarded' else ''}}>丢弃</option>
            <option value="archived" {{'selected' if news.status=='archived' else ''}}>归档</option>
          </select>
        </div>
        <div>
          <div class="field-label">预发布时间</div>
          <input type="datetime-local" name="xhs_pub_time" value="{{news.xhs_pub_time or ''}}" onchange="autoSaveField('xhs_pub_time',this.value.replace('T',' '))" style="font-size:11px;padding:2px 4px;border:1px solid var(--border);border-radius:4px;background:var(--card-bg);color:var(--text);width:175px">
        </div>
      </div>
    </div>

    {% if news.publish_mode == 'free' %}
    <div class="card-section" id="freeTextSection">
      <div class="card-section-title">自由发布内容</div>
      <textarea class="inline-textarea auto-resize" name="publish_free_text" style="min-height:80px" oninput="autoSaveField('publish_free_text',this.value)">{{news.publish_free_text or ''}}</textarea>
    </div>
    {% endif %}

    <!-- Tags -->
    <div class="card-section">
      <div class="card-section-title" style="display:flex;align-items:center;justify-content:space-between">
        <span>标签</span>
        <button onclick="copyTags(this)" style="font-size:10px;padding:2px 8px;border:1px solid var(--border);border-radius:4px;background:var(--card-bg);cursor:pointer;color:var(--text2)" title="复制所有标签">📋 复制</button>
      </div>
      <div class="tag-row" id="tagBubbles"></div>
    </div>

    <!-- Score (display only → left panel) -->
    {% if scores and scores|length > 0 %}
    <div class="card-section">
      <div class="card-section-title" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <span>评分明细</span>
        <div style="display:flex;gap:5px">
          <button class="btn btn-red btn-sm" id="tabTitle" onclick="filterScoreTab('标题')" style="height:22px;font-size:10px">标题 {{"%.1f"|format(news.title_score or 0)}}</button>
          <button class="btn btn-gray btn-sm" id="tabContent" onclick="filterScoreTab('内容')" style="height:22px;font-size:10px">内容 {{"%.1f"|format(news.content_score or 0)}}</button>
        </div>
      </div>
      <div class="score-grid" id="scoreGrid">
        {% for d in scores %}
        <div class="score-item {% if d.calc=='加分' %}score-plus{% elif d.calc=='减分' %}score-minus{% else %}score-neutral{% endif %} {% if d.human_override %}score-override{% endif %}" data-cat="{{d.category}}" data-dim="{{d.dimension}}" data-val="{{d.value}}" onclick="toggleScore(this)" style="{% if d.category!='标题' %}display:none{% endif %}">{{d.dimension}}: {{d.value}}<span class="reason-tip">{{d.reason}}</span></div>
        {% endfor %}
      </div>
    </div>
    {% endif %}

    <!-- XHS Stats (conditional) -->
    {% if news.publish_xhs %}
    <div class="card-section">
      <div class="card-section-title">XHS 实发数据</div>
      {% set stats = [
        ('👁', '浏览', news.xhs_views or 0, ''),
        ('❤️', '点赞', news.xhs_likes or 0, ''),
        ('⭐', '收藏', news.xhs_saves or 0, ''),
        ('💬', '评论', news.xhs_comments or 0, ''),
        ('🔄', '分享', news.xhs_shares or 0, ''),
        ('➕', '涨粉', news.xhs_fans_gained or 0, ''),
        ('👀', '曝光', news.xhs_impression or 0, ''),
        ('🎯', '点击率', ((news.xhs_click_rate or 0)*100)|round(1), '%'),
      ] %}
      <div class="stat-cards">
        {% for icon, label, val, unit in stats %}
        <div class="stat-card">
          <span class="si">{{icon}}</span>
          <div>
            <div class="sn">{{val}}{{unit}}</div>
            <div class="sl">{{label}}</div>
          </div>
        </div>
        {% endfor %}
        {% if news.xhs_saves and news.xhs_views %}
        <div class="stat-card">
          <span class="si">📊</span>
          <div>
            <div class="sn">{{(news.xhs_saves / news.xhs_views * 100)|round(1)}}%</div>
            <div class="sl">收藏率</div>
          </div>
        </div>
        {% endif %}
      </div>
    </div>

    <!-- Metrics trend chart -->
    <div class="card-section" id="trendSection" style="display:none">
      <div class="card-section-title" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">
        <span>📈 增长趋势</span>
      </div>
      <div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:10px">
        <button class="trend-btn active" data-metric="views">浏览</button>
        <button class="trend-btn" data-metric="likes">点赞</button>
        <button class="trend-btn" data-metric="saves">收藏</button>
        <button class="trend-btn" data-metric="click_rate">点击率</button>
      </div>
      <div style="position:relative;height:140px">
        <canvas id="metricsChart"></canvas>
      </div>
      <p id="trendEmpty" style="display:none;font-size:11px;color:var(--text3);text-align:center;padding:20px 0">暂无增长数据</p>
    </div>
    {% endif %}

  </div><!-- /detail-left -->

  <!-- Right panel: score + content editing + japanese -->
  <div class="detail-right">

    <!-- URLs + Gallery (editing → right panel) -->
    <div class="card" style="padding:0;overflow:hidden">
      <div class="accordion-hdr" onclick="this.classList.toggle('open');this.nextElementSibling.classList.toggle('open')">
        <span class="ah-title">📸 图集 / 封面</span><span class="ah-arrow">▼</span>
      </div>
      <div class="accordion-body" style="padding:14px 18px">
      <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:12px">
        <div><div class="field-label">封面图路径</div><input class="url-input" name="image_url" value="{{news.image_url or ''}}" onclick="this.select()"></div>
        {% if news.original_image_url and news.original_image_url != news.image_url %}
        <div><div class="field-label">原始图片</div><a href="{{news.original_image_url}}" target="_blank" style="font-size:11px;color:var(--blue);text-decoration:none;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">{{news.original_image_url}}</a></div>
        {% endif %}
        <div><div class="field-label">图集来源</div><input class="url-input" name="gallery_url" value="{{news.gallery_url or ''}}" placeholder="https://..." onclick="this.select()"></div>
      </div>
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px">
        <button class="btn btn-gray btn-sm" onclick="downloadGallery()" id="galleryBtn">📥 下载图集</button>
        <button class="btn btn-gray btn-sm" id="manageGalleryBtn" onclick="toggleGalleryModal()" {% if not news.gallery_images and not news.cached_images %}style="display:none"{% endif %}>🖼️ 管理图集</button>
      </div>
      <pre id="galleryLog" style="display:none;margin-bottom:8px;padding:8px;background:#1e1e1e;color:#0f0;border-radius:5px;font-size:10px;max-height:150px;overflow-y:auto;white-space:pre-wrap;font-family:Menlo,monospace"></pre>
      {% if news.gallery_images or news.gallery_video %}
      <div class="img-strip" id="publishImgStrip">
        {% for p in news.gallery_images %}
        <div class="img-item" onclick="togglePublishImg(this)" style="display:flex;flex-direction:column;align-items:center">
          {% if p.endswith('.mp4') %}
          <video src="/local-image?path={{p}}" style="height:100px;border-radius:6px"></video>
          {% else %}
          <img src="/local-image?path={{p}}">
          {% endif %}
          <input type="checkbox" class="chk" data-path="{{p}}" onclick="event.stopPropagation()">
          <button class="btn btn-gray" style="font-size:9px;padding:1px 5px;position:absolute;bottom:2px;right:2px" onclick="event.stopPropagation();setAsCover('{{p}}')" title="设为封面">📷</button>
        </div>
        {% endfor %}
        {% if news.gallery_video %}
        <div class="img-item" onclick="togglePublishImg(this)" style="display:flex;flex-direction:column;align-items:center">
          <video src="/local-image?path={{news.gallery_video}}" style="height:100px;border-radius:6px"></video>
          <span style="font-size:9px;color:var(--red);margin-top:2px">🎬 视频</span>
          <input type="checkbox" class="chk" data-path="{{news.gallery_video}}" onclick="event.stopPropagation()">
        </div>
        {% endif %}
      </div>
      {% endif %}
      </div><!-- /accordion-body -->
    </div><!-- /gallery card -->

    <!-- 内容编辑卡片 -->
    <div class="card" style="padding:0;overflow:hidden;margin-bottom:14px">
        <div class="accordion-hdr" onclick="this.classList.toggle('open');this.nextElementSibling.classList.toggle('open')">
          <span class="ah-title">✏️ 内容编辑</span><span class="ah-arrow">▼</span>
        </div>
        <div class="accordion-body">
      <div class="field-group">
        <div>
          <div class="field-label">标题 <span id="titleCount" style="float:right;font-size:11px;color:var(--text3)"></span></div>
          <input class="inline-input" name="title" id="titleInput" value="{{news.title}}" oninput="updateTitleCount()" style="font-size:15px;font-weight:600;padding-bottom:8px">
        </div>
        <div>
          <div class="field-label">🎬 短配文</div>
          <textarea class="inline-textarea auto-resize" name="video_caption" style="min-height:36px">{{news.video_caption or ''}}</textarea>
        </div>
        <div>
          <div class="field-label">引流摘要</div>
          <input class="inline-input" name="summary" value="{{news.summary or ''}}">
        </div>
        <hr class="sep-line">
        <div>
          <div class="field-label">新闻要点 <span id="contentCount" style="float:right;font-size:11px;color:var(--text3);font-weight:400"></span></div>
          <textarea name="content" id="contentHidden" style="display:none">{{news.content or ''}}</textarea>
          {% if story_parts is defined and (story_parts or news.primary_format=='story') %}
          <div id="editorjs" style="border:1px solid var(--border);border-radius:8px;padding:4px 0;background:var(--bg);min-height:200px"></div>
          <div style="display:flex;gap:6px;margin-top:8px;justify-content:flex-end">
            <button class="btn btn-gray btn-sm" onclick="openImgPicker()">📷 插入图片</button>
            <button class="btn btn-sm" onclick="openStoryPreview()" style="background:#7c3aed;color:#fff">👁 预览</button>
          </div>
          <div id="imgPicker" style="display:none;position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);background:#fff;border:1px solid var(--border);border-radius:10px;padding:14px;z-index:8000;box-shadow:0 8px 32px rgba(0,0,0,.25);width:360px;max-height:70vh;overflow-y:auto">
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:10px">
              <span style="font-size:13px;font-weight:600">选择图片插入</span>
              <button onclick="closeImgPicker()" style="background:none;border:none;font-size:16px;cursor:pointer;color:var(--text3)">✕</button>
            </div>
            <div id="imgPickerGrid" style="display:flex;flex-wrap:wrap;gap:6px"></div>
          </div>
          {% else %}
          <textarea class="inline-textarea auto-resize" name="content" style="min-height:120px" oninput="updateContentCount()">{{news.content or ''}}</textarea>
          {% endif %}
        </div>
        <div>
          <div class="field-label">我的解读</div>
          <textarea class="inline-textarea auto-resize" name="comment" style="min-height:100px">{{news.comment or ''}}</textarea>
        </div>
      </div>
        </div><!-- /accordion-body -->
      </div><!-- /内容编辑 card -->

    <!-- 改写卡片 -->
    <div class="card" style="padding:0;overflow:hidden;margin-bottom:14px">
      <div class="accordion-hdr" onclick="this.classList.toggle('open');this.nextElementSibling.classList.toggle('open')">
        <span class="ah-title">✍️ 改写</span><span class="ah-arrow">▼</span>
      </div>
      <div class="accordion-body" style="padding:14px">
      <div class="field-label">改写标题 <span id="rewrittenTitleCount" style="float:right;font-size:11px;color:var(--text3)"></span></div>
      <input class="inline-input" name="rewritten_title" value="{{news.rewritten_title or ''}}" style="margin-bottom:10px" onchange="autoSaveField('rewritten_title',this.value)" oninput="updateRewrittenTitleCount()">
      <div class="field-label">改写文 <span id="rewrittenCount" style="float:right;font-size:11px;color:var(--text3);font-weight:400"></span></div>
      <textarea name="rewritten_content" id="rewrittenHidden" style="display:none">{{news.rewritten_content or ''}}</textarea>
      <div id="editorjs-rewritten" style="border:1px solid var(--border);border-radius:8px;padding:4px 0;background:var(--bg);min-height:120px"></div>
      </div><!-- /accordion-body -->
    </div><!-- /改写 card -->

    <!-- English version card -->
    {% if news.en_title or news.en_content or news.en_tweet %}
    <div class="card" style="padding:0;overflow:hidden;margin-bottom:14px">
      <div class="accordion-hdr" onclick="this.classList.toggle('open');this.nextElementSibling.classList.toggle('open')">
        <span class="ah-title">🇬🇧 English</span>
        <span class="ah-arrow">▼</span>
      </div>
      <div class="accordion-body" style="padding:14px">
        <div class="field-label">English Title</div>
        <input class="inline-input" name="en_title" value="{{news.en_title or ''}}" style="margin-bottom:10px" onchange="autoSaveField('en_title',this.value)">
        <div class="field-label">English Content <span style="float:right;font-size:11px;color:var(--text3)">{{(news.en_content or '')|length}} chars</span></div>
        <textarea class="auto-resize" name="en_content" style="width:100%;min-height:120px;border:1px solid var(--border);border-radius:8px;padding:10px;font-size:13px;line-height:1.6;background:var(--bg);color:var(--text);font-family:inherit;box-sizing:border-box" oninput="autoSaveField('en_content',this.value)">{{news.en_content or ''}}</textarea>
        <div class="field-label" style="margin-top:10px">Tweet (≤280 chars) <span id="enTweetCount" style="float:right;font-size:11px;color:var(--text3)">{{(news.en_tweet or '')|length}} chars</span></div>
        <textarea class="auto-resize" name="en_tweet" id="enTweetTextarea" style="width:100%;min-height:48px;border:1px solid var(--border);border-radius:8px;padding:10px;font-size:13px;line-height:1.5;background:var(--bg);color:var(--text);font-family:inherit;box-sizing:border-box" oninput="document.getElementById('enTweetCount').textContent=this.value.length+' chars';autoSaveField('en_tweet',this.value)">{{news.en_tweet or ''}}</textarea>
        <button onclick="var t=document.getElementById('enTweetTextarea');var v=t.value;if(navigator.clipboard&&navigator.clipboard.writeText){navigator.clipboard.writeText(v)}else{t.select();document.execCommand('copy')}this.textContent='✅ 已复制';setTimeout(()=>this.textContent='📋 复制',2000)" style="margin-top:6px;font-size:11px;padding:4px 10px;border:1px solid var(--border);border-radius:6px;background:var(--card-bg);color:var(--text2);cursor:pointer">📋 复制</button>
      </div>
    </div>
    {% endif %}

    <!-- Japanese original (accordion, conditional) -->
    {% if news.title_ja or news.content_ja %}
    <div class="card" style="padding:0;overflow:hidden">
      <div class="accordion-hdr" onclick="this.classList.toggle('open');this.nextElementSibling.classList.toggle('open')">
        <span class="ah-title">📰 日文原文</span>
        <span class="ah-arrow">▼</span>
      </div>
      <div class="accordion-body">
        {% if news.title_ja %}<p style="font-size:10.5px;color:var(--text3);margin-bottom:2px">日文标题</p><p style="font-size:13px;font-weight:600;margin-bottom:12px;color:var(--text)">{{news.title_ja}}</p>{% endif %}
        {% if news.content_ja %}<p style="font-size:10.5px;color:var(--text3);margin-bottom:4px">日文正文 <span style="float:right">{{news.content_ja|length}} 字</span></p><p style="font-size:12px;color:var(--text2);white-space:pre-wrap;line-height:1.7;max-height:400px;overflow-y:auto">{{news.content_ja}}</p>{% endif %}
      </div>
    </div>
    {% endif %}

  </div><!-- /detail-right -->

</div><!-- /detail-layout -->

<div class="toast" id="toast">已保存</div>

<!-- Story 预览 Modal -->
<!-- Confirm modal (detail) -->
<div class="modal" id="confirmModal" style="z-index:300">
  <div class="modal-card" style="max-width:380px;text-align:center">
    <div style="font-size:32px;margin-bottom:12px" id="confirmIcon">⚠️</div>
    <p id="confirmMsg" style="font-size:14px;font-weight:600;color:var(--text);margin-bottom:6px"></p>
    <p id="confirmSub" style="font-size:12px;color:var(--text2);margin-bottom:20px"></p>
    <div style="display:flex;gap:10px;justify-content:center">
      <button class="btn btn-gray" style="min-width:80px" onclick="_confirmResolve(false)">取消</button>
      <button class="btn btn-red" style="min-width:80px" id="confirmOkBtn" onclick="_confirmResolve(true)">确定</button>
    </div>
  </div>
</div>

<div id="storyPreviewModal" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:9000;overflow-y:auto;padding:20px" onclick="if(event.target===this)this.style.display='none'">
  <div style="max-width:420px;margin:0 auto;background:#fff;border-radius:16px;padding:0 0 24px;position:relative;box-shadow:0 12px 40px rgba(0,0,0,.3)">
    <div style="display:flex;align-items:center;justify-content:space-between;padding:14px 16px 10px;border-bottom:1px solid #f0f0f0">
      <span style="font-size:13px;font-weight:600;color:#333">📱 小红书预览</span>
      <button onclick="document.getElementById('storyPreviewModal').style.display='none'" style="background:none;border:none;font-size:18px;cursor:pointer;color:#999;line-height:1">✕</button>
    </div>
    <div id="storyPreviewBody" style="padding:16px;font-family:-apple-system,sans-serif"></div>
  </div>
</div>

<div class="modal" id="taskModal" onclick="if(event.target===this)closeTaskModal()">
  <div class="modal-card" style="max-width:750px;background:#1e1e1e;color:#0f0">
    <h3 id="taskModalTitle" style="color:#fff;margin-bottom:12px">🖥️ 终端</h3>
    <pre id="taskLog" style="font:12px Menlo,monospace;white-space:pre-wrap;min-height:200px;max-height:50vh;overflow-y:auto;margin:0">等待中...</pre>
    <div style="margin-top:12px;text-align:right"><button class="btn" style="background:#dc3545;color:#fff" onclick="stopTask()">🛑 终止</button> <button class="btn" style="background:#555;color:#fff" onclick="closeTaskModal()">关闭</button></div>
  </div>
</div>
<script>function closeTaskModal(){document.getElementById('taskModal').classList.remove('active')}
async function stopTask(){if(await showConfirm('确定终止当前任务？','','终止','btn-red','🛑')){await fetch('/api/task/regen_'+key+'/stop',{method:'POST'});await fetch('/api/task/regen-title_'+key+'/stop',{method:'POST'});location.reload()}}</script>

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
let _confirmResolve=null;
function showConfirm(msg,sub='',okLabel='确定',okClass='btn-red',icon='⚠️'){
  return new Promise(resolve=>{
    _confirmResolve=v=>{document.getElementById('confirmModal').classList.remove('active');resolve(v)};
    document.getElementById('confirmMsg').textContent=msg;
    document.getElementById('confirmSub').textContent=sub;
    document.getElementById('confirmSub').style.display=sub?'':'none';
    document.getElementById('confirmIcon').textContent=icon;
    document.getElementById('confirmOkBtn').textContent=okLabel;
    document.getElementById('confirmOkBtn').className='btn '+okClass;
    document.getElementById('confirmModal').classList.add('active');
  });
}
{% if scores and scores|length > 0 %}
function filterScoreTab(cat){
  document.getElementById('tabTitle').className=cat==='标题'?'btn btn-red btn-sm':'btn btn-gray btn-sm';
  document.getElementById('tabContent').className=cat==='内容'?'btn btn-red btn-sm':'btn btn-gray btn-sm';
  document.querySelectorAll('#scoreGrid .score-item').forEach(el=>{
    el.style.display=el.dataset.cat===cat?'':'none';
  });
}
{% endif %}
function esc(s){return String(s||'').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}
async function autoSaveField(field,val){
  var data={};data[field]=field==='publish_xhs'?parseInt(val):val;
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(data)});
  var t=document.getElementById('toast');t.textContent='已保存';t.style.display='block';setTimeout(()=>t.style.display='none',1000);
}
async function toggleDetailPreselect(key,el){
  const cur=el.textContent==='★';
  el.textContent=cur?'☆':'★';
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({preselected:cur?0:1})});
}
async function onModeChange(val){
  await autoSaveField('publish_mode',val);
  if(val==='free'||val==='rewritten'){location.reload();return}
  var s=document.getElementById('freeTextSection'); if(s)s.remove();
  s=document.getElementById('rewrittenSection'); if(s)s.remove();
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
async function regenerateTitleOnly(){
  var btn=document.querySelector('[onclick=\"regenerateTitleOnly()\"]'),orig=btn.textContent;
  runTask({title:'📝 重拟标题',apiUrl:'/api/regenerate-title/'+key,apiBody:{},btn:btn,origText:orig,onDone:()=>location.reload()});
}
async function regenerateContent(){
  if(!await showConfirm('重新生成会覆盖当前内容','标题和正文将被重新生成，无法撤销','重新生成','btn-red','🔄'))return;
  var btn=document.getElementById('regenBtn'),orig=btn.textContent;
  runTask({title:'🔄 重新生成',apiUrl:'/api/regenerate/'+key,apiBody:{},btn:btn,origText:orig,onDone:()=>location.reload()});
}
// Check if regen / regen-title is already running on page load
(async function checkRegenRunning(){
  // 先查重新生成
  var r=await fetch('/api/task/regen_'+key);var d=await r.json();
  var tid='regen_'+key,title='🔄 重新生成',btnId='regenBtn';
  if(d.status!=='running'){
    // 再查重拟标题
    r=await fetch('/api/task/regen-title_'+key);d=await r.json();
    tid='regen-title_'+key;title='📝 重拟标题';btnId='';
  }
  if(d.status==='running'){
    var btn=btnId?document.getElementById(btnId):document.querySelector('[onclick=\"regenerateTitleOnly()\"]');
    if(btn){btn.disabled=true;btn.style.opacity='0.6';btn.style.background='var(--red)';btn.style.color='#fff';btn.textContent='⏳ 运行中...'}
    document.getElementById('taskModalTitle').textContent=title;
    document.getElementById('taskLog').textContent=d.log||'⏳ 运行中...';
    document.getElementById('taskModal').classList.add('active');
    var lastLen=(d.log||'').length;
    for(var i=0;i<60;i++){
      await new Promise(r=>setTimeout(r,2000));
      try{var sr=await fetch('/api/task/'+tid);var sd=await sr.json()}catch(e){continue}
      if(sd.log&&sd.log.length>lastLen){document.getElementById('taskLog').textContent=sd.log;lastLen=sd.log.length}
      if(sd.status==='done'){if(btn){btn.textContent='✅ 完成';btn.style.background='';btn.style.color='';btn.style.opacity='1'}setTimeout(()=>location.reload(),1000);return}
      if(sd.status&&sd.status.startsWith('error')){if(btn){btn.textContent='❌ 失败';btn.style.background='';btn.style.color='';btn.style.opacity='1';btn.disabled=false}return}
    }
  }
})();
async function setAsCover(path){
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({image_url:path})});
  location.reload();
}

let tags={% if news.tags %}{{news.tags|tojson}}{% else %}[]{% endif %};
let allTagSuggestions=[];
fetch('/api/all-tags').then(r=>r.json()).then(d=>{allTagSuggestions=d.tags});

function renderTags(){
  const el=document.getElementById('tagBubbles');
  el.innerHTML=tags.map((t,i)=>`<span class="tag-bubble">${esc(t)}<span class="del" onclick="delTag(${i})">×</span></span>`).join('')
    +'<div style="position:relative;display:inline-flex;flex:1;min-width:60px">'
    +'<input class="tag-input" id="tagInput" placeholder="+添加" autocomplete="off" oninput="filterTagSuggestions()" onkeydown="addTag(event)" onblur="setTimeout(()=>{const d=document.getElementById(\'tagDropdown\');if(d)d.style.display=\'none\'},200)" onfocus="filterTagSuggestions()">'
    +'<div id="tagDropdown" style="display:none;position:absolute;top:100%;left:0;right:0;max-height:200px;overflow-y:auto;background:var(--card-bg);border:1px solid var(--border);border-radius:6px;z-index:999;box-shadow:0 4px 12px rgba(0,0,0,0.1);font-size:11px;color:var(--text)"></div>'
    +'</div>';
}
function filterTagSuggestions(){
  const input=document.getElementById('tagInput');
  const dropdown=document.getElementById('tagDropdown');
  const val=(input.value||'').toLowerCase().trim();
  if(!val){dropdown.style.display='none';return}
  const matches=allTagSuggestions.filter(t=>t.toLowerCase().includes(val)&&!tags.includes(t)).slice(0,20);
  if(!matches.length){dropdown.style.display='none';return}
  dropdown.innerHTML=matches.map(t=>`<div style="padding:5px 8px;cursor:pointer;border-bottom:1px solid var(--border);white-space:nowrap;overflow:hidden;text-overflow:ellipsis" onmousedown="event.preventDefault();selectTagSuggestion('${esc(t).replace(/'/g,"\\'")}')">${esc(t)}</div>`).join('');
  dropdown.style.display='block';
}
function selectTagSuggestion(t){
  const input=document.getElementById('tagInput');
  input.value=t;
  document.getElementById('tagDropdown').style.display='none';
  tags.push(t);renderTags();autoSaveField('tags',tags);
}
function delTag(i){tags.splice(i,1);renderTags();autoSaveField('tags',tags)}
function editRelatedKeys(){
  const val=prompt('输入关联文章 key（逗号分隔）:', '{{news.related_keys or ''}}');
  if(val!==null) autoSaveField('related_keys',val).then(()=>location.reload());
}
async function copyTags(btn){
  const text=tags.map(t=>'#'+t).join(' ');
  try{await navigator.clipboard.writeText(text)}
  catch{
    const ta=document.createElement('textarea');
    ta.value=text;ta.style.position='fixed';ta.style.left='-9999px';
    document.body.appendChild(ta);ta.select();
    document.execCommand('copy');document.body.removeChild(ta);
  }
  btn.textContent='✅ 已复制'; setTimeout(()=>btn.textContent='📋 复制',1500);
}
function addTag(e){
  if(e.key==='Enter'||e.key===','){
    e.preventDefault();
    var v=e.target.value.trim().replace(/,$/,'');
    v=v.replace(/[\s　]+/g,'').replace(/[\"'""「」『』【】&]/g,'').replace(/^#+/,'');
    if(v){tags.push(v);e.target.value='';renderTags();autoSaveField('tags',tags)}
    document.getElementById('tagDropdown').style.display='none';
  }
}
renderTags();
function xhsTitleLen(s){
  // 标题长度：CJK×1，其余（英文/数字/假名）×0.5，向上取整
  var cjk=0;
  for(var i=0;i<s.length;i++){
    var c=s.charCodeAt(i);
    if((c>=0x4e00&&c<=0x9fff)||(c>=0x3000&&c<=0x303f)||(c>=0xff00&&c<=0xffef))cjk++;
  }
  return Math.ceil(cjk+(s.length-cjk)*0.5);
}
function xhsContentLen(s){
  // 正文字数：所有字符×1，换行符不计
  var newlines=0;
  for(var i=0;i<s.length;i++){if(s.charCodeAt(i)===10)newlines++;}
  return s.length-newlines;
}
function updateTitleCount(){
  var el=document.getElementById('titleInput'),c=document.getElementById('titleCount');
  if(!el||!c)return;
  var n=xhsTitleLen(el.value);
  c.textContent=n+'/20';
  c.style.color=n>20?'var(--red)':'var(--text3)';
}
function updateRewrittenTitleCount(){
  var el=document.querySelector('[name=rewritten_title]'),c=document.getElementById('rewrittenTitleCount');
  if(!el||!c)return;
  var n=xhsTitleLen(el.value);
  c.textContent=n+'/20';
  c.style.color=n>20?'var(--red)':'var(--text3)';
}
function updateContentCount(){
  var ta=document.getElementById('contentHidden'),c=document.getElementById('contentCount');
  if(!ta||!c)return;
  var n=xhsContentLen(ta.value);
  c.textContent=n+'/1000';
  c.style.color=n>1000?'var(--red)':'var(--text3)';
}
function updateRewrittenCount(){
  var ta=document.getElementById('rewrittenHidden'),c=document.getElementById('rewrittenCount');
  if(!ta||!c)return;
  var n=xhsContentLen(ta.value);
  c.textContent=n+'/1000';
  c.style.color=n>1000?'var(--red)':'var(--text3)';
}
updateTitleCount();
updateRewrittenTitleCount();
updateContentCount();

// ── Metrics trend chart ──────────────────────────────────────────────
{% if news.publish_xhs %}
(function(){
  var trendSection=document.getElementById('trendSection');
  var chartCanvas=document.getElementById('metricsChart');
  var trendEmpty=document.getElementById('trendEmpty');
  var _chart=null, _snapshots=[], _currentMetric='views';

  var METRIC_LABELS={'views':'浏览','likes':'点赞','saves':'收藏','click_rate':'点击率'};

  function fmtTime(t){return t?t.slice(5,16):t}  // "2026-05-23 14:00" → "05-23 14:00"

  function buildDataset(metric){
    return _snapshots.map(s=>metric==='click_rate'?+(s[metric]*100).toFixed(2):s[metric]);
  }

  function initChart(metric){
    if(_chart)_chart.destroy();
    _chart=new Chart(chartCanvas,{
      type:'line',
      data:{
        labels:_snapshots.map(s=>fmtTime(s.collected_at)),
        datasets:[{
          label:METRIC_LABELS[metric],
          data:buildDataset(metric),
          borderColor:'#3b82f6',
          backgroundColor:'rgba(59,130,246,.08)',
          borderWidth:1.5,
          pointRadius:0,
          pointHoverRadius:3,
          fill:true,
          tension:.3
        }]
      },
      options:{
        responsive:true,
        maintainAspectRatio:false,
        plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false,
          callbacks:{label:ctx=>METRIC_LABELS[metric]+': '+ctx.raw+(metric==='click_rate'?'%':'')}}},
        scales:{
          x:{ticks:{font:{size:9},maxTicksLimit:8,maxRotation:0},grid:{display:false}},
          y:{ticks:{font:{size:9}},beginAtZero:true}
        }
      }
    });
  }

  function switchMetric(metric){
    _currentMetric=metric;
    document.querySelectorAll('.trend-btn').forEach(b=>{
      b.classList.toggle('active',b.dataset.metric===metric);
    });
    if(_chart){
      _chart.data.datasets[0].data=buildDataset(metric);
      _chart.data.datasets[0].label=METRIC_LABELS[metric];
      _chart.update();
    }
  }

  document.querySelectorAll('.trend-btn').forEach(b=>{
    b.addEventListener('click',function(){switchMetric(this.dataset.metric)});
  });

  function loadChart(){
    var s=document.createElement('script');
    s.src='https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
    s.onload=function(){
      fetch('/api/metrics-history/{{news.key}}').then(r=>r.json()).then(d=>{
        if(!d.count){
          trendSection.style.display='';
          chartCanvas.style.display='none';
          trendEmpty.style.display='';
          return;
        }
        _snapshots=d.snapshots;
        trendSection.style.display='';
        initChart(_currentMetric);
      });
    };
    document.head.appendChild(s);
  }
  loadChart();
})();
{% endif %}

function autoGrow(el){el.style.height='auto';el.style.height=(el.scrollHeight+2)+'px'}
function bindAutoResize(){
  document.querySelectorAll('.auto-resize:not([data-bound])').forEach(function(ta){
    ta.setAttribute('data-bound','1');
    ta.addEventListener('input',function(){autoGrow(this)});
    autoGrow(ta);
  });
}
bindAutoResize();
// 每次accordion展开时重新绑定，并强制对已绑定元素重跑 autoGrow
document.querySelectorAll('.accordion-hdr').forEach(function(hdr){
  hdr.addEventListener('click',function(){
    var section=this.closest('.accordion-section')||this.parentElement;
    setTimeout(function(){
      bindAutoResize();
      // 已绑定的元素在隐藏时 autoGrow 算出 0，展开后需要强制重算
      (section||document).querySelectorAll('.auto-resize').forEach(function(ta){
        autoGrow(ta);
      });
    },60);
  });
});

document.getElementById('saveBtn').addEventListener('click',async()=>{
  // 若 Editor.js 激活，先序列化内容到隐藏 textarea
  if(_ejsEditor){
    try{const out=await _ejsEditor.save();document.getElementById('contentHidden').value=_ejsBlocksToText(out.blocks||[]);}catch(e){}
  }
  const data={};
  ['title','summary','content','comment','video_caption','gallery_url','image_url'].forEach(k=>{var el=document.querySelector('[name='+k+']');if(el)data[k]=el.value});
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
  {% if news.gallery_video %}galleryImages.push({path:'{{news.gallery_video}}',sel:true});{% endif %}
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
  var gInput=document.querySelector('[name=gallery_url]');
  var gUrl=(gInput&&gInput.value||'').trim();
  var r=await fetch('/api/gallery-download/'+key,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_url:gUrl})});
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
  // 去重：同路径保留第一个（sel=true 先入为主）
  var seen=new Set(); galleryImages=galleryImages.filter(function(p){if(seen.has(p.path))return false;seen.add(p.path);return true});
  var grid=document.getElementById('galleryGrid');
  grid.innerHTML=galleryImages.map(function(p,i){
    var isVideo=p.path.endsWith('.mp4');
    var media=isVideo?`<video src="/local-image?path=${encodeURIComponent(p.path)}" style="width:100%;height:120px;object-fit:cover;border-radius:6px"></video>`:`<img src="/local-image?path=${encodeURIComponent(p.path)}" style="width:100%;height:120px;object-fit:cover;border-radius:6px">`;
    return `<div style="position:relative;cursor:pointer" onclick="toggleGalleryImg(${i})">
    ${media}
    ${isVideo?'<span style="position:absolute;top:4px;left:4px;background:#333;color:#fff;font-size:9px;padding:1px 4px;border-radius:3px">🎬</span>':''}
    <input type="checkbox" ${p.sel?'checked':''} style="position:absolute;top:4px;right:4px;pointer-events:none">
  </div>`}).join('');
  document.getElementById('galleryModal').classList.add('active');
}
function toggleGalleryImg(i){galleryImages[i].sel=!galleryImages[i].sel;showGalleryModal()}
function selectAllGallery(val){galleryImages.forEach(function(p){p.sel=val});showGalleryModal()}
async function saveGallery(){
  var all=galleryImages.filter(function(p){return p.sel}).map(function(p){return p.path});
  var imgs=all.filter(function(p){return!p.endsWith('.mp4')});
  var vids=all.filter(function(p){return p.endsWith('.mp4')});
  var vidCbs=document.querySelectorAll('#publishImgStrip input[type=checkbox][data-path]'),vidCb=null;
  vidCbs.forEach(function(c){if(c.dataset.path.endsWith('.mp4'))vidCb=c});
  if(vidCb&&!vidCb.checked)vids=[];
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_images:imgs, gallery_video:vids[0]||''})});
  closeGalleryModal();location.reload();
}
async function saveAndUpload(){
  var all=galleryImages.filter(function(p){return p.sel}).map(function(p){return p.path});
  var imgs=all.filter(function(p){return!p.endsWith('.mp4')});
  var vids=all.filter(function(p){return p.endsWith('.mp4')});
  var vidCbs=document.querySelectorAll('#publishImgStrip input[type=checkbox][data-path]'),vidCb=null;
  vidCbs.forEach(function(c){if(c.dataset.path.endsWith('.mp4'))vidCb=c});
  if(vidCb&&!vidCb.checked)vids=[];
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_images:imgs, gallery_video:vids[0]||''})});
  await fetch('/api/gallery-upload/'+key,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({selected:imgs})});
  alert('已上传 '+imgs.length+' 张');closeGalleryModal();location.reload();
}
async function clearGalleryVideo(){await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({gallery_video:''})});location.reload()}
function closeGalleryModal(){document.getElementById('galleryModal').classList.remove('active')}
async function togglePublishImg(el){var cb=el.querySelector('input[type=checkbox]');cb.checked=!cb.checked;el.style.opacity=cb.checked?'1':'0.4';await savePublishImages()}
async function savePublishImages(){
  var paths=[];var vidPath='';document.querySelectorAll('#publishImgStrip input[type=checkbox][data-path]').forEach(function(cb){
    if(cb.checked){
      if(cb.dataset.path.endsWith('.mp4'))vidPath=cb.dataset.path;else paths.push(cb.dataset.path);
    }
  });
  await fetch('/api/news/'+key,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_images:paths, publish_video:vidPath})});
  var t=document.getElementById('toast');t.textContent='发布图已保存 ('+(paths.length+(vidPath?1:0))+'项)';t.style.display='block';setTimeout(function(){t.style.display='none';t.textContent='已保存'},1500);
}
(function initPublishCheckboxes(){
  {% if news.publish_images %}var pubSet=new Set({{news.publish_images|tojson}});{% else %}var pubSet=new Set();{% endif %}
  {% if news.publish_video %}pubSet.add('{{news.publish_video}}');{% endif %}
  document.querySelectorAll('#publishImgStrip input[type=checkbox]').forEach(function(cb){
    if(pubSet.has(cb.dataset.path)){cb.checked=true}else{cb.parentElement.style.opacity='0.4'}
  });
})();
// Image hover zoom overlay
(function(){
  const ov=document.createElement('div');ov.id='imgZoom';ov.innerHTML='<img>';
  document.body.appendChild(ov);
  const img=ov.querySelector('img');
  document.querySelectorAll('.img-strip .img-item img, #galleryGrid img').forEach(el=>{
    el.addEventListener('mouseenter',e=>{
      img.src=e.target.src;ov.style.display='block';
    });
    el.addEventListener('mouseleave',()=>ov.style.display='none');
  });
})();
async function toggleScore(el){
  document.getElementById('overrideDim').textContent=el.dataset.dim;
  document.getElementById('overrideVal').value=el.dataset.val;
  document.getElementById('overrideReason').textContent=el.querySelector('.reason-tip')?.textContent||'';
  document.getElementById('overrideNote').value='';
  document.getElementById('overrideModal').classList.add('active');
}
async function submitOverride(){
  let dim=document.getElementById('overrideDim').textContent;
  let val=parseFloat(document.getElementById('overrideVal').value);
  let note=document.getElementById('overrideNote').value;
  let r=await fetch('/api/score-dim/'+key+'/'+encodeURIComponent(dim),{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({human_value:val,override_note:note})});
  if(r.ok){location.reload()}else{alert('纠正失败: '+(await r.json()).error)}
}

// ── Editor.js 富文本编辑器 ────────────────────────────────────
const _allImgs = {% if news.gallery_images %}{{news.gallery_images|tojson}}{% else %}[]{% endif %};
let _ejsEditor = null;
let _ejsImgCounter = 0;
let _pickerCb = null;

// ── Editor.js 初始化 ──────────────────────────────────────────
function _textToEjsBlocks(text, imgs){
  const blocks=[]; let imgIdx=0, last=0;
  const re=/【(?:图片|推文)\d+：[^】]*】/g; let m;
  function _addText(t){
    if(!t.trim()) return;
    for(const line of t.split('\n')){
      const s=line.trim();
      if(!s) continue;
      if(s.startsWith('### ')) blocks.push({type:'header',data:{text:s.slice(4).trim(),level:3}});
      else if(s.startsWith('## ')) blocks.push({type:'header',data:{text:s.slice(3).trim(),level:2}});
      else if(s.startsWith('> ')) blocks.push({type:'quote',data:{text:s.slice(2).trim(),caption:'',alignment:'left'}});
      else blocks.push({type:'paragraph',data:{text:s}});
    }
  }
  while((m=re.exec(text))!==null){
    _addText(text.slice(last,m.index));
    // 解析 caption 内容：优先从路径标记恢复真实路径，否则按顺序索引
    const inner=m[0].replace(/^【(?:图片|推文)\d+：/,'').replace(/】$/,'');
    let paths=[];
    if(inner.startsWith('/')){
      // 新格式：内容是绝对路径（可能多张，以 | 分隔）
      paths=inner.split('|').filter(p=>p.startsWith('/'));
    } else {
      // 旧格式：按顺序索引 _allImgs
      const p=imgs[imgIdx]||''; if(p){paths=[p];imgIdx++;}
    }
    blocks.push({type:'galleryImage',data:{paths,caption:m[0]}});
    last=m.index+m[0].length;
  }
  _addText(text.slice(last));
  if(!blocks.length) blocks.push({type:'paragraph',data:{text:text}});
  return blocks;
}

function _ejsBlocksToText(blocks){
  const parts=[]; let imgN=1;
  for(const b of blocks){
    if(b.type==='paragraph'&&b.data.text) parts.push(b.data.text);
    else if(b.type==='header'&&b.data.text){
      parts.push((b.data.level===3?'### ':'## ')+b.data.text);
	    } else if(b.type==='quote'&&b.data.text){
	      parts.push('> '+b.data.text);
    } else if(b.type==='galleryImage'){
      const paths=(b.data.paths||[]).filter(p=>p);
      if(!paths.length){imgN++;continue;}  // 图片已全部删除，跳过此块
      // 将真实路径编码进 caption，加载时可精确还原（以 / 开头判断是路径还是描述）
      parts.push(`【图片${imgN}：${paths.join('|')}】`); imgN++;
    } else if(b.type==='image'){
      // 兼容旧 ImageTool blocks
      const cap=b.data.caption||(b.data.file?.url?`【图片${imgN}：图片${imgN}】`:'');
      if(cap){parts.push(cap);imgN++;}
    }
  }
  return parts.filter(s=>s&&s.trim()).join('\n\n');
}

// ── 自定义图片块 ──────────────────────────────────────────────
class GalleryImageBlock {
  static get toolbox(){return{title:'图片',icon:'<svg xmlns="http://www.w3.org/2000/svg" width="17" height="15" viewBox="0 0 336 276"><path d="M291 150V79c0-19-15-34-34-34H79c-19 0-34 15-34 34v42l67-44 81 72 56-29 42 30zm0 52l-43-30-56 30-81-72-66 44v30c0 19 15 34 34 34h178c17 0 31-13 34-29zM79 0h178c44 0 79 35 79 79v118c0 44-35 79-79 79H79c-44 0-79-35-79-79V79C0 35 35 0 79 0z"/></svg>'};}
  static get isReadOnlySupported(){return true;}

  constructor({data,api}){
    this.api=api;
    this.data={paths:data.paths||[],caption:data.caption||''};
    this._el=null;
  }

  render(){
    const wrap=document.createElement('div');
    wrap.className='gb-wrap';
    wrap.style.cssText='border:1px solid #e0e0e0;border-radius:8px;overflow:hidden;background:#fafafa;margin:2px 0';
    this._el=wrap;
    this._rebuild();
    return wrap;
  }

  _rebuild(){
    const wrap=this._el; if(!wrap) return;
    wrap.innerHTML='';
    const paths=this.data.paths.filter(p=>p);
    this.data.paths=paths;  // 清理空路径
    if(paths.length){
      // 图片展示区
      const row=document.createElement('div');
      row.style.cssText=`display:flex;gap:4px;padding:6px;background:#f0f0f0;justify-content:center`;
      paths.forEach((p,idx)=>{
        const cell=document.createElement('div');
        cell.style.cssText=`position:relative;flex:${paths.length===1?'0 0 auto':'1 1 0'};max-width:${paths.length===1?'100%':'50%'}`;
        const img=document.createElement('img');
        img.src='/local-image?path='+encodeURIComponent(p);
        img.style.cssText='width:100%;max-height:320px;object-fit:contain;border-radius:4px;display:block';
        const del=document.createElement('button');
        del.textContent='✕';
        del.title='移除此图';
        del.style.cssText='position:absolute;top:4px;right:4px;background:rgba(0,0,0,.5);color:#fff;border:none;border-radius:50%;width:22px;height:22px;cursor:pointer;font-size:12px;line-height:1;padding:0';
        del.onclick=()=>{
          this.data.paths.splice(idx,1);
          if(this.data.paths.length===0){
            // 全部删除后自动移除此 block
            try{
              const bi=this.api.blocks.getCurrentBlockIndex();
              this.api.blocks.delete(bi);
            }catch(e){this._rebuild();}
          } else {
            this._rebuild();
          }
        };
        cell.appendChild(img);cell.appendChild(del);
        row.appendChild(cell);
      });
      wrap.appendChild(row);
    }
    // 操作栏
    const bar=document.createElement('div');
    bar.style.cssText='display:flex;gap:6px;padding:6px 8px;align-items:center;flex-wrap:wrap;background:#fff';
    const addBtn=document.createElement('button');
    addBtn.textContent=paths.length?'+ 添加图片':'📷 从图库选图';
    addBtn.style.cssText='font-size:11px;padding:3px 10px;border:1px dashed #999;border-radius:12px;background:none;cursor:pointer;color:#555';
    addBtn.onclick=()=>openImgPicker(p=>{this.data.paths.push(p);this._rebuild();});
    bar.appendChild(addBtn);
    // Caption
    const cap=document.createElement('input');
    cap.placeholder='图片说明（图片标记）';
    cap.value=this.data.caption;
    cap.style.cssText='flex:1;font-size:11px;border:none;outline:none;background:transparent;color:#888;min-width:80px';
    cap.oninput=()=>{this.data.caption=cap.value;};
    bar.appendChild(cap);
    wrap.appendChild(bar);
  }

  save(){return{paths:this.data.paths,caption:this.data.caption};}
}

let _ejsRewrittenEditor=null;
function _initEditorJs(){
  if(typeof EditorJS==='undefined') return;

  // Init main content editor (story articles only)
  const el=document.getElementById('editorjs');
  if(el){
    const raw=document.getElementById('contentHidden').value;
    const initBlocks=_textToEjsBlocks(raw,_allImgs);
    _ejsEditor=new EditorJS({
      holder:'editorjs',
      minHeight:100,
      placeholder:'输入正文内容... （用 / 插入小标题或图片块）',
      tools:{
        header:{class:Header,config:{levels:[2,3],defaultLevel:2},inlineToolbar:true},
        quote:{class:Quote,inlineToolbar:true,config:{quotePlaceholder:'输入引用内容',captionPlaceholder:'出处（可选）'}},
        galleryImage:{class:GalleryImageBlock}
      },
      data:{blocks:initBlocks},
      onChange:async()=>{
        try{
          const out=await _ejsEditor.save();
          document.getElementById('contentHidden').value=_ejsBlocksToText(out.blocks||[]);
          updateContentCount();
        }catch(e){}
      }
    });
  }

  // Init rewritten content Editor.js (always)
  const rewEl=document.getElementById('editorjs-rewritten');
  if(rewEl){
    const rewRaw=document.getElementById('rewrittenHidden').value;
    const rewBlocks=_textToEjsBlocks(rewRaw,_allImgs);
    _ejsRewrittenEditor=new EditorJS({
      holder:'editorjs-rewritten',
      minHeight:80,
      placeholder:'改写内容...',
      tools:{
        header:{class:Header,config:{levels:[2,3],defaultLevel:2},inlineToolbar:true},
        quote:{class:Quote,inlineToolbar:true,config:{quotePlaceholder:'输入引用内容',captionPlaceholder:'出处（可选）'}},
        galleryImage:{class:GalleryImageBlock}
      },
      data:{blocks:rewBlocks},
      onChange:async()=>{
        try{
          const out=await _ejsRewrittenEditor.save();
          document.getElementById('rewrittenHidden').value=_ejsBlocksToText(out.blocks||[]);
          autoSaveField('rewritten_content', document.getElementById('rewrittenHidden').value);
          updateRewrittenCount();
        }catch(e){}
      }
    });
    updateRewrittenCount();
  }
}

let _imgPickerCb=null;
// cb 有值时：选完后调 cb(path)；无值时：插入到 Editor.js 当前光标位置
function openImgPicker(cb){
  _imgPickerCb=cb||null;
  const grid=document.getElementById('imgPickerGrid');
  grid.innerHTML='';
  _allImgs.forEach(p=>{
    const d=document.createElement('div');
    d.style.cssText='cursor:pointer;border:2px solid transparent;border-radius:6px;overflow:hidden;flex-shrink:0';
    d.onmouseenter=()=>d.style.borderColor='#7c3aed';
    d.onmouseleave=()=>d.style.borderColor='transparent';
    d.onclick=async()=>{
      closeImgPicker();
      if(_imgPickerCb){_imgPickerCb(p);return;}
      if(!_ejsEditor) return;
      _ejsImgCounter++;
      const url='/local-image?path='+encodeURIComponent(p);
      const cap=`【图片${_ejsImgCounter}：${p.split('/').pop()}】`;
      const curIdx=_ejsEditor.blocks.getCurrentBlockIndex();
      _ejsEditor.blocks.insert('galleryImage',{paths:[p],caption:cap},{},curIdx+1,true);
    };
    const img=document.createElement('img');
    img.src='/local-image?path='+encodeURIComponent(p);
    img.style.cssText='width:88px;height:88px;object-fit:cover;display:block';
    img.title=p.split('/').pop();
    d.appendChild(img);grid.appendChild(d);
  });
  document.getElementById('imgPicker').style.display='block';
}
function closeImgPicker(){document.getElementById('imgPicker').style.display='none'}

async function openStoryPreview(){
  const title='{{news.title|e}}';
  const intro=document.querySelector('[name=summary]')?.value||'';
  const outro=document.querySelector('[name=comment]')?.value||'';
  let html=`<h2 style="font-size:17px;font-weight:700;line-height:1.5;margin:0 0 14px;color:#111">${esc(title)}</h2>`;
  if(intro) html+=`<p style="font-size:13px;line-height:1.8;color:#777;margin:0 0 18px;padding-bottom:14px;border-bottom:1px solid #eee">${esc(intro).replace(/\n/g,'<br>')}</p>`;
  let blocks=[];
  if(_ejsEditor){try{const out=await _ejsEditor.save();blocks=out.blocks||[];}catch(e){}}
  for(const b of blocks){
    if(b.type==='paragraph'&&b.data.text)
      html+=`<p style="font-size:14px;line-height:1.9;color:#222;margin:0 0 12px">${b.data.text}</p>`;
    else if(b.type==='header'&&b.data.text){
      const tag=b.data.level===3?'h3':'h2';
      html+=`<${tag} style="font-size:${b.data.level===3?'14':'16'}px;font-weight:700;margin:16px 0 6px;color:#111">${esc(b.data.text)}</${tag}>`;
    } else if(b.type==='quote'&&b.data.text){
      html+=`<blockquote style="font-size:14px;line-height:1.8;color:#555;border-left:3px solid #ff6b35;padding-left:14px;margin:12px 0">${esc(b.data.text)}</blockquote>`;
    } else if(b.type==='galleryImage'){
      // 支持多图横排
      const paths=(b.data.paths||[]).filter(p=>p);
      if(!paths.length) continue;
      const cols=paths.length===1?'1fr':paths.map(()=>'1fr').join(' ');
      const imgs=paths.map(p=>`<img src="/local-image?path=${encodeURIComponent(p)}" style="width:100%;border-radius:8px;display:block;object-fit:cover">`).join('');
      html+=`<div style="display:grid;grid-template-columns:${cols};gap:4px;margin:12px 0">${imgs}</div>`;
      if(b.data.caption&&!b.data.caption.startsWith('/'))
        html+=`<p style="font-size:11px;color:#aaa;margin:2px 0 12px;text-align:center">${esc(b.data.caption)}</p>`;
    } else if(b.type==='image'&&b.data.file?.url)
      html+=`<div style="margin:12px 0"><img src="${b.data.file.url}" style="width:100%;border-radius:10px;display:block"></div>`;
  }
  if(outro) html+=`<p style="font-size:13px;line-height:1.8;color:#777;margin:18px 0 0;padding-top:14px;border-top:1px solid #eee">${esc(outro).replace(/\n/g,'<br>')}</p>`;
  document.getElementById('storyPreviewBody').innerHTML=html;
  document.getElementById('storyPreviewModal').style.display='block';
}

// Init on load (load Editor.js from CDN first)
(function loadEditorJs(){
  if(!document.getElementById('editorjs') && !document.getElementById('editorjs-rewritten')) return;
  function loadScript(src,cb){const s=document.createElement('script');s.src=src;s.onload=cb;document.head.appendChild(s);}
  loadScript('https://cdn.jsdelivr.net/npm/@editorjs/editorjs@2.29.1/dist/editorjs.umd.min.js',()=>{
    loadScript('https://cdn.jsdelivr.net/npm/@editorjs/header@2.8.1/dist/header.umd.min.js',()=>{
      loadScript('https://cdn.jsdelivr.net/npm/@editorjs/quote@2.6.0/dist/quote.umd.min.js',()=>{
        loadScript('https://cdn.jsdelivr.net/npm/@editorjs/image@2.10.3/dist/image.umd.js',()=>{
          _initEditorJs();
        });
      });
    });
  });
})();
</script>
<div class="modal" id="overrideModal"><div class="modal-card" style="max-width:360px">
  <h3 style="margin-bottom:8px">纠正 <span id="overrideDim"></span></h3>
  <div style="font-size:12px;color:#888;margin-bottom:12px;padding:6px 10px;background:#f8f8f8;border-radius:6px">LLM 原始评分理由：<span id="overrideReason" style="color:#666"></span></div>
  <div class="field-row" style="margin-bottom:10px"><label>分值</label><div class="value"><select id="overrideVal" style="padding:6px 10px;border:1px solid #ddd;border-radius:6px;font-size:14px;width:100%"><option value="0">0</option><option value="0.5">0.5</option><option value="1">1</option></select></div></div>
  <div class="field-row"><label>理由</label><div class="value"><input id="overrideNote" class="url-input" placeholder="纠正理由（可选）" style="width:100%;padding:8px;font-size:13px"></div></div>
  <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:14px">
    <button class="btn btn-gray" onclick="document.getElementById('overrideModal').classList.remove('active')">取消</button>
    <button class="btn btn-red" onclick="submitOverride()">确认</button>
  </div>
</div></div>
</body></html>"""

MANUAL_REVIEW_HTML = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>待人工清单</title>
<style>body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f5f6f8;margin:0;padding:20px;color:#222}
h2{margin:0 0 4px}.sub{color:#888;font-size:12px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}
th,td{padding:8px 10px;font-size:13px;text-align:left;border-bottom:1px solid #eee;vertical-align:top}
th{background:#fafafa;color:#666;font-weight:600;font-size:12px}
.k{font-family:ui-monospace,monospace;color:#7c3aed}.r{color:#b45309;font-size:12px}
.btn{border:1px solid #ddd;background:#fff;border-radius:6px;padding:3px 8px;font-size:12px;cursor:pointer}
.btn:hover{background:#f0f0f0}.empty{color:#999;padding:30px;text-align:center}</style></head><body>
<h2>🚩 待人工清单</h2><div class="sub">成人产业/非赛道等内容门禁命中 → 需人工判断是否写、怎么写（不阻断流程）</div>
<table><thead><tr><th style="width:90px">key</th><th>标题</th><th style="width:60px">体裁</th><th>命中原因</th><th style="width:90px">入库</th><th style="width:70px">操作</th></tr></thead>
<tbody id="tb"><tr><td colspan="8" class="empty">加载中…</td></tr></tbody></table>
<script>
async function load(){const r=await fetch('/api/manual-review');const d=await r.json();const tb=document.getElementById('tb');
 if(!d.rows.length){tb.innerHTML='<tr><td colspan="6" class="empty">暂无待人工素材 🎉</td></tr>';return;}
 tb.innerHTML=d.rows.map(x=>`<tr><td class="k">${x.key.slice(0,12)}</td><td>${(x.title||'').slice(0,60)}</td><td>${x.format||''}</td>
 <td class="r">${x.manual_reason||''}</td><td>${(x.created_at||'').slice(0,10)}</td>
 <td><button class="btn" onclick="clearOne('${x.key}')">已处理</button></td></tr>`).join('');}
async function clearOne(k){await fetch('/api/manual-review/'+k+'/clear',{method:'POST'});load();}
load();
</script></body></html>"""


@app.route('/api/manual-review', methods=['GET'])
def api_manual_review():
    rows = [r for r in query_news(status="active", limit=500) if int(r.get("manual_review") or 0) == 1]
    return jsonify({"rows": rows, "total": len(rows)})


@app.route('/api/manual-review/<key>/clear', methods=['POST'])
def api_manual_review_clear(key):
    update_news(key, {"manual_review": 0, "manual_reason": ""})
    return jsonify({"ok": True})


@app.route('/manual-review')
def manual_review_page():
    return render_template_string(MANUAL_REVIEW_HTML)


@app.route('/api/split-preview')
def api_split_preview():
    """拆篇预览：仅列出 cluster 体量超阈值的候选，分页。"""
    from services import split_write as _sp
    from services import split_articles as _sa
    day = (request.args.get('day') or '').strip()
    try:
        th = int(request.args.get('threshold') or _sp.DEFAULT_THRESHOLD)
    except Exception:
        th = _sp.DEFAULT_THRESHOLD
    try:
        page = max(1, int(request.args.get('page') or 1))
    except Exception:
        page = 1
    try:
        page_size = min(200, max(1, int(request.args.get('page_size') or 50)))
    except Exception:
        page_size = 50
    rows = query_news(status="active", limit=1000)
    if day:
        rows = [r for r in rows if (r.get('created_at') or '').startswith(day)]
    # 同 cluster 只保留「主 key」一行（与写稿选材一致：簇内 title_score 最高者代表整簇，其余成员吸收）
    reps, used = [], set()
    for r in sorted(rows, key=lambda x: -(x.get('title_score') or 0)):
        cl = {k.strip() for k in (r.get('cluster_keys') or '').split(',') if k.strip()}
        if not cl:
            continue
        if (cl | {r['key']}) & used:
            continue
        reps.append(r)
        used |= cl | {r['key']}
    out = []
    for c in reps:
        L = _sp.cluster_text_len(c)
        if L <= th:
            continue
        out.append({"key": c['key'], "full_key": c['key'], "title": c.get('title') or '',
                    "format": c.get('format') or '', "len": L})
    total = len(out)
    start = (page - 1) * page_size
    page_rows = out[start:start + page_size]
    counts = _sa.counts_for([r["full_key"] for r in page_rows])
    for r in page_rows:
        r["split_count"] = counts.get(r["full_key"], 0)
    return jsonify({"day": day, "rows": page_rows, "total": total,
                    "page": page, "page_size": page_size, "threshold": th})


@app.route('/api/split-write/<key>', methods=['POST'])
def api_split_write(key):
    """后台触发「拆篇写入」（write-full --split-large --key）。"""
    import datetime
    import os
    import sqlite3
    import subprocess
    import sys
    from services import paths as _p
    conn = sqlite3.connect(_p.sqlite_path())
    try:
        r = conn.execute("SELECT key FROM news WHERE key LIKE ? || '%'", (key[:40],)).fetchone()
        k = r[0] if r else None
    finally:
        conn.close()
    if not k:
        return jsonify({"ok": False, "msg": "key 不存在"})
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    logp = os.path.join(repo, "logs", "split_write.log")
    try:
        with open(logp, "a") as lf:
            lf.write(f"\n=== {datetime.datetime.now()} split-write {k} ===\n")
            lf.flush()
            subprocess.Popen([sys.executable, "-m", "cli", "write-full", "--split-large", "--key", key],
                             cwd=repo, stdout=lf, stderr=lf, start_new_session=True)
    except Exception as e:
        return jsonify({"ok": False, "msg": str(e)})
    return jsonify({"ok": True})


@app.route('/api/split-articles/<main_key>', methods=['GET'])
def api_split_articles(main_key):
    from services import split_articles as _sa
    rows = _sa.list_by_main(main_key)
    return jsonify({"rows": rows, "total": len(rows)})


WRITE_FAILURES_HTML = r"""<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>写稿失败队列</title>
<style>body{font-family:-apple-system,"PingFang SC",sans-serif;background:#f5f6f8;margin:0;padding:20px;color:#222}
h2{margin:0 0 4px}.sub{color:#888;font-size:12px;margin-bottom:14px}
table{width:100%;border-collapse:collapse;background:#fff;border-radius:10px;overflow:hidden;box-shadow:0 1px 3px rgba(0,0,0,.06)}
th,td{padding:8px 10px;font-size:13px;text-align:left;border-bottom:1px solid #eee;vertical-align:top}
th{background:#fafafa;color:#666;font-weight:600;font-size:12px}
.k{font-family:ui-monospace,monospace;color:#7c3aed}
.badge{display:inline-block;padding:1px 6px;border-radius:10px;font-size:11px}
.pending{background:#fff7e6;color:#b45309}.needs_manual{background:#fee2e2;color:#b91c1c}.resolved{background:#dcfce7;color:#15803d}
pre{background:#1e1e2e;color:#e0e0e0;padding:10px;border-radius:6px;font-size:11px;max-height:260px;overflow:auto;white-space:pre-wrap}
.btn{border:1px solid #ddd;background:#fff;border-radius:6px;padding:3px 8px;font-size:12px;cursor:pointer}
.btn:hover{background:#f0f0f0}.empty{color:#999;padding:30px;text-align:center}.r{color:#b45309;font-size:12px}</style></head><body>
<h2>❌ 写稿失败队列</h2><div class="sub">写稿失败（含 LLM 抖动）→ 1 小时后自动重试 → 再失败转「待人工」；成功则标记 resolved（保留记录）。</div>
<div id="stats" style="margin-bottom:14px"></div>
<table><thead><tr><th style="width:92px">key</th><th style="width:140px">时间</th><th style="width:180px">标题</th><th>原因</th><th style="width:60px">状态</th><th style="width:44px">重试</th><th style="width:120px">下次重试</th><th style="width:78px">操作</th></tr></thead>
<tbody id="tb"><tr><td colspan="8" class="empty">加载中…</td></tr></tbody></table>
<script>
function esc(x){return (x==null?'':(''+x)).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]))}
async function load(){const d=await(await fetch('/api/write-failures')).json();const tb=document.getElementById('tb');
 if(!d.rows.length){tb.innerHTML='<tr><td colspan="8" class="empty">队列为空 🎉</td></tr>';return;}
 tb.innerHTML=d.rows.map(x=>`<tr><td><a class="k" href="/detail/${x.key}" target="_blank" title="${x.key}">${x.key.slice(0,12)}</a></td>
  <td style="font-size:11px;color:#555">${esc(x.created_at||x.updated_at||'')}</td>
  <td style="font-size:12px">${esc((x.title||'—').slice(0,40))}</td>
  <td><span style="color:#7c3aed;font-size:11px">[${esc(x.category||'')}·${esc(x.level||'error')}]</span> <span class="r" style="font-size:12px">${esc(x.reason||'')}</span>
   ${x.traceback?`<details><summary style="cursor:pointer;font-size:11px;color:#7c3aed">报错堆栈</summary><pre>${esc(x.traceback)}</pre></details>`:''}</td>
  <td><span class="badge ${x.status}">${x.status==='needs_manual'?'待人工':(x.status==='resolved'?'已了结':'待重试')}</span>${x.note?`<div style="font-size:10px;color:#888">${esc(x.note)}</div>`:''}</td>
  <td>${x.retry_count||0}</td><td>${esc(x.next_retry_at||'—')}</td>
  <td>${x.status==='resolved'?'<span style=\"color:#15803d;font-size:11px\">✓</span>':`<button class="btn" onclick="retryNow('${x.key}')">立即重试</button> <button class="btn" onclick="dismiss('${x.key}')">忽略</button>`}</td></tr>`).join('');}
async function loadStats(){try{const d=await(await fetch('/api/write-failures/stats')).json();
 const mx=Math.max(1,...d.by_category.map(x=>x.n));
 const bars=d.by_category.map(x=>`<div style="display:flex;align-items:center;gap:8px;margin:3px 0"><span style="width:130px;font-size:12px">${esc(x.label)}</span><span style="height:11px;background:#7c3aed;border-radius:3px;width:${Math.round(x.n/mx*200)}px"></span><b style="font-size:12px">${x.n}</b></div>`).join('');
 document.getElementById('stats').innerHTML=`<div style="background:#fff;padding:12px 14px;border-radius:10px;box-shadow:0 1px 3px rgba(0,0,0,.06)"><b>近 ${d.days} 天失败主因</b>（共 ${d.total} 篇）<div style="margin-top:8px">${bars||'<span style="color:#999;font-size:12px">暂无数据</span>'}</div></div>`;}catch(e){}}
async function retryNow(k){await fetch('/api/write-failures/'+k+'/retry',{method:'POST'});load();}
async function dismiss(k){if(confirm('确认移除该失败记录（不再重试）？')){await fetch('/api/write-failures/'+k+'/dismiss',{method:'POST'});load();}}
loadStats();
load();
</script></body></html>"""


@app.route('/api/write-failures', methods=['GET'])
def api_write_failures():
    from services import write_failures as _wf
    rows = _wf.list_all()
    return jsonify({"rows": rows, "total": len(rows)})


@app.route('/api/write-failures/stats', methods=['GET'])
def api_write_failures_stats():
    from services import write_failures as _wf
    days = int(request.args.get('days', 30))
    return jsonify(_wf.stats(days))


@app.route('/api/write-failures/<key>/retry', methods=['POST'])
def api_write_failures_retry(key):
    from services import write_failures as _wf
    _wf.set_due_now(key)                 # 置为立即到期，由 write-retry（15 分钟）处理
    return jsonify({"ok": True})


@app.route('/api/write-failures/<key>/dismiss', methods=['POST'])
def api_write_failures_dismiss(key):
    from services import write_failures as _wf
    _wf.resolve(key)
    return jsonify({"ok": True})


@app.route('/write-failures')
def write_failures_page():
    return render_template_string(WRITE_FAILURES_HTML)


@app.route('/api/lexicon', methods=['GET'])
def api_lexicon_list():
    from services import lexicon as _lx
    try:
        page = max(1, int(request.args.get('page') or 1))
    except Exception:
        page = 1
    try:
        ps = min(200, max(1, int(request.args.get('page_size') or 50)))
    except Exception:
        ps = 50
    rows = _lx.all(type=request.args.get('type') or None,
                   status=request.args.get('status') or None,
                   q=(request.args.get('q') or '').strip(),
                   sort_by=request.args.get('sort_by') or 'updated_at',
                   sort_dir=request.args.get('sort_dir') or 'desc')
    total = len(rows)
    start = (page - 1) * ps
    return jsonify({"rows": rows[start:start + ps], "total": total, "page": page, "page_size": ps})


@app.route('/api/lexicon', methods=['POST'])
def api_lexicon_upsert():
    from services import lexicon as _lx
    d = request.get_json(silent=True) or {}
    term = (d.get('term') or '').strip()
    if not term:
        return jsonify({"ok": False, "msg": "term 必填"})
    ok = _lx.add(term, zh=d.get('zh'), type=d.get('type') or 'word',
                 action=d.get('action') or 'translate', aliases=d.get('aliases') or '',
                 status=d.get('status') or 'manual', source='ui', note=d.get('note') or '')
    return jsonify({"ok": ok})


@app.route('/api/lexicon/delete', methods=['POST'])
def api_lexicon_delete():
    from services import lexicon as _lx
    return jsonify({"ok": _lx.delete((request.get_json(silent=True) or {}).get('term', ''))})


@app.route('/api/lexicon/promote', methods=['POST'])
def api_lexicon_promote():
    from services import lexicon as _lx
    return jsonify({"ok": _lx.promote((request.get_json(silent=True) or {}).get('term', ''))})


@app.route('/api/lexicon/revert', methods=['POST'])
def api_lexicon_revert():
    from services import lexicon as _lx
    return jsonify({"ok": _lx.revert((request.get_json(silent=True) or {}).get('term', ''))})


@app.route('/api/lexicon/judge', methods=['POST'])
def api_lexicon_judge():
    from services import lexicon as _lx
    return jsonify({"ok": True, "count": len(_lx.judge(limit=300))})


@app.route('/api/lexicon/export', methods=['POST'])
def api_lexicon_export():
    from services import lexicon as _lx
    return jsonify({"ok": True, "count": _lx.export_config()})


@app.route('/api/token-usage', methods=['GET'])
def api_token_usage():
    from services import token_usage as _tu
    from services import paths as _p
    import datetime as _dt
    from collections import defaultdict as _dd
    days = int(request.args.get('days', 30))
    date = (request.args.get('date') or '').strip() or _dt.datetime.now().strftime("%Y-%m-%d")
    run = (request.args.get('run') or '').strip() or None
    rows = _tu.detail_on(date, run)          # 当天全部明细（id 升序）
    keys = list({r["key"] for r in rows if r.get("key")})
    if keys:
        import sqlite3 as _sq
        conn = _sq.connect(_p.sqlite_path())
        try:
            ph = ",".join("?" for _ in keys)
            tm = {k: (t or "") for k, t in conn.execute(
                f"SELECT key, COALESCE(NULLIF(rewritten_title,''),title) FROM news "
                f"WHERE key IN ({ph})", keys)}
        finally:
            conn.close()
        for r in rows:
            r["title"] = tm.get(r["key"], "")
    # 分组：scope=run 的行作父，其同名 run 的子行作子（子行记录在 run 之前）
    # fetch：子行按 stage 聚合 + 重试摘要；write：子行=逐篇 article
    _RETRY_STAGES = {"score_retry", "regen"}

    def _stage_agg(calls):
        agg = {}
        for c in calls:
            st = (c.get("stage") or "").strip() or "(未标注)"
            a = agg.setdefault(st, {"stage": st, "n": 0, "calls": 0, "prompt_tokens": 0,
                                    "completion_tokens": 0, "total_tokens": 0, "cache_hit_tokens": 0})
            a["n"] += 1
            for f in ("calls", "prompt_tokens", "completion_tokens", "total_tokens", "cache_hit_tokens"):
                a[f] += int(c.get(f) or 0)
        return sorted(agg.values(), key=lambda x: -x["total_tokens"])

    groups, pending = [], _dd(list)
    for r in rows:
        if (r.get("scope") or "") == "run":
            kids = pending.pop(r.get("run") or "", [])
            g = {"row": r, "children": kids}
            if (r.get("run") or "") == "fetch":
                g["stages"] = _stage_agg(kids)
                g["retry"] = {"n": sum(1 for c in kids if (c.get("stage") or "") in _RETRY_STAGES),
                              "total": len(kids),
                              "tokens": sum(int(c.get("total_tokens") or 0) for c in kids
                                            if (c.get("stage") or "") in _RETRY_STAGES)}
            groups.append(g)
        else:
            pending[r.get("run") or ""].append(r)
    for _rn, _kids in pending.items():          # 无父的散条
        if _kids:
            groups.append({"row": None, "children": _kids})
    groups.reverse()                             # 新在前
    return jsonify({"by_run": _tu.summary(days).get("by_run", []), "by_day": _tu.by_day(days, run),
                    "date": date, "groups": groups, "total": len(rows)})


@app.route('/api/topic-cache', methods=['GET'])
def api_topic_cache_list():
    from sqlite_db import list_topic_ids_paged
    return jsonify(list_topic_ids_paged(
        page=request.args.get('page', 1),
        page_size=request.args.get('page_size', 50),
        sort_by=request.args.get('sort_by', 'name'),
        sort_dir=request.args.get('sort_dir', 'asc'),
        q=(request.args.get('q') or '').strip(),
    ))


@app.route('/api/topic-cache', methods=['POST'])
def api_topic_cache_upsert():
    from sqlite_db import upsert_topic_id
    d = request.get_json(silent=True) or {}
    name = (d.get("name") or "").strip().lstrip("#")
    topic_id = (d.get("topic_id") or "").strip()
    link = (d.get("link") or "").strip()
    if not name or not topic_id:
        return jsonify({"ok": False, "msg": "话题名和 topic_id 必填"})
    upsert_topic_id(name, topic_id, link, "manual")
    return jsonify({"ok": True})


@app.route('/api/topic-cache/<path:name>', methods=['DELETE'])
def api_topic_cache_delete(name):
    from sqlite_db import delete_topic_id
    return jsonify({"ok": delete_topic_id(name)})


@app.route('/topic-cache')
def topic_cache_page():
    return render_template_string(TOPIC_CACHE_HTML)


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
            if f.endswith(('.jpg','.jpeg','.png','.webp','.mp4')) and not f.startswith('.') and not f.startswith('cover.'):
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
            except Exception:
                pass
    scores = get_score_dims(key)

    # 解析体裁（现在是单一字符串）
    import re as _re
    news['primary_format'] = news.get('format', 'news')
    _fmt_labels = {'news': '资讯', 'story': '故事体', 'ranking': '盘点', 'comparison': '对比'}
    news['format_label'] = _fmt_labels.get(news['primary_format'], news['primary_format'])

    # story 体裁：构建含行内图片的预览片段
    # 支持新格式【图片N：描述】和旧格式【推文N：描述】
    _IMG_RE = _re.compile(r'【(?:图片|推文)\d+：[^】]*】')
    story_parts = []
    if news['primary_format'] == 'story' and news.get('content'):
        # 所有 gallery_images 均可作为行内图片（文章图 + 推文图）
        all_imgs = news['gallery_images']
        img_idx = 0
        last = 0
        content = news['content']
        for m in _IMG_RE.finditer(content):
            text = content[last:m.start()].strip()
            if text:
                story_parts.append({'t': 'text', 'v': text})
            img_path = all_imgs[img_idx] if img_idx < len(all_imgs) else None
            story_parts.append({'t': 'img', 'v': img_path, 'cap': m.group(0)})
            if img_path:
                img_idx += 1
            last = m.end()
        tail = content[last:].strip()
        if tail:
            story_parts.append({'t': 'text', 'v': tail})

    # 已使用的行内图片路径列表，供 JS 初始化块编辑器
    story_tweet_imgs = [p['v'] for p in story_parts if p['t'] == 'img' and p.get('v')]

    # 关联文章标题
    related_articles = []
    rk_raw = news.get('related_keys', '') or ''
    for rk in rk_raw.split(','):
        rk = rk.strip()
        if rk:
            rr = get_by_key(rk)
            related_articles.append({'key': rk, 'title': (rr.get('title','') or rk)[:40] if rr else rk[:16]})

    # 渠道判定（读 logs/channel.log 最新一条）
    channel_decision = {"channel": "", "reason": ""}
    try:
        cf = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs", "channel.log")
        if os.path.exists(cf):
            for _ln in open(cf, encoding="utf-8"):
                _pp = _ln.rstrip("\n").split("\t")
                if len(_pp) >= 5 and key.startswith(_pp[2]):
                    channel_decision = {"channel": _pp[3], "reason": "\t".join(_pp[4:])}
    except Exception:  # noqa: BLE001
        pass
    return rts(DETAIL_HTML, news=news, scores=scores,
               story_parts=story_parts, story_tweet_imgs=story_tweet_imgs,
               related_articles=related_articles, channel_decision=channel_decision)

@app.route('/api/news')
def api_list():
    s = stats()
    _needs_review = request.args.get('needs_review', '0') == '1'
    rows = query_news(
        date_from=request.args.get('date_from',''),
        date_to=request.args.get('date_to',''),
        category=request.args.get('category',''),
        status=request.args.get('status','active'),
        search=request.args.get('search',''),
        publish_xhs=request.args.get('publish_xhs',''),
        needs_review=_needs_review,
        fmt=request.args.get('fmt',''),
        score_min=request.args.get('score_min',''),
        fetch_by=request.args.get('fetch_by',''),
        preselected=request.args.get('preselected',''),
        grade=request.args.get('grade',''),
        sort_by=request.args.get('sort_by','created_at'),
        sort_dir=request.args.get('sort_dir','DESC'),
        limit=min(int(request.args.get('limit',200)), 500),
        offset=int(request.args.get('offset', 0)),
    )
    # Count filtered rows (without LIMIT) for correct pagination
    filtered_total = len(query_news(
        date_from=request.args.get('date_from',''),
        date_to=request.args.get('date_to',''),
        category=request.args.get('category',''),
        status=request.args.get('status','active'),
        search=request.args.get('search',''),
        publish_xhs=request.args.get('publish_xhs',''),
        needs_review=_needs_review,
        fmt=request.args.get('fmt',''),
        score_min=request.args.get('score_min',''),
        fetch_by=request.args.get('fetch_by',''),
        preselected=request.args.get('preselected',''),
        grade=request.args.get('grade',''),
        sort_by=request.args.get('sort_by','created_at'),
        sort_dir=request.args.get('sort_dir','DESC'),
        limit=10000,
    ))
    return jsonify({"rows": rows, "total": filtered_total, "today": s["today"], "pending": s["pending"], "published": s["published"]})

@app.route('/api/news/<key>')
def api_detail(key):
    news = get_by_key(key)
    if not news: return jsonify({"error": "not found"}), 404
    news['scores'] = get_score_dims(key)
    return jsonify(news)

@app.route('/api/all-tags')
def api_all_tags():
    from sqlite_db import _connect
    from collections import Counter
    with _connect() as db:
        rows = db.execute("SELECT tags FROM news WHERE tags IS NOT NULL AND tags!='' AND status='active'").fetchall()
    c = Counter()
    for r in rows:
        for t in (r['tags'] or '').split(','):
            t = t.strip()
            if t: c[t] += 1
    # 只返回出现 >=2 次的标签，过滤单次使用的人名/杂项
    return jsonify({"tags": sorted([t for t, n in c.items() if n >= 2])})

def _kick_gallery_download(key):
    """标记待发布后，一次性踢一脚后台图集下载（oneway，不等待、不回报）。

    - 已有缓存 / 正在下载 → 跳过，幂等
    - 任何异常都吞掉：图集只是配图，失败不能影响 PUBLISH_XHS 标记本身
    """
    try:
        from web.gallery_downloader import get_status as _gst, trigger_download
        st = (_gst(key) or {}).get('status', '')
        if st in ('running', 'done') or st.startswith('error: 未检测到图集链接'):
            return                     # 有缓存 / 下载中 / 源站本来就没有图集 → 不重复踢
        trigger_download(key)          # daemon thread，立即返回
    except Exception as e:
        try:
            app.logger.warning(f'gallery kick failed {key}: {e}')
        except Exception:
            pass


@app.route('/api/news/<key>', methods=['PUT'])
def api_update(key):
    data = request.get_json()
    update_news(key, data)
    # 待发布选中（publish_xhs=1）→ 立刻踢图集下载，不阻塞响应
    if str(data.get('publish_xhs', '')).strip().lower() in ('1', 'true'):
        _kick_gallery_download(key)
    return jsonify({"ok": True})

@app.route('/api/collect-metrics', methods=['POST'])
def api_collect_metrics_batch():
    """批量回收已发布文章的实发数据"""
    try:
        from scripts.metrics_collector import collect_all
        result = collect_all()
        return jsonify({"ok": True, "collected": result.get("collected", 0)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500



if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False, threaded=True)
