"""交付层 —— 唯一实现（替代 Telegram segment-send）。

渠道（XHS_DELIVERY_CHANNEL，默认 feishu）：
  feishu : **始终本地落盘** + 飞书交互卡片（card 2.0：表格用 table 组件，文本用 lark_md）
  web    : 仅本地落盘 data/reviews/
飞书按**块**切分卡片，绝不在表格中间断开。
"""
import argparse
import os
import sys
from pathlib import Path

from services import paths

__all__ = ["chunk_markdown", "feishu_md_to_lark", "feishu_build_cards",
           "feishu_send", "deliver", "web_save", "main_legacy"]

DEFAULT_MAX = 3200          # 单卡片内容软上限（字符）


# ---------------------------------------------------------------------------
# Markdown 解析
# ---------------------------------------------------------------------------
def _split_blocks(md: str):
    """拆成 ('md', text) 与 ('table', header, rows)。表格整块，不在中间断。"""
    lines, blocks, buf, i = md.split("\n"), [], [], 0

    def flush():
        if buf:
            blocks.append(("md", "\n".join(buf)))
            buf.clear()

    while i < len(lines):
        ln = lines[i]
        if (ln.strip().startswith("|") and i + 1 < len(lines)
                and set(lines[i + 1].strip().replace("|", "").replace(" ", "")) <= set("-:")):
            flush()
            header = [c.strip() for c in ln.strip().strip("|").split("|")]
            i += 2
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            blocks.append(("table", header, rows))
            continue
        buf.append(ln)
        i += 1
    flush()
    return blocks


def feishu_md_to_lark(md: str) -> str:
    """Markdown → 飞书 lark_md：标题加粗、列表用「• 」（飞书不认 '- '）、表格转条目。"""
    out = []
    for b in _split_blocks(md):
        if b[0] == "table":
            _, header, rows = b
            for cells in rows:
                title = cells[-1] if cells else ""
                meta = "  ".join(f"{header[j]}={cells[j]}" for j in range(len(cells) - 1)
                                 if j < len(header) and header[j] not in ("title", "标题"))
                out.append(f"• **{title}**" + (f"　{meta}" if meta else ""))
            continue
        for ln in b[1].split("\n"):
            if ln.startswith("#"):
                out.append("**" + ln.lstrip("#").strip() + "**")
            elif ln.strip() == "---":
                out.append("———————")
            elif ln.strip().startswith(("- ", "* ")):
                out.append("• " + ln.strip()[2:].strip())
            else:
                out.append(ln)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# 飞书卡片
# ---------------------------------------------------------------------------
def _md_to_elements(md: str):
    """→ [(element, size)]；表格用 table 组件（整块），文本用 div(lark_md)。"""
    els = []
    for b in _split_blocks(md):
        if b[0] == "md":
            meta = []

            def flush_meta():
                if meta:
                    els.append(({"tag": "note", "elements": [
                        {"tag": "lark_md", "content": "\n".join(meta)}]},
                        sum(len(m) for m in meta)))
                    meta.clear()

            for ln in b[1].split("\n"):
                st = ln.strip()
                if st.startswith("# "):
                    continue                    # H1 → 已在卡片 header，正文不重复
                if st.startswith("**") and "：**" in st:
                    meta.append(feishu_md_to_lark(ln).strip())   # 元信息 → note
                    continue
                flush_meta()
                txt = feishu_md_to_lark(ln).strip() if st else ""
                if txt:
                    els.append(({"tag": "div", "text": {"tag": "lark_md", "content": txt}}, len(txt)))
            flush_meta()
        else:
            _, header, rows = b
            cols = [{"name": f"c{j}", "display_name": (header[j] if j < len(header) else f"col{j}"),
                     "data_type": "text", "width": "auto"} for j in range(len(header))]
            trows = [{f"c{j}": (r[j] if j < len(r) else "") for j in range(len(header))} for r in rows]
            if cols and trows:
                size = sum(len(v) for row in trows for v in row.values())
                els.append(({"tag": "table", "columns": cols, "rows": trows,
                             "row_height": "low", "page_size": 20,
                             "header_style": {"text_align": "left", "text_size": "normal",
                                              "background_color": "grey", "bold": True, "lines": 1},
                             "row_style": {"text_align": "left", "text_size": "normal", "lines": 1}},
                            size))
    return els


def _card(title: str, elements: list) -> dict:
    return {"config": {"wide_screen_mode": True},
            "header": {"title": {"tag": "plain_text", "content": title[:60]}, "template": "blue"},
            "elements": elements or [{"tag": "div", "text": {"tag": "lark_md", "content": "(空)"}}]}


def feishu_build_cards(md: str, title: str = "交付", max_chars: int = DEFAULT_MAX):
    """按块打包成多张卡片；**绝不把表格拆开**（表格超限则独占一张）。"""
    els = _md_to_elements(md)
    cards, cur, cur_size = [], [], 0
    for el, size in els:
        if cur and cur_size + size > max_chars:
            cards.append(_card(title, cur))
            cur, cur_size = [], 0
        cur.append(el)
        cur_size += size
    if cur:
        cards.append(_card(title, cur))
    cards = cards or [_card(title, [])]
    n = len(cards)
    if n > 1:                                    # 多段时标注 (i/N)
        for i, c in enumerate(cards, 1):
            c["header"]["title"]["content"] = f"{title[:50]}（{i}/{n}）"
    return cards


def feishu_send(text: str) -> bool:
    sys.path.insert(0, str(paths.REPO_ROOT / "scripts"))
    try:
        import feishu_bot  # type: ignore
    except Exception as e:  # noqa: BLE001
        print(f"[delivery] feishu_bot 不可用: {e}", file=sys.stderr)
        return False
    open_id = getattr(feishu_bot, "FEISHU_OPERATOR_OPEN_ID", "") or ""
    if not open_id:
        return bool(feishu_bot.send_alert(text))
    title = next((l.lstrip("#").strip() for l in text.split("\n") if l.startswith("#")), "交付")
    schema = os.environ.get("XHS_FEISHU_SCHEMA", "1.0")
    cards = feishu_build_cards_v2(text, title) if schema.startswith("2") else feishu_build_cards(text, title)
    ok = True
    for card in cards:
        ok = bool(feishu_bot.send_card(open_id, card)) and ok
    if not ok and schema.startswith("2"):        # 2.0 发送失败 → 回落 1.0
        for card in feishu_build_cards(text, title):
            ok = bool(feishu_bot.send_card(open_id, card)) and ok
    return ok


# ---------------------------------------------------------------------------
# 本地 / 交付
# ---------------------------------------------------------------------------
def chunk_markdown(text: str, max_chars: int = DEFAULT_MAX):
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


def web_save(text: str, name: str = "latest.md") -> bool:
    d = paths.REPO_ROOT / "data" / "reviews"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(text, encoding="utf-8")
    return True


def deliver(text: str, channel: str | None = None, name: str = "latest.md",
            max_chars: int = DEFAULT_MAX, send_fn=None, save_local: bool = True) -> dict:
    channel = channel or os.environ.get("XHS_DELIVERY_CHANNEL") or "feishu"
    result = {"channel": channel, "chunks": 0, "local_saved": False, "path": ""}
    if save_local:
        result["local_saved"] = web_save(text, name)
        result["path"] = str(paths.REPO_ROOT / "data" / "reviews" / name)
    if channel == "web":
        result["ok"] = bool(result["local_saved"])
        return result
    if send_fn is not None:                      # 测试/自定义 send
        chunks = chunk_markdown(text, max_chars)
        result["chunks"] = len(chunks)
        result["feishu_sent"] = all(bool(send_fn(c)) for c in chunks)
    else:
        _cards = (feishu_build_cards_v2(text, name)
                  if os.environ.get("XHS_FEISHU_SCHEMA", "1.0").startswith("2")
                  else feishu_build_cards(text, name))
        result["chunks"] = len(_cards)
        try:
            result["feishu_sent"] = bool(feishu_send(text))
        except Exception as e:  # noqa: BLE001
            print(f"[delivery] feishu 发送失败: {e}", file=sys.stderr)
            result["feishu_sent"] = False
    result["ok"] = bool(result["local_saved"]) and bool(result.get("feishu_sent"))
    return result


def main_legacy(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="交付 review 存档（feishu/web）")
    ap.add_argument("file_path")
    ap.add_argument("chat_id", nargs="?", default="")
    ap.add_argument("max_chars", nargs="?", type=int, default=DEFAULT_MAX)
    ap.add_argument("--channel", default=None)
    args = ap.parse_args(argv)
    text = Path(args.file_path).read_text(encoding="utf-8")
    r = deliver(text, channel=args.channel, name=Path(args.file_path).name, max_chars=args.max_chars)
    print(f"[delivery] {r}")
    return 0 if r["ok"] else 1

# ---------------------------------------------------------------------------
# card 2.0（markdown 组件：真标题层级/列表）；1.0 为兜底
# ---------------------------------------------------------------------------
def _md_to_elements_v2(md: str):
    els = []
    for b in _split_blocks(md):
        if b[0] == "md":
            # H1 已在卡片 header，正文去掉重复；其余原样交给 markdown 组件（真 ## / 列表）
            txt = "\n".join(l for l in b[1].split("\n") if not l.strip().startswith("# ")).strip()
            if txt:
                els.append(({"tag": "markdown", "content": txt}, len(txt)))
        else:
            _, header, rows = b
            cols = [{"name": f"c{j}", "display_name": (header[j] if j < len(header) else f"col{j}"),
                     "data_type": "text", "width": "auto"} for j in range(len(header))]
            trows = [{f"c{j}": (r[j] if j < len(r) else "") for j in range(len(header))} for r in rows]
            if cols and trows:
                size = sum(len(v) for row in trows for v in row.values())
                els.append(({"tag": "table", "columns": cols, "rows": trows,
                             "row_height": "low"}, size))
    return els


def feishu_build_cards_v2(md: str, title: str = "交付", max_chars: int = DEFAULT_MAX):
    els = _md_to_elements_v2(md)
    cards, cur, cur_size = [], [], 0
    for el, size in els:
        if cur and cur_size + size > max_chars:
            cards.append(cur); cur, cur_size = [], 0
        cur.append(el); cur_size += size
    if cur:
        cards.append(cur)
    cards = cards or [[]]
    n = len(cards)
    out = []
    for i, els_i in enumerate(cards, 1):
        t = title[:50] + (f"（{i}/{n}）" if n > 1 else "")
        out.append({"schema": "2.0", "config": {"wide_screen_mode": True},
                    "header": {"title": {"tag": "plain_text", "content": t}},
                    "body": {"elements": els_i or [{"tag": "markdown", "content": "(空)"}]}})
    return out

