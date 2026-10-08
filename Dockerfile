# syntax=docker/dockerfile:1
# xhs 容器：Python 3.14 (Debian trixie) + Xvfb + Google Chrome
# 可移植契约：宿主差异只允许出现在 compose override / .env，绝不进本文件
FROM python:3.14-slim-trixie

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    DISPLAY=:99 \
    TZ=Asia/Shanghai

# ---- 系统依赖 ----
RUN apt-get update && apt-get install -y --no-install-recommends \
      xvfb x11-utils xauth \
      fonts-noto-cjk fonts-noto-color-emoji fonts-liberation \
      ffmpeg librsvg2-bin \
      nodejs npm \
      sqlite3 \
      git curl ca-certificates gnupg jq \
      supervisor cron tini netcat-openbsd procps psmisc \
    && rm -rf /var/lib/apt/lists/*

# ---- Google Chrome（apt 源，锁定稳定版；不做运行期 apt upgrade） ----
RUN curl -fsSL https://dl.google.com/linux/linux_signing_key.pub \
      | gpg --dearmor -o /usr/share/keyrings/google-chrome.gpg \
 && echo "deb [arch=amd64 signed-by=/usr/share/keyrings/google-chrome.gpg] https://dl.google.com/linux/chrome/deb/ stable main" \
      > /etc/apt/sources.list.d/google-chrome.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends google-chrome-stable \
 && rm -rf /var/lib/apt/lists/*

# ---- 非 root 用户：HOME=/home/user，与代码里 ~/PG/XiaohongshuSkills 硬编码一致 ----
RUN groupadd -g 1000 user 2>/dev/null || true \
 && useradd -m -u 1000 -g 1000 -s /bin/bash user \
 && mkdir -p /home/user/PG \
 && chown -R user:user /home/user

# ---- 容器编排配置（烘焙进镜像，不依赖 bind mount） ----
COPY ops/ /opt/ops/
RUN chmod +x /opt/ops/*.sh 2>/dev/null || true

WORKDIR /home/user/PG/XiaohongshuSkills
ENTRYPOINT ["/usr/bin/tini","--"]
CMD ["/opt/ops/entrypoint.sh"]
