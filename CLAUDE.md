# CLAUDE.md

## 规则

1. **代码修改后必须自测** — 任何代码修改、新功能、bugfix 完成后，必须实际运行验证（启动服务→调用 API→检查 DB→确认页面渲染），确认无报错且输出符合预期后，才能向用户报告完成。禁止不测试就声称完成。
2. **工作区必须干净** — 不允许有 untracked 文件残留。遇到 untracked 文件要么 `git add` 入库，要么写入 `.gitignore`。禁止对 untracked 文件视而不见或声称 working tree clean。
3. **新图集站点使用独立脚本** — 每个站点一个 `<site>_dl.py` 放 `scripts/scrapers/` 下。脚本必须导出 `scrape(gallery_url: str) -> list[str]` 和 `download(gallery_url: str, out_dir: Path) -> int`。共享工具（headers、download_images）从 `.` 导入。在 `gallery_fetch.py` 中注册调度分支和 `_scrape_<site>` 包装函数。
4. **改名/改结构必须全局搜索验证** — 涉及字段名、函数名、变量名或数据结构变更时，必须执行三步：(a)改前 `grep -rn` 全项目列出所有引用点；(b)逐点修改；(c)改后再搜一遍确认生产代码 0 残留。禁止只改核心文件就报告完成。
