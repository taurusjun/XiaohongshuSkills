# CLAUDE.md

## 规则

1. **代码修改后必须自测** — 任何代码修改、新功能、bugfix 完成后，必须实际运行验证（启动服务→调用 API→检查 DB→确认页面渲染），确认无报错且输出符合预期后，才能向用户报告完成。禁止不测试就声称完成。
2. **工作区必须干净** — 不允许有 untracked 文件残留。遇到 untracked 文件要么 `git add` 入库，要么写入 `.gitignore`。禁止对 untracked 文件视而不见或声称 working tree clean。
