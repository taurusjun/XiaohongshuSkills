## Why

Claude Code 在回答运营相关问题时（「今天有哪些候选文章？」「上周哪个话题效果最好？」「帮我纠正这篇文章的原创度评分」）需要读写项目 SQLite 数据库，但目前只能通过 Bash 运行 Python 脚本间接操作，既低效又容易出错。将核心运营操作封装为 MCP server，让 Claude Code 可以直接调用工具，同时为未来的 Feishu Bot 和 Web UI 提供统一的数据访问层。

## What Changes

- **新增** `xhs-operations` MCP server：封装文章候选管理、维度版本管理、话题效果查询、评分纠正等核心运营操作
- **新增** `xhs-llm` MCP server：将 LiteLLM 的不同用途（翻译、评分、内容生成、图片评分、override 分析）封装为语义化工具，强制每个工具使用正确的 temperature 和 JSON schema
- **新增** `.claude/settings.json`：注册两个 MCP server，Claude Code 对话中可直接调用

## Capabilities

### New Capabilities

- `xhs-operations-server`：基于 SQLite 的运营数据 MCP server，支持文章管理、维度版本、话题效果、评分纠正
- `xhs-llm-server`：LiteLLM 语义化封装 MCP server，每个工具有独立 temperature、JSON schema 约束和 retry 机制

### Modified Capabilities

- `scoring-pipeline`：通过 `xhs-llm` MCP 调用 `evaluate_content` 工具，temperature 统一为 0.1，不再硬编码 0.7

## Impact

**新增文件：**
- `mcp/xhs_operations_server.py`：xhs-operations MCP server 实现（FastMCP 框架）
- `mcp/xhs_llm_server.py`：xhs-llm MCP server 实现
- `.claude/settings.json`：MCP server 注册配置

**修改文件：**
- `scripts/yahoo_common.py`：`call_litellm` 支持 `temperature` 参数（默认保持 0.7，evaluate_quality 调用时传 0.1）
