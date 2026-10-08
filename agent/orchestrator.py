"""编排器 —— 加载 skill 提示词 + 调 LLM + 可选交付。

取代 Hermes 的 skill_view + cron。任务定义见 TASKS；提示词=仓库里的 SKILL.md。
"""
import argparse
import os
import sys
from pathlib import Path

from agent import llm
from services import paths

TASKS = {
    "daily-material-review": {
        "skill": "skills/creative/xhs-daily-material-review/SKILL.md",
        "instruction": (
            "执行每日素材 review：按 SKILL.md 的 4 层流程产出分级/价值建议/跨时间关联/"
            "发布回顾，最后输出 Markdown 存档（表格需符合表格铁律）。"
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

__all__ = ["build_messages", "run_task", "main"]


def load_skill_text(rel_path: str) -> str:
    p = paths.REPO_ROOT / rel_path
    return p.read_text(encoding="utf-8") if p.exists() else f"(缺 {rel_path})"


def build_messages(task: str, context: str = "") -> list[dict]:
    spec = TASKS.get(task)
    if not spec:
        raise ValueError(f"未知任务: {task}（可选: {', '.join(TASKS)}）")
    system = ("你是小红书内容流水线的编排器。严格遵守下面的 skill 规范，"
              "不确定就按规范里的默认处理，不要臆造。\n\n=== SKILL ===\n"
              + load_skill_text(spec["skill"]))
    user = spec["instruction"]
    if context:
        user += "\n\n=== 上下文 ===\n" + context
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def run_task(task: str, context: str = "", chat_fn=None) -> str:
    messages = build_messages(task, context)
    return (chat_fn or llm.chat)(messages)


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ap = argparse.ArgumentParser(description="项目内置编排器")
    ap.add_argument("--task", required=True, choices=sorted(TASKS))
    ap.add_argument("--context-file", default=None)
    ap.add_argument("--dry-run", action="store_true", help="只打印组装好的 prompt，不调 LLM")
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

    out = run_task(args.task, context)
    print(out)
    if args.deliver:
        from services.delivery import deliver
        print("[delivery]", deliver(out, name=args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
