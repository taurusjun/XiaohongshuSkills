## Why

当前 SKILL.md（`RedBookSkills`）只覆盖手动发布操作，没有覆盖运营智能体的日常操作场景（查看候选文章、运行反思分析、纠正评分维度、查看周报数据）。随着 xhs-smart-operations-agent 的上线，运营者需要通过自然语言与智能体交互，而不是手动跑 Python 脚本。同时，原有 SKILL 的 trigger 太窄，失败处理缺少 LLM 调用失败场景。

## What Changes

- **修改** 现有 `RedBookSkills`（SKILL.md）：收窄 scope 为发布操作，扩宽 trigger 覆盖，补充 LiteLLM 失败处理路径
- **新增** `RedBookOps` SKILL：覆盖运营智能体的日常操作场景，通过 `xhs-operations` MCP 读写数据，提供有数据支撑的回答

## Capabilities

### New Capabilities

- `xhs-ops-skill`：运营智能体操作 SKILL，trigger 覆盖「查候选/运行反思/纠正评分/查周报/调整权重/看今日规划」等场景

### Modified Capabilities

- `redbook-publish-skill`：现有 SKILL 收窄 scope、扩宽 trigger、补充 LLM 失败处理

## Impact

**修改文件：**
- `SKILL.md`：更新现有 RedBookSkills 定义

**新增文件：**
- `.claude/skills/xhs-ops`：RedBookOps SKILL 定义文件（依赖 xhs-operations MCP）
