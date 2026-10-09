#!/usr/bin/env bash
# 宿主机(Mac)重启后的恢复 + 代理端口漂移处理。
# 用法：bash ops/recover-host.sh
# 说明：VeloceMac 的 Mixed 端口会在 20800-20820 漂移，**每次都要重新扫描确认**。
set -euo pipefail
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO"
GATEWAY=192.168.5.2

echo "[1] colima"
if ! colima status >/dev/null 2>&1; then
  colima start
fi

echo "[2] 扫描真实代理端口 (20800-20820) ..."
PORT=""
for p in $(seq 20800 20820); do
  if colima ssh -- sh -c "curl -s -m 4 -x socks5h://$GATEWAY:$p -o /dev/null https://www.baidu.com" </dev/null 2>/dev/null; then
    PORT="$p"; break
  fi
done
if [ -z "$PORT" ]; then
  echo "❌ 找不到可用代理端口 —— 宿主 VeloceMac 是否已开启？"; exit 1
fi
echo "    live proxy port = $PORT"

echo "[3] VM dockerd 代理 (拉镜像用) -> $PORT"
colima ssh -- sudo mkdir -p /etc/systemd/system/docker.service.d </dev/null
colima ssh -- sudo bash -c "printf '[Service]\nEnvironment=\"HTTP_PROXY=socks5://$GATEWAY:$PORT\"\nEnvironment=\"HTTPS_PROXY=socks5://$GATEWAY:$PORT\"\nEnvironment=\"NO_PROXY=localhost,127.0.0.1,$GATEWAY/24\"\n' > /etc/systemd/system/docker.service.d/http-proxy.conf" </dev/null
colima ssh -- sudo systemctl daemon-reload </dev/null
colima ssh -- sudo systemctl restart docker </dev/null
sleep 4

echo "[4] 同步 override 里的容器代理端口 -> $PORT"
sed -i '' -E "s#(http://$GATEWAY:)[0-9]+#\1$PORT#g; s#(socks5://$GATEWAY:)[0-9]+#\1$PORT#g" docker-compose.override.mac.yml

echo "[5] build + up（必须带 override，否则 data/.venv 卷不挂载）"
docker compose -f docker-compose.yml -f docker-compose.override.mac.yml build
docker compose -f docker-compose.yml -f docker-compose.override.mac.yml up -d

echo "[6] 自检"
sleep 8
docker exec xhs bash -lc 'cd /home/user/PG/XiaohongshuSkills && PYTHONPATH=. .venv/bin/python -c "from services import news,paths; import sqlite3; c=sqlite3.connect(paths.sqlite_path()); print(\"news rows:\", c.execute(\"select count(*) from news\").fetchone()[0])"'
echo "done."
