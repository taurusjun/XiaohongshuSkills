"""交付层 —— 唯一实现（替代 Telegram segment-send）。

渠道：
  feishu : 用 scripts/feishu_bot.py 分段发到运营者（FEISHU_OPERATOR_OPEN_ID）
  web    : 落盘到 data/reviews/<name>.md（由 admin UI / 其他服务读取）
默认渠道取 env XHS_DELIVERY_CHANNEL，缺省 feishu。
"""
import argparse
import os
import sys
from pathlib import Path

from services import paths

__all__ = ["chunk_markdown", "deliver", "feishu_send", "web_save", "main_legacy"]

DEFAULT_MAX = 3500


def chunk_markdown(text: str, max_chars: int = DEFAULT_MAX):
    """按行边界切分，尽量不切开表格行、不超 max_chars。"""
    chunks, cur = [], ""
    for line in text.split("\n"):
        if cur and len(cur) + len(line) + 1 > max_chars:
            chunks.append(cur)
            cur = line
        else:
            cur = line if not cur else cur + "\n" + line
    if cur:
        chunks.append(cur)
    return chunks


def feishu_md_to_lark(md: str) -> str:
    """把 Markdown 转成飞书 lark_md：标题加粗；**表格转成条目列表**（lark_md 不支持表格）。"""
    lines = md.split("\n")
    out, i = [], 0
    while i < len(lines):
        ln = lines[i]
        # GFM 表格：表头 + 分隔行 + 数据行
        if (ln.strip().startswith("|") and i + 1 < len(lines)
                and set(lines[i + 1].strip().replace("|", "").replace(" ", "")) <= set("-:")):
            header = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
                if cells:
                    title = cells[-1]
                    meta = " ".join(f"{header[j]}={cells[j]}" for j in range(len(cells) - 1)
                                    if j < len(header) and header[j] not in ("title", "标题"))
                    out.append(f"- **{title}**" + (f"  ({meta})" if meta else ""))
                i += 1
            continue
        if ln.startswith("#"):
            out.append("**" + ln.lstrip("#").strip() + "**")
        elif ln.strip() == "---":
            out.append("———————")
        else:
            out.append(ln)
        i += 1
    return "\n".join(out)


def _feishu_card(title: str, lark_md: str) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": title[:60]}, "template": "blue"},
        "elements": [{"tag": "div", "text": {"tag": "lark_md", "content": lark_md}}],
    }


def feishu_send(text: str) -> bool:
    """发到运营者：Markdown → lark_md 交互卡片（飞书才能正确渲染）。"""
    sys.path.insert(0, str(paths.REPO_ROOT / "scripts"))
    try:
        import feishu_bot  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[delivery] feishu_bot 不可用: {e}", file=sys.stderr)
        return False
    open_id = getattr(feishu_bot, "FEISHU_OPERATOR_OPEN_ID", "") or ""
    if not open_id:
        return bool(feishu_bot.send_alert(text))
    lark = feishu_md_to_lark(text)
    chunks = chunk_markdown(lark, 3200)
    ok = True
    for idx, c in enumerate(chunks):
        title = c.lstrip("*").split("\n", 1)[0][:50] or "交付"
        ok = bool(feishu_bot.send_card(open_id, _feishu_card(title, c))) and ok
    return ok


def web_save(text: str, name: str = "latest.md") -> bool:
    d = paths.REPO_ROOT / "data" / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return True


def deliver(text: str, channel: str | None = None, name: str = "latest.md",
            max_chars: int = DEFAULT_MAX, send_fn=None, save_local: bool = True) -> dict:
    """交付：**始终本地落盘**（data/reviews/），channel 含 feishu 时额外推送。

    渠道取值（XHS_DELIVERY_CHANNEL 或 channel 参数）：
      feishu（默认）: 本地存档 + 飞书推送
      web          : 仅本地存档
    """
    channel = channel or os.environ.get("XHS_DELIVERY_CHANNEL") or "feishu"
    result = {"channel": channel, "chunks": 0, "local_saved": False, "path": ""}
    if save_local:
        result["local_saved"] = web_save(text, name)
        result["path"] = str(paths.REPO_ROOT / "data" / "reviews" / name)
    if channel == "web":
        result["ok"] = bool(result["local_saved"])
        return result
    chunks = chunk_markdown(text, max_chars)
    send = send_fn or feishu_send
    results = [bool(send(c)) for c in chunks]
    result["chunks"] = len(chunks)
    result["feishu_sent"] = all(results) if results else False
    result["ok"] = bool(result["local_saved"]) and bool(result["feishu_sent"])
    return result


def main_legacy(argv=None) -> int:
    """兼容原 segment-send.py 调用：<file_path> [<chat_id>] [<max_chars>]。"""
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="交付 review 存档（feishu/web，替代 Telegram）")
    ap.add_argument("file_path")
    ap.add_argument("chat_id", nargs="?", default="")
    ap.add_argument("max_chars", nargs="?", type=int, default=DEFAULT_MAX)
    ap.add_argument("--channel", default=None)
    args = ap.parse_args(argv)
    text = Path(args.file_path).read_text(encoding="utf-8")
    r = deliver(text, channel=args.channel, name=Path(args.file_path).name, max_chars=args.max_chars)
    print(f"[delivery] {r}")
    return 0 if r["ok"] else 1
