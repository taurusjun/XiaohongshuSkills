"""Skill 一致性审计：把 skill 文档逐条抽出规则，用 GLM 判「实现是否一致」。

流程：extract_items（GLM 抽条目）→ gather_evidence（仓库关键词取证）→ judge（GLM 逐条判）→ render。
审计用 GLM（不同厂商），不用被审的 DeepSeek。
"""
import json
import re
from pathlib import Path

from services import glm_client as _glm

__all__ = ["extract_items", "dedup", "classify", "judge_all", "render", "run"]

_EXTRACT_SYS = "你是 skill 文档结构化助手。只输出严格 JSON。"
_EXTRACT_TPL = """以下是一个「小红书写稿/发布」skill 文档的第 {i}/{n} 段（行 {a}-{b}）。
请把它拆成**可逐条审计的规则条目**。只抽「规则 / 判据 / 铁律 / 禁止项 / 教训结论」；
纯背景介绍、操作命令、工具用法、示例代码**不要抽**。

每条格式：
{{"rule": "<规则内容摘要，≤80字>", "expectation": "<期望/判定标准，≤40字>"}}

只输出 JSON：{{"items": [ ... ]}}"""

_JUDGE_SYS = "你是严格的代码审计员。只输出严格 JSON。"
_JUDGE_TPL = """判断「实现」是否满足「skill 规则」。

【skill 规则】{rule}
【期望】{expectation}

【实现证据（从仓库按关键词检索的片段）】
{evidence}

请判断，只输出 JSON：
{{"verdict":"一致|部分一致|偏离|缺失|不适用","impl_loc":"<最相关的文件:行>","reason":"<一句话依据>"}}
说明：
- 一致：实现完整满足该规则
- 部分一致：实现了但缺细节/有例外
- 偏离：实现与规则冲突
- 缺失：未找到相关实现
- 不适用：该规则属于「人工 agent 流程」，自动化流水线不需要（如工具调用纪律/read_file 流程）"""


def _chunks(text: str, size: int = 160):
    lines = text.splitlines()
    out = []
    for i in range(0, len(lines), size):
        seg = lines[i:i + size]
        out.append((i + 1, i + len(seg), "\n".join(seg)))
    return out


def extract_items(skill_path, size: int = 160) -> list[dict]:
    text = Path(skill_path).read_text(encoding="utf-8")
    ch = _chunks(text, size)
    items = []
    for i, (a, b, seg) in enumerate(ch, 1):
        try:
            d = _glm.chat_json(_EXTRACT_TPL.format(i=i, n=len(ch), a=a, b=b) + "\n\n---\n" + seg,
                               system=_EXTRACT_SYS, max_tokens=3000)
        except Exception as e:  # noqa: BLE001
            print(f"  [extract] 第{i}段失败: {e}")
            continue
        for it in (d.get("items") or []):
            r = (it.get("rule") or "").strip()
            if not r:
                continue
            items.append({"id": len(items) + 1, "lines": f"{a}-{b}", "rule": r,
                          "expectation": (it.get("expectation") or "").strip()})
    return items


_STOP = set("的 了 是 在 和 与 或 不 要 必须 所有 每条 以及 一个 这个 那个 可以 需要 使用 进行 通过 规则 要求 禁止".split())


def _keywords(rule: str, k: int = 8) -> list[str]:
    toks = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}|[\u4e00-\u9fff]{2,4}", rule or "")
    out = []
    for t in toks:
        if t in _STOP or t in out:
            continue
        out.append(t)
        if len(out) >= k:
            break
    return out


def gather_evidence(rule: str, repo_root, limit: int = 5) -> str:
    """按关键词在 agent/services/config/scripts 里检索，返回片段（文件:行 + 内容）。"""
    root = Path(repo_root)
    dirs = ["agent", "services", "config", "scripts", "web"]
    hits = []
    for kw in _keywords(rule):
        for d in dirs:
            p = root / d
            if not p.exists():
                continue
            for f in p.rglob("*.py" if d != "config" else "*"):
                if not f.is_file() or ".venv" in str(f) or "__pycache__" in str(f):
                    continue
                try:
                    txt = f.read_text(encoding="utf-8", errors="ignore")
                except Exception:  # noqa: BLE001
                    continue
                if kw in txt:
                    for ln, line in enumerate(txt.splitlines(), 1):
                        if kw in line:
                            hits.append((kw, f"{f.relative_to(root)}:{ln}", line.strip()[:120]))
                            break
        if len(hits) >= limit * 2:
            break
    seen, out = set(), []
    for kw, loc, line in hits:
        if loc in seen:
            continue
        seen.add(loc)
        out.append(f"[{loc}] {line}")
        if len(out) >= limit:
            break
    return "\n".join(out) if out else "(未检索到相关实现)"


def judge(item: dict, evidence: str) -> dict:
    try:
        d = _glm.chat_json(_JUDGE_TPL.format(rule=item["rule"], expectation=item.get("expectation", ""),
                                             evidence=evidence),
                           system=_JUDGE_SYS, max_tokens=1500)
    except Exception as e:  # noqa: BLE001
        return {"verdict": "ERR", "impl_loc": "", "reason": str(e)[:80]}
    return {"verdict": (d.get("verdict") or "ERR").strip(),
            "impl_loc": (d.get("impl_loc") or "").strip(),
            "reason": (d.get("reason") or "").strip()}


def run(skill_path, repo_root, out_json, size: int = 160):
    items = extract_items(skill_path, size=size)
    print(f"[skill_audit] 抽出 {len(items)} 条")
    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    return items


_CLS_SYS = "你是规则分类器。只输出严格 JSON。"
_CLS_TPL = """以下是若干条 skill 规则。逐条判断属于哪类：
- "code"：可在代码/prompt/config 里核对的（字数/假名/新字体/字段/评分/渠道/术语/密度/关联/标记/发布字段…）
- "process"：人工 agent 的操作流程与工具纪律（read_file/工具调用次数/汇报时机/前缀位数/查存档/请示用户…）

{listing}

只输出 JSON：{{"<序号>": "code|process", ...}}"""


def dedup(items: list[dict]) -> list[dict]:
    """按规则文本归一化去重（保留首次出现，合并行号）。"""
    import re as _re
    seen, out = {}, []
    for it in items:
        k = _re.sub(r"[\s\W]+", "", it.get("rule") or "")
        if not k or k in seen:
            if k in seen:
                seen[k]["lines"] = seen[k]["lines"] + "," + it.get("lines", "")
            continue
        seen[k] = it
        out.append(it)
    for i, it in enumerate(out, 1):
        it["id"] = i
    return out


def classify(items: list[dict], batch: int = 20) -> None:
    """给每条打 kind=code|process（原地改）。"""
    for i in range(0, len(items), batch):
        seg = items[i:i + batch]
        listing = "\n".join("%d. %s" % (j + 1, x["rule"][:100]) for j, x in enumerate(seg))
        try:
            d = _glm.chat_json(_CLS_TPL.format(listing=listing), system=_CLS_SYS, max_tokens=2000)
        except Exception as e:  # noqa: BLE001
            print(f"  [classify] 第{i//batch+1}批失败: {e}")
            d = {}
        for j, x in enumerate(seg, 1):
            x["kind"] = str(d.get(str(j), "")).strip() or "code"


def judge_all(items: list[dict], repo_root) -> None:
    """对 kind=code 的逐条判定；process 直接标「不适用」。"""
    for it in items:
        if it.get("kind") == "process":
            it["verdict"] = "不适用"
            it["impl_loc"] = ""
            it["reason"] = "人工 agent 流程（自动化流水线已取代）"
            continue
        ev = gather_evidence(it["rule"], repo_root)
        j = judge(it, ev)
        it["verdict"] = j["verdict"]
        it["impl_loc"] = j["impl_loc"]
        it["reason"] = j["reason"]
        it["evidence"] = ev
        print("  #%s %s | %s" % (it["id"], it["verdict"], it["rule"][:40]), flush=True)


def render(items: list[dict], skill_name: str, date: str) -> str:
    order = {"偏离": 0, "缺失": 1, "部分一致": 2, "ERR": 3, "一致": 4, "不适用": 5}
    items = sorted(items, key=lambda x: order.get(x.get("verdict", ""), 9))
    cnt = {}
    for x in items:
        cnt[x.get("verdict", "?")] = cnt.get(x.get("verdict", "?"), 0) + 1
    lines = [f"# Skill 一致性审计 — {skill_name}", f"",
             f"日期：{date}　审计模型：GLM（{_glm._model()}）　条目：{len(items)}", "",
             "## 汇总", ""]
    for k in ("一致", "部分一致", "偏离", "缺失", "不适用", "ERR"):
        if cnt.get(k):
            lines.append(f"- {k}：{cnt[k]}")
    lines += ["", "## 逐条对齐表", "",
              "| # | skill 规则 | 期望 | 实现位置 | 结论 | 理由 |", "|---|---|---|---|---|---|"]
    for x in items:
        lines.append("| {id} | {rule} | {exp} | {loc} | **{v}** | {r} |".format(
            id=x.get("id"), rule=(x.get("rule") or "")[:70], exp=(x.get("expectation") or "")[:30],
            loc=(x.get("impl_loc") or "—"), v=x.get("verdict"), r=(x.get("reason") or "")[:60]))
    lines += ["", "---", "> 「一致」条目已省略（见 JSON）。完整清单：`config/skill_audit/items.json`。"]
    return "\n".join(lines)
