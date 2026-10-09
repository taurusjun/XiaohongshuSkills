"""第4层：发布数据聚合 + data-feedback-patterns.md 的**仅追加**更新。

- aggregate(): 调 layer4_aggregate.py（只读，走 127.0.0.1:5000，不走代理）。
- context(): 给 LLM 的趋势表末尾 + 当前最大模式编号（`### N.`）。
- update(): 模式节插到 `## 跨会话趋势表` 之前；趋势行追加到趋势表末行之后。仅追加，不改历史。
"""
import datetime as dt
import re
import subprocess
import sys

from services import paths

__all__ = ["aggregate", "context", "update"]

FEEDBACK_REL = "skills/creative/xhs-daily-material-review/references/data-feedback-patterns.md"
AGG_REL = "skills/xhs-daily-material-review-layer4/scripts/layer4_aggregate.py"
TREND_MARK = "## 跨会话趋势表"


def _now_jst():
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Asia/Tokyo"))
    except Exception:  # noqa: BLE001
        return dt.datetime.utcnow() + dt.timedelta(hours=9)


def aggregate(date=None):
    """运行 layer4_aggregate.py，返回 stdout（只读）。失败返回错误串。"""
    script = paths.REPO_ROOT / AGG_REL
    if not script.exists():
        return f"[aggregate 跳过] 找不到 {AGG_REL}"
    now = _now_jst()
    args = [sys.executable, str(script), now.strftime("%Y-%m-%d"), now.strftime("%H:%M")]
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=240,
                           cwd=str(paths.REPO_ROOT))
    except Exception as e:  # noqa: BLE001
        return f"[aggregate 失败] {e}"
    out = r.stdout or ""
    if r.returncode != 0:
        out += f"\n[aggregate rc={r.returncode}]\n{(r.stderr or '')[-1500:]}"
    return out


def _fb_path():
    return paths.REPO_ROOT / FEEDBACK_REL


def context():
    p = _fb_path()
    if not p.exists():
        return {"last_pattern_no": 0, "tail": ""}
    txt = p.read_text(encoding="utf-8")
    nums = [int(m) for m in re.findall(r"^###\s+(\d+)\.", txt, re.M)]
    last = max(nums) if nums else 0
    i = txt.find(TREND_MARK)
    tail = txt[i:i + 3000] if i >= 0 else txt[-3000:]
    return {"last_pattern_no": last, "tail": tail}


def update(pattern_section, trend_row):
    """仅追加：模式节插入趋势表前；趋势行追加到趋势表末行后。返回 (ok, msg)。"""
    pat = (pattern_section or "").strip()
    row = (trend_row or "").strip()
    p = _fb_path()
    if not p.exists():
        return False, f"找不到 {FEEDBACK_REL}"
    if not pat and not row:
        return False, "LLM 未给 pattern/trend_row"
    txt = p.read_text(encoding="utf-8")
    orig = txt
    if pat:
        i = txt.find(TREND_MARK)
        if i < 0:
            return False, "找不到趋势表锚点"
        txt = txt[:i] + pat.rstrip() + "\n\n---\n\n" + txt[i:]
    if row:
        lines = txt.split("\n")
        mi = next((k for k, l in enumerate(lines) if l.strip() == TREND_MARK), None)
        if mi is None:
            return False, "找不到趋势表锚点"
        # 锚点之后**最后一个**以 | 开头的行 = 趋势表末行（表内可能有非 | 的注释行，不能提前 break）
        cand = [k for k in range(mi + 1, len(lines)) if lines[k].strip().startswith("|")]
        if not cand:
            return False, "趋势表无数据行"
        lines.insert(cand[-1] + 1, row)
        txt = "\n".join(lines)
    if txt == orig:
        return False, "无变化"
    p.write_text(txt, encoding="utf-8")
    return True, f"已更新（模式节={'有' if pat else '无'}，趋势行={'有' if row else '无'}）"
