## MODIFIED Requirements

### Requirement: RedBookSkills trigger 扩宽，补充 LLM 失败处理路径
现有 SKILL SHALL 更新 trigger 覆盖更广的发布意图，并补充 LiteLLM 调用失败时的明确处理路径。

**更新后的 trigger 场景（扩宽）：**
- 发布操作：`发布到小红书|发图文|发视频|post to xhs|推送内容`
- 检索操作：`搜索笔记|查看评论|看首页推荐|获取笔记详情`
- 账号管理：`检查登录|获取登录二维码|查看账号数据`

**新增 LLM 失败处理路径：**

#### Scenario: LiteLLM API key 失效
- **WHEN** 发布流程中内容生成步骤报 401/403 错误
- **THEN** SKILL 告知「LiteLLM API key 失效，请检查 `scripts/.env` 中的 `LITELLM_API_KEY`」，不继续发布流程

#### Scenario: LiteLLM quota 耗尽
- **WHEN** 内容生成报 429 错误
- **THEN** SKILL 告知「LiteLLM quota 已耗尽，可选择：(1) 等待 quota 恢复 (2) 手动编写内容后提供给发布命令」

#### Scenario: 模型不支持 JSON mode
- **WHEN** `response_format=json_object` 返回错误
- **THEN** SKILL 自动降级为 text 模式重试，并警告「当前模型不支持 JSON mode，评分结果稳定性可能下降」
