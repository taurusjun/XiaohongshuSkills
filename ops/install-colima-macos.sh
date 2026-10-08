#!/usr/bin/env bash
# 在 Intel Mac 上安装 colima + lima + docker CLI + compose
# 绕开 Homebrew Tier 3（这台机器上 lima/qemu/docker/go 均无 bottle）
# 用法：bash ops/install-colima-macos.sh
set -euo pipefail
PROXY="${HTTP_PROXY:-http://127.0.0.1:20809}"
export HTTPS_PROXY="$PROXY" HTTP_PROXY="$PROXY"
export ALL_PROXY="${ALL_PROXY:-socks5://127.0.0.1:20809}"
export NO_PROXY=127.0.0.1,localhost
export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"

DL=/tmp/xhs-dl; mkdir -p "$DL"; cd "$DL"

echo "[1/4] colima v0.10.3"
curl -sSL -o colima https://github.com/abiosoft/colima/releases/download/v0.10.3/colima-Darwin-x86_64
install -m 0755 colima /usr/local/bin/colima

echo "[2/4] lima v2.2.1 -> ~/.local"
curl -sSL -o lima.tar.gz https://github.com/lima-vm/lima/releases/download/v2.2.1/lima-2.2.1-Darwin-x86_64.tar.gz
rm -rf lima && mkdir lima && tar xzf lima.tar.gz -C lima
mkdir -p "$HOME/.local/bin" "$HOME/.local/share" "$HOME/.local/libexec"
cp -R lima/bin/. "$HOME/.local/bin/"
cp -R lima/share/. "$HOME/.local/share/"
cp -R lima/libexec/. "$HOME/.local/libexec/"

echo "[3/4] docker CLI 29.8.2"
curl -sSL -o docker.tgz https://download.docker.com/mac/static/stable/x86_64/docker-29.8.2.tgz
rm -rf docker && tar xzf docker.tgz
install -m 0755 docker/docker /usr/local/bin/docker

echo "[4/4] docker compose v2"
mkdir -p "$HOME/.docker/cli-plugins"
curl -sSL -o "$HOME/.docker/cli-plugins/docker-compose" \
  https://github.com/docker/compose/releases/latest/download/docker-compose-darwin-x86_64
chmod +x "$HOME/.docker/cli-plugins/docker-compose"

echo "done. next:"
echo '  export PATH="$HOME/.local/bin:/usr/local/bin:$PATH"'
echo '  colima start --vm-type vz --cpu 4 --memory 8 --disk 60'
