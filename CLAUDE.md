# CLAUDE.md

## 规则

1. **代码修改后必须自测** — 任何代码修改、新功能、bugfix 完成后，必须实际运行验证（启动服务→调用 API→检查 DB→确认页面渲染），确认无报错且输出符合预期后，才能向用户报告完成。禁止不测试就声称完成。
2. **工作区必须干净** — 不允许有 untracked 文件残留。遇到 untracked 文件要么 `git add` 入库，要么写入 `.gitignore`。禁止对 untracked 文件视而不见或声称 working tree clean。
3. **新图集站点使用独立脚本** — 每个站点一个 `<site>_dl.py` 放 `scripts/scrapers/` 下。脚本必须导出 `scrape(gallery_url: str) -> list[str]` 和 `download(gallery_url: str, out_dir: Path) -> int`。共享工具（headers、download_images）从 `.` 导入。在 `gallery_fetch.py` 中注册调度分支和 `_scrape_<site>` 包装函数。
4. **改名/改结构必须全局搜索验证** — 涉及字段名、函数名、变量名或数据结构变更时，必须执行三步：(a)改前 `grep -rn` 全项目列出所有引用点；(b)逐点修改；(c)改后再搜一遍确认生产代码 0 残留。禁止只改核心文件就报告完成。
5. **结论必须有证据支撑** — 对任何问题给出根因结论前，必须自查：(a)这个结论有代码/日志/测试结果直接支持吗？(b)能不能用一句话解释因果链路？如果答不上来，先做实验拿证据，不要猜。禁止用"可能是""应该是"等模糊措辞回避验证。
6. **print 错误必须同步写 error log** — 任何 `print(f"⚠️` 或 `print(f"❌` 的异常/错误信息，必须同时调用 `_log_db_error()` 写入 `data/logs/error-YYYY-MM-DD.log`。只打 print 不写 log 会导致 web UI 任务日志和 error log 都看不到失败原因。
7. **先复现再修 bug** — 对于任何 bug，禁止只根据错误描述就直接改代码。必须先写脚本复现问题，定位到确切根因后，再动手修。禁止"可能""应该是"式猜测后直接提交改动。
8. **所有代码修改必须在远程服务器上操作** — 包括改文件、新增文件、删除文件，一律通过 SSH 在 `user@192.168.0.70`（项目路径 `/Users/user/PG/XiaohongshuSkills`）上直接操作。禁止在本地修改后 scp/rsync 上传，或在本地提交再 push。提交和 push 也在远程执行。
9. **禁止对 SQLite 二进制文件执行文本操作** — `data/*.db` 是二进制文件，严禁用 `patch`、`sed`、`awk`、`dd`、`cp --no-preserve` 等文本/字节替换命令直接操作。修改 DB 内容必须通过 `sqlite3` CLI 或 Python `sqlite3` 模块执行 SQL。违反此规则会破坏 B-tree 页结构，导致数据库不可恢复。

## 图集抓取架构（scrape_gallery_images 调度）

**入口**：`scripts/gallery_fetch.py` 的 `scrape_gallery_images(gallery_url)`。流程：`domain = _domain_of(url)` → 按域名走三档处理 → 返回图片 URL 列表。

**三档处理（从专用到通用）**：
1. **专用 scraper（复杂站点）**：dispatch 里一串 `if "<domain>" in domain: images = _scrape_<site>(url); return images`，每分支固定 `print("  📷 抓到 N 张图片")`。用于需要翻页 / SPA / WAF / JSON 接口的站点（natalie、ddnavi、mdpr、nikkansports…）。
2. **selector 字典（简单站点）**：`GALLERY_SITES: dict[域名 -> CSS 选择器]`。不写脚本，只加一行；dispatch 末尾的通用路径用该 selector 在容器内找 img，经 skip_kw/ext 过滤、`_to_large_url()` 把缩略图升级为原图。
3. **未登记域名**：落到默认 selector `"article, body"` 全页尽力扫，常抓不准或抓 0（即“无法识别/抓不到”的表现）。

**新增站点怎么选档**：
- 图在固定容器、无翻页无反爬 → **加 `GALLERY_SITES` 一行**即可（最省）。
- 有翻页 / SPA / WAF / JSON 接口 / 特殊 URL 规则 → **写专用脚本**（见规则 #3）：`scripts/scrapers/<site>_dl.py` 导出 `scrape(url)->list[str]` 与 `download(url, out_dir)->int`；共享工具从 `.`（`scrapers/__init__.py`）导入：`download_images`、`cdp_page_html`、`IMG_HEADERS`；再在 `gallery_fetch.py` 加 `_scrape_<site>` 包装 + dispatch 分支。

**通用约定 / 工具**：
- **代理**：出站 `requests.get(..., proxies=_get_proxies())`；`_get_proxies()` 读 `scripts/.env` 的 `HTTP_PROXY`（当前 `http://127.0.0.1:20809`，爬虫与 TG gateway 同一个代理）。代理会闪断，下载失败先怀疑代理（直连/重试能否好）。
- **反爬 / SPA 兜底**：requests 被 WAF（405 / Human Verification）或拿到 SPA 空壳时，走 `cdp_page_html(url, port=9222)` —— 用那台开着 9222 远程调试、已通过人机验证的 Chrome（profile `~/Google/Chrome/XiaohongshuProfiles/default`）。WAF cookie 过期需人工在该 Chrome 里重新验证一次，无法纯自动绕过。
- **张数上限**：`MAX_IMAGES`（默认 20，`--max-images` 可覆盖）。
- **缩略图 → 原图**：用 `_to_large_url()`；或去掉 CDN 尺寸/缩略参数（如 natalie 去 `?impolicy=thumb...`）。
- **落盘目录**：`CACHE_DIR/<key>`，`key` 取自 DB `news.key`（`data/news_dev.db`）；下载完回写 `news.gallery_images`。
- **错误处理**：错误 `print(⚠️/❌)` 必须同时 `_log_db_error()`（见规则 #6），该函数在 `scripts/sqlite_db.py`。

**排障三分法（下不下来先定性）**：
1. **代理闪断** → 换直连/重试能好；
2. **站点反爬**（WAF / SPA）→ 需 CDP + 已验证的 9222 Chrome；
3. **边缘 404 / 地域封锁**（如 moviewalker：CloudFront 对非日本出口连首页都 404）→ 需日本出口 IP，当前环境抓不到。

**已知站点要点**：
- natalie 等 Vue SPA：按 `/gallery/news/<galleryId>/<photoId>` 锚点锁定本图集照片，**别**按目录或文件名前缀（同目录混着当天其他文章的图）。
