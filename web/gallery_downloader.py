#!/usr/bin/env python3
"""Web UI 独享图集模块 — 下载/预览/上传全流程，不依赖 scripts/gallery_*.py"""

import os, sys, json, time, re
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
from config.yahoo_conf import GALLERY_CACHE_DIR
from sqlite_db import get_by_key, update_news

# Reuse scripts download components
from gallery_fetch import detect_gallery_link, scrape_gallery_images, HEADERS
# Import Instagram/YouTube downloaders
from unified_media_downloader import download_instagram as _dl_ig
from unified_media_downloader import download_youtube as _dl_yt

CACHE_DIR = Path(GALLERY_CACHE_DIR).expanduser()

# Task tracking {key: {'status': 'running'|'done'|'error:...', 'log': str, 'images': [str]}}
_tasks = {}


def get_status(key: str) -> dict:
    """查询下载状态"""
    task = _tasks.get(key)
    if task:
        return task
    # Check cache dir
    d = CACHE_DIR / key
    if d.is_dir():
        cached = [str(d / f) for f in sorted(os.listdir(d))
                  if f.endswith(('.jpg','.jpeg','.png','.webp','.mp4')) and not f.startswith('.')]
        if cached:
            return {'status': 'done', 'images': cached, 'log': ''}
    return {'status': 'idle', 'images': [], 'log': ''}


def _download(key: str, gallery_url: str = ""):
    """后台下载线程"""
    task = {'status': 'running', 'log': '', 'images': []}
    _tasks[key] = task
    log = []

    try:
        row = get_by_key(key)
        article_url = row.get('link', '') if row else ''

        # Detect gallery if not provided
        if not gallery_url:
            if article_url:
                log.append(f'🔍 检测图集链接: {article_url[:60]}...')
                gallery_url = detect_gallery_link(article_url)
            if not gallery_url:
                task['status'] = 'error: 未检测到图集链接'
                task['log'] = '\n'.join(log)
                return

        log.append(f'📸 图集: {gallery_url}')
        update_news(key, {'gallery_url': gallery_url})

        d = CACHE_DIR / key
        d.mkdir(parents=True, exist_ok=True)

        # Instagram
        if 'instagram.com/p/' in gallery_url or 'instagram.com/reel/' in gallery_url:
            files = _dl_ig(gallery_url, d)
            for f in files:
                log.append(f'  ✓ {f}')
            image_urls = []
        elif 'youtube.com' in gallery_url or 'youtu.be' in gallery_url:
            m = re.search(r'(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})', gallery_url)
            if m:
                files = _dl_yt(m.group(1), d)
                for f in files:
                    log.append(f'  ✓ {f}')
            image_urls = []
        else:
            image_urls = scrape_gallery_images(gallery_url)
            if not image_urls:
                task['status'] = 'error: 图集为空'
                task['log'] = '\n'.join(log)
                return
            log.append(f'📷 抓到 {len(image_urls)} 张图片')
            task['log'] = '\n'.join(log)
            for i, url in enumerate(image_urls):
                try:
                    resp = __import__('requests').get(url, headers=HEADERS, timeout=30)
                    ext = url.rsplit('.', 1)[-1].split('?')[0] or 'jpg'
                    if ext not in ('jpg','jpeg','png','webp','gif','mp4'):
                        ext = 'jpg'
                    fname = f'{i+1:03d}.{ext}'
                    (d / fname).write_bytes(resp.content)
                    log.append(f'    ✓ {fname}')
                except Exception as e2:
                    log.append(f'    ✗ {i+1:03d}: {e2}')
                task['log'] = '\n'.join(log)

        task['status'] = 'done'
        images = [str(d / f) for f in sorted(os.listdir(d))
                  if f != 'meta.json' and not f.startswith('.')]
        task['images'] = images
        log.append(f'✅ 已缓存 {len(images)} 张')
        task['log'] = '\n'.join(log)

    except Exception as e:
        task['status'] = f'error: {e}'
        task['log'] = '\n'.join(log)


def trigger_download(key: str, gallery_url: str = ""):
    """触发后台下载"""
    import threading
    # Reset old task
    _tasks.pop(key, None)
    t = threading.Thread(target=_download, args=(key, gallery_url), daemon=True)
    t.start()
    return True


def upload_selected(key: str, selected_paths: list[str]) -> list[str]:
    """上传选中图片到 Cloudinary，返回 CDN URL 列表"""
    from image_uploader import upload_to_cloudinary
    uploaded = []
    for path in selected_paths:
        url = upload_to_cloudinary(path)
        if url:
            uploaded.append(url)
    return uploaded
