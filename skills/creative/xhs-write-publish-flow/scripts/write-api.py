#!/usr/bin/env python3
"""
write-api.py — 将 draft.md 写入 DB 的官方工具。

用法：
  ALL_PROXY="" python3 scripts/write-api.py <完整40位key> <标题> <draft文件路径>

功能：
  - 读取 draft 文件：第一行（##... 或 #...）提取为 rewritten_title，其余为 rewritten_content
  - 自动去除行首 Markdown 标题标记（# 或 ##）
  - 自动设置 publish_mode=rewritten, preselected=1, publish_xhs=0
  - 通过 urllib 发送，绕过 SOCKS5 代理

示例：
  ALL_PROXY="" python3 scripts/write-api.py \
    945feea09999c54c977bc21787e6c1a78f3bb209 \
    "生放送唱完4首歌，他坦白：拉链一直开着" \
    /tmp/xhs_draft_s1_takahashi.md

验证：
  curl -s --noproxy '*' http://127.0.0.1:5000/api/news/<key> | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'body: {len(d[\"rewritten_content\"])} chars')
print(f'title: {d[\"rewritten_title\"]}')
print(f'pre={d[\"preselected\"]} pub={d[\"publish_xhs\"]} mode={d[\"publish_mode\"]}')
"""
import json
import urllib.request
import sys


def strip_md_title(line: str) -> str:
    """Remove leading markdown heading markers (# or ##) from a title line."""
    stripped = line.strip()
    # Handle both "## title" and "# title"
    while stripped.startswith('#'):
        stripped = stripped.lstrip('#').strip()
    return stripped


def main():
    if len(sys.argv) < 4:
        print("用法: python3 write-api.py <key> <title> <draft_file>", file=sys.stderr)
        sys.exit(1)

    key = sys.argv[1]
    # title parameter is accepted for backward compatibility but we
    # EXTRACT from the draft file to ensure consistency
    draft_path = sys.argv[3]

    with open(draft_path, 'r') as f:
        full_text = f.read()

    lines = full_text.strip().split('\n')
    title_line = strip_md_title(lines[0])
    body = '\n'.join(lines[1:]) if len(lines) > 1 else ''
    body = body.strip()

    data = json.dumps({
        'publish_mode': 'rewritten',
        'rewritten_title': title_line,
        'rewritten_content': body,
        'preselected': 1,
        'publish_xhs': 0,
    }).encode('utf-8')

    req = urllib.request.Request(
        f'http://127.0.0.1:5000/api/news/{key}',
        data=data,
        headers={'Content-Type': 'application/json'},
        method='PUT'
    )

    with urllib.request.urlopen(req, timeout=10) as resp:
        print('Response:', resp.read().decode())

    print(f'Title: {title_line}')
    print(f'Body length: {len(body)} chars')
    print(f'Key: {key}')


if __name__ == '__main__':
    main()
