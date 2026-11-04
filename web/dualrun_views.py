"""dualrun_views.py — 双跑日报页面（只读）。

数据来源（按优先级）：
1. 环境变量 DUAL_RUN_DIR
2. <repo>/logs/dual_run      （容器：xhs-storage/logs 挂载进 repo/logs）
3. ~/PG/xhs-storage/logs/dual_run（生产宿主）

路由：
    GET /dual-run                列出日期 + 渲染 Markdown
    GET /dual-run?date=YYYY-MM-DD
    GET /dual-run/raw/<name>     原始 Markdown
"""
import os
import re
from pathlib import Path

import markdown
from flask import Blueprint, Response, abort, render_template_string

dualrun_bp = Blueprint("dualrun", __name__)

_SAFE = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|trend-[\d-]+)$")


def _report_dir() -> Path:
    env = os.environ.get("DUAL_RUN_DIR")
    if env:
        return Path(env).expanduser()
    repo = Path(__file__).resolve().parent.parent
    cand = repo / "logs" / "dual_run"
    if cand.is_dir():
        return cand
    return Path("~/PG/xhs-storage/logs/dual_run").expanduser()


def _reports() -> list[str]:
    d = _report_dir()
    if not d.is_dir():
        return []
    names = [p.stem for p in d.glob("*.md") if _SAFE.match(p.stem)]
    return sorted(names, reverse=True)


PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>双跑日报</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",sans-serif;
     background:#f6f6f7;color:#222;line-height:1.7}
.top{background:#fff;border-bottom:1px solid #eee;padding:14px 22px;display:flex;
     align-items:center;gap:14px;position:sticky;top:0;z-index:5}
.top b{font-size:17px}
.top .hint{color:#999;font-size:12px}
.wrap{display:flex;gap:18px;max-width:1180px;margin:18px auto;padding:0 18px}
.side{width:170px;flex-shrink:0}
.side a{display:block;padding:7px 10px;border-radius:7px;color:#444;text-decoration:none;
        font-size:13px;margin-bottom:4px}
.side a:hover{background:#f0f0f2}
.side a.on{background:#ff2442;color:#fff}
main{flex:1;background:#fff;border-radius:12px;padding:26px 30px;min-width:0;
     box-shadow:0 1px 3px rgba(0,0,0,.05)}
main h1{font-size:20px;margin:0 0 16px}
main h2{font-size:16px;margin:24px 0 8px;padding-top:14px;border-top:1px solid #f0f0f0}
main table{border-collapse:collapse;width:100%;margin:8px 0 14px;font-size:13px}
main th,main td{border:1px solid #ececec;padding:6px 10px;text-align:left}
main th{background:#fafafa;font-weight:600}
main code{background:#f4f4f5;padding:1px 5px;border-radius:4px;font-size:12px}
main blockquote{color:#777;border-left:3px solid #eee;padding-left:10px;margin:8px 0}
.raw{float:right;font-size:12px;color:#ff2442;text-decoration:none}
.empty{color:#999;padding:40px 0;text-align:center}
</style>
</head>
<body>
<div class="top"><b>🔬 双跑日报</b><span class="hint">生产 vs 容器 · 每天 05:30 自动生成</span></div>
<div class="wrap">
  <div class="side">
    {% for n in names %}
      <a href="/dual-run?date={{ n }}" class="{{ 'on' if n == current else '' }}">{{ n }}</a>
    {% endfor %}
    {% if not names %}<div class="hint">暂无报告</div>{% endif %}
  </div>
  <main>
    {% if current %}<a class="raw" href="/dual-run/raw/{{ current }}">原始 Markdown</a>{% endif %}
    {% if body %}{{ body|safe }}{% else %}<div class="empty">暂无日报</div>{% endif %}
  </main>
</div>
</body>
</html>
"""


@dualrun_bp.get("/dual-run")
def dual_run_page():
    from flask import request
    names = _reports()
    current = request.args.get("date") or (names[0] if names else "")
    body = ""
    if current in names:
        path = _report_dir() / f"{current}.md"
        body = markdown.markdown(path.read_text(encoding="utf-8"),
                                 extensions=["tables", "fenced_code"])
    return render_template_string(PAGE, names=names, current=current, body=body)


@dualrun_bp.get("/dual-run/raw/<name>")
def dual_run_raw(name):
    if not _SAFE.match(name):
        abort(404)
    path = _report_dir() / f"{name}.md"
    if not path.is_file():
        abort(404)
    return Response(path.read_text(encoding="utf-8"),
                    mimetype="text/plain; charset=utf-8")
