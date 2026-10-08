"""草稿 I/O —— dump_ja / write-api 的唯一实现（路径/接口 env 化）。"""
import argparse
import json
import os
import sys
import textwrap
import urllib.request

from services import paths

WRAP = 500
__all__ = ["load_l1_raw", "render_dump", "strip_md_title", "write_draft", "get_news",
           "main_dump", "main_write"]


def load_l1_raw(day: str):
    path = paths.workspace_dir() / f"l1_raw_{day}.json"
    if not path.exists():
        sys.exit(f"缺 {path} —— 先按 docstring 里的 curl 命令补 dump")
    with open(path, encoding="utf-8") as f:
        return json.load(f)["rows"]


def render_dump(rows, prefixes):
    out = []
    for p in prefixes:
        hits = [r for r in rows if r["key"].startswith(p)]
        if not hits:
            out.append(f"===== {p} NOT FOUND")
            continue
        r = hits[0]
        out.append(f"===== {r['key']} | {r['title']} | fmt={r['format']} lf={r['is_long_form']} "
                   f"| ja_len={len(r.get('content_ja') or '')} | ts={r.get('title_score')} cs={r.get('content_score')}")
        out.append(f"LINK: {r.get('link')}")
        out.append(f"SUMMARY: {(r.get('summary') or '')[:300]}")
        out.append("--- content_ja ---")
        txt = (r.get("content_ja") or "").replace("\u3000", " ")
        out.extend(textwrap.wrap(txt, WRAP))
        out.append("--- end ---\n")
    return "\n".join(out)


def main_dump(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 2:
        print("用法: dump_ja.py <YYYY-MM-DD> <key前缀> [key前缀 ...]")
        return 1
    rows = load_l1_raw(argv[0])
    print(render_dump(rows, argv[1:]))
    return 0


def strip_md_title(line: str) -> str:
    stripped = line.strip()
    while stripped.startswith("#"):
        stripped = stripped.lstrip("#").strip()
    return stripped


def write_draft(key: str, title: str, body: str, api_base: str | None = None) -> dict:
    """PUT 入库（publish_mode=rewritten, preselected=1, publish_xhs=0）。返回响应 dict。"""
    base = api_base or paths.api_base()
    data = json.dumps({
        "publish_mode": "rewritten",
        "rewritten_title": title,
        "rewritten_content": body,
        "preselected": 1,
        "publish_xhs": 0,
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/news/{key}", data=data,
                                 headers={"Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(req, timeout=10) as resp:
        try:
            return json.loads(resp.read())
        except Exception:
            return {"ok": True}


def _put_news_raw(key: str, title: str, body: str, api_base: str | None = None) -> str:
    """PUT 入库并返回**原始响应文本**（与原 write-api.py 打印一致）。"""
    base = api_base or paths.api_base()
    data = json.dumps({
        "publish_mode": "rewritten", "rewritten_title": title,
        "rewritten_content": body, "preselected": 1, "publish_xhs": 0,
    }).encode("utf-8")
    req = urllib.request.Request(f"{base}/api/news/{key}", data=data,
                                 headers={"Content-Type": "application/json"}, method="PUT")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.read().decode()


def get_news(key: str, api_base: str | None = None) -> dict:
    base = api_base or paths.api_base()
    with urllib.request.urlopen(f"{base}/api/news/{key}", timeout=10) as resp:
        return json.loads(resp.read())


def main_write(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) < 3:
        print("用法: write-api.py <key> <title> <draft_file>", file=sys.stderr)
        return 1
    key, _title, draft_path = argv[0], argv[1], argv[2]
    with open(draft_path, encoding="utf-8") as f:
        full_text = f.read()
    lines = full_text.strip().split("\n")
    title_line = strip_md_title(lines[0])
    body = "\n".join(lines[1:]).strip() if len(lines) > 1 else ""
    print("Response:", _put_news_raw(key, title_line, body))
    print(f"Title: {title_line}")
    print(f"Body length: {len(body)} chars")
    print(f"Key: {key}")
    return 0
