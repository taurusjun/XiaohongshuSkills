"""编排器 —— 加载 skill 提示词 + 调 LLM + 可选交付。

取代 Hermes 的 skill_view + cron。任务定义见 TASKS；提示词=仓库里的 SKILL.md。
"""
import argparse
import json
import os
import sys
from pathlib import Path

from agent import llm
from agent import tools as _tools
from services import paths

TASKS = {
    "daily-material-review": {
        "skills": [
            "skills/creative/xhs-daily-material-review/SKILL.md",
            "skills/creative/xhs-daily-material-review-layer1/SKILL.md",
            "skills/xhs-daily-material-review-layer23/SKILL.md",
            "skills/xhs-daily-material-review-layer4/SKILL.md",
        ],
        "instruction": (
            "执行每日素材 review。最终**只输出一份完整的 Markdown 存档**，格式严格如下（对齐历史存档）：\n"
            "# 每日素材 Review — <YYYY-MM-DD>（东京时间）\n"
            "（元信息块：データ範囲 / 查询 / fetch_by 分布 / 完成度 / 巡检）\n"
            "---\n"
            "## 一、全量素材一览（按来源分组）  … 用 Markdown 表格（表头前 ## 且表前空行、列数一致）\n"
            "## 二、分级（S/A/AKB大TOP/B/C）\n## 三、价值建议与跨时间关联\n## 四、发布数据回顾\n\n"
            "硬要求：① 最终消息**必须以 `# 每日素材 Review` 开头**，**绝不能是 JSON**；"
            "调用工具（news_list/news_get 等）拿到的 JSON 只是中间数据，不要原样输出。"
            "② 所有表格符合表格铁律。③ 全中文。"
        ),
    },
    "write": {
        "skill": "skills/creative/xhs-write-publish-flow/SKILL.md",
        "instruction": (
            "执行小红书当日写稿：按 SKILL.md 6 阶段管道，覆盖 S/A/AKB大TOP 素材，"
            "入库前过机械门禁（services.precheck），最后给「待发布推荐」5 篇。"
        ),
    },
}

__all__ = ["build_messages", "run_task", "run_agent", "main"]


def load_skill_text(rel_path: str) -> str:
    p = paths.REPO_ROOT / rel_path
    return p.read_text(encoding="utf-8") if p.exists() else f"(缺 {rel_path})"


def _skill_paths(spec: dict):
    if spec.get("skills"):
        return spec["skills"]
    return [spec["skill"]]


def build_messages(task: str, context: str = "") -> list[dict]:
    spec = TASKS.get(task)
    if not spec:
        raise ValueError(f"未知任务: {task}（可选: {', '.join(TASKS)}）")
    blocks = "\n\n".join(f"=== SKILL: {sp} ===\n" + load_skill_text(sp) for sp in _skill_paths(spec))
    system = ("你是小红书内容流水线的编排器。严格遵守下面的 skill 规范，"
              "不确定就按规范里的默认处理，不要臆造。\n\n" + blocks)
    user = spec["instruction"]
    if context:
        user += "\n\n=== 上下文 ===\n" + context
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run_task(task: str, context: str = "", chat_fn=None) -> str:
    messages = build_messages(task, context)
    return (chat_fn or llm.chat)(messages)


def run_agent(task: str, context: str = "", chat_raw_fn=None, max_iters: int = 8,
              tools=None) -> str:
    """函数调用循环：LLM 可反复调用 tools（services 的能力），直到给出最终文本。"""
    chat_raw_fn = chat_raw_fn or llm.chat_raw
    schemas = _tools.TOOL_SCHEMAS if tools is None else tools
    messages = build_messages(task, context)
    for _ in range(max_iters):
        msg = chat_raw_fn(messages, tools=schemas)
        if not isinstance(msg, dict):
            return str(msg)
        msg.setdefault("role", "assistant")
        messages.append(msg)
        tcs = msg.get("tool_calls")
        if not tcs:
            return msg.get("content", "") or ""
        for tc in tcs:
            fn = (tc.get("function") or {})
            name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            result = _tools.dispatch(name, args)
            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "content": json.dumps(result, ensure_ascii=False)})
    # 达到上限仍在调工具：禁用工具强制要一次文本终稿
    try:
        final = chat_raw_fn(messages, tools=None)
        if isinstance(final, dict):
            return final.get("content", "") or ""
    except Exception:
        pass
    return messages[-1].get("content", "") or ""


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="项目内置编排器")
    ap.add_argument("--task", required=True, choices=sorted(TASKS))
    ap.add_argument("--context-file", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只打印组装好的 prompt，不调 LLM")
    ap.add_argument("--agent", action="store_true", help="用工具循环（function calling）而非单轮")
    ap.add_argument("--deliver", action="store_true", help="把结果交付（本地落盘+按需飞书）")
    ap.add_argument("--name", default="latest.md")
    args = ap.parse_args(argv)

    context = ""
    if args.context_file:
        context = Path(args.context_file).read_text(encoding="utf-8")

    if args.dry_run:
        msgs = build_messages(args.task, context)
        print(f"[dry-run] task={args.task} messages={len(msgs)} "
              f"system_chars={len(msgs[0]['content'])} user_chars={len(msgs[1]['content'])}")
        print("--- user ---")
        print(msgs[1]["content"])
        return 0

    out = run_agent(args.task, context) if args.agent else run_task(args.task, context)
    print(out)
    if args.deliver:
        if out.strip().startswith(("{", "[")):
            print("[delivery] 拒发：输出疑似 JSON（非 Markdown 存档）", file=sys.stderr)
            return 3
        from services.delivery import deliver
        print("[delivery]", deliver(out, name=args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
