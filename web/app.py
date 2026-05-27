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
        if 'log' in t and t['log']:
            t['log'] = t['log'].replace('\x00','').replace('\x1b','')
        # Don't expose proc object
        r = {k: v for k, v in t.items() if k != 'proc'}
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

# Custom keywords persistence
CUSTOM_KW_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'custom_keywords.json')
def _load_custom_keywords():
    if os.path.exists(CUSTOM_KW_FILE):
        with open(CUSTOM_KW_FILE) as f:
            return json.load(f)
    return []
def _save_custom_keywords(kws):
    os.makedirs(os.path.dirname(CUSTOM_KW_FILE), exist_ok=True)
    with open(CUSTOM_KW_FILE, 'w') as f:
        json.dump(kws, f)

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
    tid = f"regen-title_{key}"
    _tasks[tid] = {'status': 'running', 'log': ''}
    def do_regen_title():
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
            fs = row.get('format_suitability') or '["news"]'
            primary_format = (_j.loads(fs) if isinstance(fs, str) else fs or ['news'])[0]
            # 资讯体直接标记，不让模型猜；故事体用 story_type 细分
            story_type = row.get('story_type') or ('' if primary_format == 'story' else '资讯体')
            current_title = row.get('title') or ''
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
                except: pass
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
            from yahoo_common import translate_title, generate_content_and_comment, evaluate_quality, generate_video_caption, generate_story_article
            import json as _json
            log = []
            is_story = row.get('is_long_form') or 'story' in (row.get('format_suitability') or '')
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
                story = generate_story_article(title_ja, title_zh, content_ja or row.get('content', ''),
                                               article_images=article_imgs)
                if not story:
                    _tasks['regen_'+key] = {'status': 'error: 故事体生成失败', 'log': '故事体生成失败'}
                    return
                new_title = story.get('title', title_zh)
                log.append(f'标题: {new_title[:50]}')
                log.append('评估质量...')
                _tasks['regen_'+key] = {'status': 'running', 'log': '\n'.join(log)}
                quality = evaluate_quality(new_title, story['body'], story.get('outro', ''), title_ja, content_ja)
                updates = {'title': new_title, 'summary': story.get('intro', ''),
                           'content': story['body'], 'comment': story.get('outro', ''),
                           'is_long_form': True,
                           'format_suitability': row.get('format_suitability', '["story"]'),
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
                    updates['format_suitability'] = '["news"]'
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
                except: pass
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
    """代理本地图片文件"""
    path = request.args.get('path', '')
    if not path or not os.path.exists(path):
        return '', 404
    return send_file(path)

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
    <div class="nav-item active" onclick="">
      <span class="ni">📋</span> 文章列表
      <span class="nav-badge g" id="sidebarPending" style="display:none">0</span>
    </div>
  </div>

  <div class="sidebar-divider"></div>

  <div class="sidebar-section">
    <div class="sidebar-label">运营工具</div>
    <div class="nav-item" onclick="toggleFetchDrawer()">
      <span class="ni">🔍</span> 抓取管理
    </div>
    <div class="nav-item" onclick="toggleConfigPanel()">
      <span class="ni">⚙️</span> 策略配置
    </div>
    <div class="nav-item" onclick="toggleTagPanel()">
      <span class="ni">🏷️</span> 标签配置
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
            <th style="width:30px"></th>
            <th style="width:50px">封面</th>
            <th>标题</th>
            <th style="width:70px">体裁</th>
            <th onclick="setSort('title_score')" style="width:64px">评分 ↕</th>
            <th style="width:60px">状态</th>
            <th style="width:50px">发布</th>
            <th style="width:60px">模式</th>
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

function _fmtBadge(fs,cat){
  try{const a=JSON.parse(fs||'[]');const f=a[0]||cat||'';
    if(f==='story')return`<span class="fmt fmt-story">长文</span>`;
    if(f==='news')return`<span class="fmt fmt-news">资讯</span>`;
    if(f==='ranking')return`<span class="fmt fmt-ranking">盘点</span>`;
    if(f)return`<span class="fmt fmt-other">${esc(f)}</span>`;
  }catch(e){}return'<span class="fmt fmt-other">—</span>';
}
async function loadList(){
  const p=new URLSearchParams({sort_by:sortBy,sort_dir:sortDir,limit:pageSize,offset:page*pageSize,
    search:S('search').value,date_from:S('dateFrom').value,date_to:S('dateTo').value,
    fetch_by:S('fetchBy').value,status:S('status').value,publish_xhs:S('publishXhs').value,
    fmt:S('fmtFilter').value,score_min:S('scoreFilter').value});
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
    <td><input type="checkbox" class="rowSel" value="${n.key}" onclick="event.stopPropagation()" onchange="updateArchiveBar()" style="width:14px;height:14px"></td>
    <td>${thumb}</td>
    <td><div class="tc"><div class="tc-body">
      ${n.fetch_by?`<div class="tc-kw">${esc(n.fetch_by)}</div>`:''}
      <a href="/detail/${n.key}" class="link tc-title" onclick="event.stopPropagation()">${esc(n.title||'')}</a>
      <div class="tc-snip">${esc((n.content||'').substring(0,60))}</div>
    </div></div></td>
    <td>${_fmtBadge(n.format_suitability,n.category)}</td>
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
      return`<select onchange="event.stopPropagation();fetch('/api/news/${n.key}',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({publish_mode:this.value})})" style="font-size:10px;padding:1px 3px;border:1px solid var(--border);border-radius:4px;background:${pm==='free'?'#fef2f2':pm==='caption'?'#fefce8':'#f0fdf4'};color:${pm==='free'?'#b91c1c':pm==='caption'?'#a16207':'#15803d'};cursor:pointer">
        <option value="normal" ${pm==='normal'?'selected':''}>默认</option>
        <option value="caption" ${pm==='caption'?'selected':''}>短配文</option>
        <option value="free" ${pm==='free'?'selected':''}>自由</option>
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
    fmt:S('fmtFilter').value,score_min:S('scoreFilter').value});
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
  const isStory=n.is_long_form||(n.format_suitability||'').includes('story');
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
.card-section-title{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--text3);margin-bottom:10px}
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
.cover-img{width:100%;max-height:200px;object-fit:cover;display:block}
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
#editorjs .ce-block__content{max-width:none}
#editorjs .codex-editor__redactor{padding-bottom:20px!important}
#editorjs h2.ce-header{font-size:16px;font-weight:700;margin:16px 0 6px;color:var(--text)}
#editorjs h3.ce-header{font-size:14px;font-weight:600;margin:14px 0 4px;color:var(--text)}
#editorjs .image-tool__image-picture{max-width:100%;border-radius:8px}
</style>
</head>
<body>

<!-- Topbar -->
<div class="topbar">
  <a href="javascript:history.back()">← 返回</a>
  {% if news.fetch_by %}<span class="badge badge-gray" style="flex-shrink:0">{{news.fetch_by}}</span>{% endif %}
  <span class="badge {% if news.primary_format=='story' %}badge-purple{% else %}badge-blue{% endif %}" style="flex-shrink:0">{{news.format_label}}{% if news.is_long_form %} 长文{% endif %}</span>
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
      <div class="meta-item">来源 <b>{{news.source or '-'}}</b></div>
      <div class="meta-item">新闻时间 <b>{{news.pub_time or '-'}}</b></div>
      <div class="meta-item">入库时间 <b>{{news.created_at[:16] if news.created_at else '-'}}</b></div>
      {% if news.publish_time %}<div class="meta-item" style="color:var(--green)">XHS发布 <b>{{news.publish_time}}</b> <span style="cursor:pointer;color:var(--red);font-size:10px;margin-left:4px" onclick="autoSaveField('publish_time','');location.reload()">[清除]</span></div>{% endif %}
      <div style="margin-top:8px">
        <a href="{{news.link or ''}}" target="_blank" style="font-size:11px;color:var(--blue);text-decoration:none;display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap" title="{{news.link or ''}}">🔗 查看原文</a>
      </div>
    </div>

    <!-- Settings -->
    <div class="card-section">
      <div class="card-section-title">状态</div>
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11.5px">
        <select name="publish_xhs" onchange="autoSaveField('publish_xhs',this.value)" class="meta-select" style="font-size:11px;padding:2px 5px">
          <option value="0" {{'selected' if not news.publish_xhs else ''}}>不发布</option>
          <option value="1" {{'selected' if news.publish_xhs else ''}}>发布XHS</option>
        </select>
        <select name="publish_mode" onchange="onModeChange(this.value)" class="meta-select" style="font-size:11px;padding:2px 5px">
          <option value="normal" {{'selected' if news.publish_mode == 'normal' or not news.publish_mode else ''}}>默认</option>
          <option value="caption" {{'selected' if news.publish_mode == 'caption' else ''}}>短配文</option>
          <option value="free" {{'selected' if news.publish_mode == 'free' else ''}}>自由</option>
        </select>
        <select name="status" onchange="autoSaveField('status',this.value)" class="meta-select" style="font-size:11px;padding:2px 5px">
          <option value="active" {{'selected' if news.status=='active' else ''}}>活跃</option>
          <option value="discarded" {{'selected' if news.status=='discarded' else ''}}>丢弃</option>
          <option value="archived" {{'selected' if news.status=='archived' else ''}}>归档</option>
        </select>
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
      <div class="card-section-title">标签</div>
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
      <div style="display:flex;flex-wrap:wrap;gap:3px 0">
        {% set stats = [
          ('👁', '浏览', news.xhs_views or 0, ''),
          ('❤', '点赞', news.xhs_likes or 0, ''),
          ('⭐', '收藏', news.xhs_saves or 0, ''),
          ('💬', '评论', news.xhs_comments or 0, ''),
          ('🔄', '分享', news.xhs_shares or 0, ''),
          ('➕', '涨粉', news.xhs_fans_gained or 0, ''),
          ('👀', '曝光', news.xhs_impression or 0, ''),
          ('🎯', '点击率', ((news.xhs_click_rate or 0)*100)|round(1), '%'),
        ] %}
        {% if news.xhs_saves and news.xhs_views %}{% set _ = stats.append(('📊', '收藏率', (news.xhs_saves / news.xhs_views * 100)|round(1), '%')) %}{% endif %}
        {% for icon, label, val, unit in stats %}
        <div style="width:50%;display:flex;align-items:baseline;gap:4px;padding:2px 0;font-size:11.5px">
          <span style="color:var(--text3);width:14px">{{icon}}</span>
          <span style="color:var(--text2);min-width:30px">{{label}}</span>
          <b style="color:var(--text)">{{val}}{{unit}}</b>
        </div>
        {% endfor %}
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
    <div class="card" style="padding:16px 18px">
      <h3 style="font-size:13px;font-weight:600;margin-bottom:12px">📸 图集 / 封面</h3>
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
    </div>

    <!-- Content editing -->
    <div class="card">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px">
        <h3 style="font-size:13px;font-weight:600;color:var(--text)">✏️ 内容编辑</h3>
      </div>
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
    </div>

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
async function onModeChange(val){
  await autoSaveField('publish_mode',val);
  if(val==='free'){location.reload();return}
  var s=document.getElementById('freeTextSection');
  if(s)s.remove();
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
function updateContentCount(){
  var ta=document.getElementById('contentHidden'),c=document.getElementById('contentCount');
  if(!ta||!c)return;
  var n=xhsCharCount(ta.value);
  c.textContent=n+'/1000';
  c.style.color=n>1000?'var(--red)':'var(--text3)';
}
updateTitleCount();
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
document.querySelectorAll('.auto-resize').forEach(function(ta){
  ta.addEventListener('input',function(){autoGrow(this)});
  autoGrow(ta);
});

document.getElementById('saveBtn').addEventListener('click',async()=>{
  // 若 Editor.js 激活，先序列化内容到隐藏 textarea
  if(_ejsEditor){
    try{const out=await _ejsEditor.save();document.getElementById('contentHidden').value=_ejsBlocksToText(out.blocks||[]);}catch(e){}
  }
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
function togglePublishImg(el){var cb=el.querySelector('input[type=checkbox]');cb.checked=!cb.checked;el.style.opacity=cb.checked?'1':'0.4';savePublishImages()}
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

function _initEditorJs(){
  const el=document.getElementById('editorjs');
  if(!el||typeof EditorJS==='undefined') return;
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
  if(!document.getElementById('editorjs')) return;
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
            except: pass
    scores = get_score_dims(key)

    # 解析体裁
    import re as _re
    fs_raw = news.get('format_suitability', '["news"]')
    try:
        fs_list = json.loads(fs_raw) if isinstance(fs_raw, str) else (fs_raw or ['news'])
    except Exception:
        fs_list = ['news']
    news['primary_format'] = fs_list[0] if fs_list else 'news'
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

    return rts(DETAIL_HTML, news=news, scores=scores,
               story_parts=story_parts, story_tweet_imgs=story_tweet_imgs)

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

@app.route('/api/news/<key>', methods=['PUT'])
def api_update(key):
    data = request.get_json()
    update_news(key, data)
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
    app.run(host='0.0.0.0', port=5000, debug=True)
