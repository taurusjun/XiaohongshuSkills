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

def get_key(keyword: str, db: str | None = None, limit: int = 20):
    """查完整 key + 改写状态 + content_ja 长度（迁移自 get-key.sh）。"""
    import sqlite3
    from services import paths
    db = db or paths.sqlite_path()
    conn = sqlite3.connect(db)
    try:
        if keyword == "全部":
            sql = ("SELECT key, substr(title,1,40), "
                   "CASE WHEN rewritten_title!='' THEN '已写' ELSE '未写' END, "
                   "length(content_ja), COALESCE(preselected,0), COALESCE(publish_xhs,0), "
                   "substr(rewritten_content,1,50) FROM news "
                   "WHERE title!='' AND (publish_xhs IS NULL OR publish_xhs=0) "
                   "AND status!='archived' ORDER BY id DESC LIMIT ?")
            return [tuple(r) for r in conn.execute(sql, (limit,))]
        sql = ("SELECT key, substr(title,1,40), "
               "CASE WHEN rewritten_title!='' THEN '已写' ELSE '未写' END, "
               "length(content_ja), COALESCE(preselected,0), COALESCE(publish_xhs,0), "
               "substr(rewritten_content,1,50) FROM news "
               "WHERE title LIKE ? OR key LIKE ? ORDER BY id DESC LIMIT ?")
        like = f"%{keyword}%"
        return [tuple(r) for r in conn.execute(sql, (like, like, limit))]
    finally:
        conn.close()


def main_getkey(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("用法: get-key.sh <关键词>   （全部=最近50条未发布素材）")
        return 1
    rows = get_key(argv[0])
    cols = ["key", "title40", "改写", "ja_len", "pre", "pub", "rc50"]
    try:
        print("\t".join(cols))
        for r in rows:
            print("\t".join(str(x) if x is not None else "" for x in r))
    except BrokenPipeError:
        pass
    return 0


def verify_rewrite(key: str, api_base: str | None = None, fetch=None) -> dict:
    """入库合规检查（迁移自 verify_rewrite.sh）。返回检查结果 dict。"""
    d = (fetch or get_news)(key, api_base)
    rt = d.get("rewritten_title") or ""
    rc = d.get("rewritten_content") or ""
    ps = d.get("preselected")
    px = d.get("publish_xhs")
    pm = d.get("publish_mode") or ""
    rk = d.get("related_keys") or ""
    ch = d.get("channel") or ""
    checks = {
        "标题不以#开头": not rc.startswith("#"),
        "正文开头不是标题": not (rc[:len(rt)] == rt and rt),
        "preselected": ps == 1,
        "publish_xhs": px == 1,
        "publish_mode": pm,
        "channel": ch,
    }
    return {"title": rt, "body_len": len(rc), "preselected": ps, "publish_xhs": px,
            "publish_mode": pm, "channel": ch, "related_keys": rk, "checks": checks}


def main_verify(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("用法: verify_rewrite.sh <完整40位key>")
        return 1
    key = argv[0]
    r = verify_rewrite(key)
    print("=== 入库验证 ===")
    print(f"Key: {key}")
    print()
    print(f"标题: {r['title']}")
    print(f"正文字数: {r['body_len']}")
    print(f"preselected: {r['preselected']}")
    print(f"publish_xhs: {r['publish_xhs']}")
    print(f"publish_mode: {r['publish_mode']}")
    print(f"channel: {r['channel']}")
    print(f"related_keys: {r['related_keys'][:60]}")
    print()
    c = r["checks"]
    print(f"检查1 {'✅ 正文不以标题行开头' if c['标题不以#开头'] else '❌ 正文以标题行开头'}")
    print(f"检查2 {'✅ 正文开头不是标题' if c['正文开头不是标题'] else '❌ 标题混入正文'}")
    print(f"检查3 {'✅ preselected=1' if c['preselected'] else '⚠️ preselected=' + str(r['preselected'])}")
    print(f"检查4 {'✅ publish_xhs=1' if c['publish_xhs'] else '⚠️ publish_xhs=' + str(r['publish_xhs'])}")
    print(f"检查5 {'✅ publish_mode=' + r['publish_mode'] if r['publish_mode'] else 'ℹ️ publish_mode=normal（默认）'}")
    if r["channel"] == "gzh":
        print("检查6 ✅ channel=gzh（公众号稿）")
    elif r["channel"] == "":
        print("检查6 ℹ️ channel为空（默认小红书）")
    else:
        print(f"检查6 ℹ️ channel={r['channel']}")
    return 0

