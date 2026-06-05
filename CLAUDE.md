# CLAUDE.md

## 规则

1. **代码修改后必须自测** — 任何代码修改、新功能、bugfix 完成后，必须实际运行验证（启动服务→调用 API→检查 DB→确认页面渲染），确认无报错且输出符合预期后，才能向用户报告完成。禁止不测试就声称完成。
2. **工作区必须干净** — 不允许有 untracked 文件残留。遇到 untracked 文件要么 `git add` 入库，要么写入 `.gitignore`。禁止对 untracked 文件视而不见或声称 working tree clean。
3. **新图集站点使用独立脚本** — 每个站点一个 `<site>_dl.py` 放 `scripts/scrapers/` 下。脚本必须导出 `scrape(gallery_url: str) -> list[str]` 和 `download(gallery_url: str, out_dir: Path) -> int`。共享工具（headers、download_images）从 `.` 导入。在 `gallery_fetch.py` 中注册调度分支和 `_scrape_<site>` 包装函数。
4. **改名/改结构必须全局搜索验证** — 涉及字段名、函数名、变量名或数据结构变更时，必须执行三步：(a)改前 `grep -rn` 全项目列出所有引用点；(b)逐点修改；(c)改后再搜一遍确认生产代码 0 残留。禁止只改核心文件就报告完成。
5. **结论必须有证据支撑** — 对任何问题给出根因结论前，必须自查：(a)这个结论有代码/日志/测试结果直接支持吗？(b)能不能用一句话解释因果链路？如果答不上来，先做实验拿证据，不要猜。禁止用"可能是""应该是"等模糊措辞回避验证。
6. **print 错误必须同步写 error log** — 任何 `print(f"⚠️` 或 `print(f"❌` 的异常/错误信息，必须同时调用 `_log_db_error()` 写入 `data/logs/error-YYYY-MM-DD.log`。只打 print 不写 log 会导致 web UI 任务日志和 error log 都看不到失败原因。
7. **先复现再修 bug** — 对于任何 bug，禁止只根据错误描述就直接改代码。必须先写脚本复现问题，定位到确切根因后，再动手修。禁止"可能""应该是"式猜测后直接提交改动。
8. **禁止对 SQLite 二进制文件执行文本操作** — `data/*.db` 是二进制文件，严禁用 `patch`、`sed`、`awk`、`dd`、`cp --no-preserve` 等文本/字节替换命令直接操作。修改 DB 内容必须通过 `sqlite3` CLI 或 Python `sqlite3` 模块执行 SQL。违反此规则会破坏 B-tree 页结构，导致数据库不可恢复。
