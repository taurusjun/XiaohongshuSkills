#!/bin/bash
# xhs 容器入口：准备卷属主 → 一次性引导 venv → 装 crontab → 起 supervisord
set -euo pipefail
cd /home/user/PG/XiaohongshuSkills

mkdir -p /tmp/.X11-unix && chmod 1777 /tmp/.X11-unix
mkdir -p /data/chrome-profiles /backup
chown -R user:user /data/chrome-profiles /backup 2>/dev/null || true
# 清 Chrome 陈旧单例锁（容器重建后 hostname 变化会让 Chrome 拒绝启动）
rm -f /data/chrome-profiles/*/Singleton* 2>/dev/null || true

mkdir -p data data/logs tmp logs
chown -R user:user data tmp logs .venv 2>/dev/null || true
# 图集缓存卷（命名卷根属 root）：交给 user，否则下载 Permission denied
mkdir -p /home/user/.cache/xhs_images
chown -R user:user /home/user/.cache 2>/dev/null || true

if [ ! -x .venv/bin/python ]; then
  echo "[entrypoint] bootstrapping .venv (first run)..."
  su user -c 'cd /home/user/PG/XiaohongshuSkills && python3 -m venv .venv \
    && .venv/bin/pip install --upgrade pip wheel \
    && .venv/bin/pip install -r requirements-docker.txt'
fi

crontab -u user /opt/ops/crontab 2>/dev/null || true
rm -f tmp/login_status_cache.json 2>/dev/null || true

exec /usr/bin/supervisord -c /opt/ops/supervisord.conf
