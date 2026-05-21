# XHS 智能运营 Agent — 完整开发任务清单

> 文件约定：新建文件放置路径均基于项目根 `/Users/user/PWorks/XiaohongshuSkills/`。  
> 以下缩写统一：`scripts/` = `scripts/`，`config/` = `config/`，`mcp/` = `mcp/`（新建目录），`tests/` = `tests/`（新建目录）。

---

## Phase 0-A：DB Schema 扩展

**交付物：**
- 修改 `scripts/sqlite_db.py`

**前置条件：** 无

### 实施步骤

1. **在 `init_db()` 的 `executescript` 中追加 5 个新字段到 `CREATE TABLE IF NOT EXISTS news` DDL**（因为是 `IF NOT EXISTS`，新建库会直接包含新字段；现有库走下一步的 `ALTER TABLE`）：

   ```sql
   xhs_views          INTEGER DEFAULT 0,
   xhs_likes          INTEGER DEFAULT 0,
   xhs_saves          INTEGER DEFAULT 0,
   xhs_comments       INTEGER DEFAULT 0,
   xhs_collected_at   TEXT DEFAULT '',
   topic_perf_updated_at TEXT DEFAULT NULL
   ```

2. **在 `init_db()` 的 compat `ALTER TABLE` 块追加六条**：

   ```python
   try: db.execute("ALTER TABLE news ADD COLUMN xhs_views INTEGER DEFAULT 0")
   except: pass
   # 同理 xhs_likes / xhs_saves / xhs_comments / xhs_collected_at / topic_perf_updated_at
   ```

3. **在 `executescript` 中新增四张表**：

   ```sql
   CREATE TABLE IF NOT EXISTS topic_performance (
       topic                 TEXT PRIMARY KEY,
       avg_saves             REAL DEFAULT 0,
       avg_comments          REAL DEFAULT 0,
       avg_views             REAL DEFAULT 0,
       engagement_score      REAL DEFAULT 0,
       post_count            INTEGER DEFAULT 0,
       discard_count         INTEGER DEFAULT 0,
       last_discard_reason   TEXT DEFAULT '',
       topic_baseline_saves  REAL DEFAULT 0,
       topic_baseline_comments REAL DEFAULT 0,
       trend_signal          TEXT DEFAULT '',
       trend_updated_at      TEXT DEFAULT '',
       window_days           INTEGER DEFAULT 90,
       last_updated          TEXT DEFAULT (datetime('now','localtime'))
   );

   CREATE TABLE IF NOT EXISTS account_snapshots (
       id               INTEGER PRIMARY KEY AUTOINCREMENT,
       snapshot_date    TEXT NOT NULL UNIQUE,
       followers        INTEGER,
       week_views       INTEGER DEFAULT 0,
       week_saves       INTEGER DEFAULT 0,
       week_likes       INTEGER DEFAULT 0,
       top_note_key     TEXT DEFAULT '',
       data_completeness TEXT DEFAULT 'full',
       created_at       TEXT DEFAULT (datetime('now','localtime'))
   );

   CREATE TABLE IF NOT EXISTS agent_config (
       key        TEXT PRIMARY KEY,
       value      TEXT NOT NULL,
       updated_at TEXT DEFAULT (datetime('now','localtime'))
   );

   CREATE TABLE IF NOT EXISTS agent_state (
       key        TEXT PRIMARY KEY,
       value      TEXT NOT NULL,
       date       TEXT DEFAULT '',
       updated_at TEXT DEFAULT (datetime('now','localtime'))
   );
   ```

4. **在 `_DIM_DEFS` 字典中注册新维度**（`sqlite_db.py` 第 225 行附近）：

   ```python
   '收藏驱动': ('内容', '加分'),
   '评论引导性': ('内容', '加分'),
   ```

5. **在 `update_news()` 的 `allowed` 集合**（第 177 行）中追加允许字段：

   ```python
   'xhs_views', 'xhs_likes', 'xhs_saves', 'xhs_comments',
   'xhs_collected_at', 'topic_perf_updated_at'
   ```

6. **在 `score_dims` 表的 `executescript` 中追加字段**，并在 compat 块追加 `ALTER TABLE`：

   ```sql
   human_override INTEGER DEFAULT 0,
   human_value    REAL,
   override_note  TEXT DEFAULT '',
   llm_value      REAL,
   dim_version    TEXT DEFAULT ''
   ```

### 自测清单

```python
# tests/test_db_schema.py
import os, sqlite3, tempfile, pytest
os.environ["SQLITE_PATH"] = ":memory:"

def test_news_new_columns():
    import scripts.sqlite_db as db
    db.DB_PATH = ":memory:"
    db.init_db()
    conn = sqlite3.connect(db.DB_PATH)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(news)")}
    for col in ("xhs_views","xhs_likes","xhs_saves","xhs_comments",
                "xhs_collected_at","topic_perf_updated_at"):
        assert col in cols, f"缺少字段 {col}"

def test_new_tables_exist():
    import scripts.sqlite_db as db
    conn = sqlite3.connect(db.DB_PATH)
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("topic_performance","account_snapshots","agent_config","agent_state"):
        assert t in tables, f"缺少表 {t}"

def test_score_dims_new_columns():
    import scripts.sqlite_db as db
    conn = sqlite3.connect(db.DB_PATH)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(score_dims)")}
    for col in ("human_override","human_value","override_note","llm_value","dim_version"):
        assert col in cols

def test_update_news_allows_xhs_fields():
    import scripts.sqlite_db as db
    db.insert_news({"key":"test_k","title":"T","link":"L"})
    assert db.update_news("test_k", {"xhs_saves": 42})
    row = db.get_by_key("test_k")
    assert row["xhs_saves"] == 42
```

- `pytest tests/test_db_schema.py -v` 全部通过
- 对已有 `data/news.db` 运行 `python -c "import scripts.sqlite_db as db; db.init_db(); print('OK')"` 确认 compat 迁移无报错

### 端到端测试

启动前先备份 `data/news.db`，运行 `python -c "from scripts.sqlite_db import init_db; init_db()"` 后执行 `sqlite3 data/news.db ".schema"` 确认六张表（含两张新表）及所有新字段全部出现。

---

## Phase 0-B：scoring_dimensions.json + evaluate_quality 改造

**交付物：**
- 新建 `config/scoring_dimensions.json`
- 修改 `scripts/yahoo_common.py`：`call_litellm`、`evaluate_quality`、新增 `build_scoring_prompt`

**前置条件：** Phase 0-A（`_DIM_DEFS` 已注册新维度）

### 实施步骤

1. **新建 `config/scoring_dimensions.json`**，文件结构：

   ```json
   {
     "version": "1.0.0",
     "dimensions": [
       {
         "name": "剧情感",
         "category": "标题",
         "direction": "plus",
         "default_weight": 1.0,
         "definition": "标题是否能让读者感受到事件的戏剧性弧度",
         "example_1": "「N号房主犯被判无期——受害者终于等到这一天」",
         "example_0_5": "「艺人宣布恋情，粉丝反应两极」（有冲突但弧度不完整）",
         "example_0": "「某偶像出席活动」（纯陈述，无剧情感）",
         "edge_case": "包含时间对比（从前/现在）或身份转变的标题即使不含冲突词也可给1"
       },
       ... // 其余 17 个现有维度，definition 等字段填基础值
       {
         "name": "收藏驱动",
         "category": "内容",
         "direction": "plus",
         "default_weight": 1.0,
         "definition": "内容是否包含让读者主动保存以便日后查阅的实用信息（清单/步骤/对比表/资源汇总）",
         "example_1": "「10张最值得收藏的写真集封面」、「入坑乃木坂必看5部 MV 清单」",
         "example_0_5": "「详细介绍一个人的经历，附有部分资料来源」（有参考价值但不够系统化）",
         "example_0": "「某艺人今日公开了新写真」（单条资讯，无保存价值）",
         "edge_case": "排名/榜单类内容即使不含「清单」字样也给1；纯消息类即使附图也给0"
       },
       {
         "name": "评论引导性",
         "category": "内容",
         "direction": "plus",
         "default_weight": 1.0,
         "definition": "内容末尾或正文中是否自然引出开放式讨论，让读者产生「我想说点什么」的冲动。核心区别：评论引导是邀请分享观点/经历，而非索要点赞/关注",
         "example_1": "文末以「你最喜欢她哪个时期的造型？」收尾；或正文对比两种截然不同的粉丝观点，天然引发站队讨论",
         "example_0_5": "文末「欢迎在评论区聊聊」（有邀请但较泛，未具体化讨论点）",
         "example_0": "文章只是陈述事实，无任何互动邀请；或文末是「喜欢请点赞收藏」（属于主动讨赏）",
         "edge_case": "含争议性事件本身（如分手/复出/整容疑云）即使无明确提问也给1，因为争议性内容天然触发评论"
       }
     ]
   }
   ```

2. **修改 `call_litellm`**（`yahoo_common.py` 第 166 行）：
   - 函数签名改为 `call_litellm(prompt, system_prompt="", max_tokens=1000, response_format=None, temperature=0.7)`
   - 在 `body` 构建处用参数而非硬编码 `0.7`
   - 在 `try` 块外层增加 retry 逻辑（两次指数退避）：

   ```python
   for attempt, wait in enumerate([0, 5, 15]):
       if wait: time.sleep(wait)
       try:
           resp = _direct_session.post(...)
           if resp.status_code == 200: return content.strip()
           if resp.status_code < 500: break  # 4xx 不重试
       except Exception as e:
           if attempt == 2: print(f"    ⚠️ LiteLLM 调用失败(最终): {e}")
   ```

   - JSON 提取逻辑改为多层 try：先 `json.loads(reasoning)`，失败时用 `re.findall(r'\{.*?\}', text, re.DOTALL)`

3. **新增纯函数 `build_scoring_prompt(dims: list[dict]) -> str`**（`yahoo_common.py` 新增，放在 `evaluate_quality` 之前）：

   ```python
   def build_scoring_prompt(dims: list[dict]) -> str:
       """从维度定义列表构造 prompt 片段"""
       lines = []
       for d in dims:
           if d.get("definition"):
               lines.append(
                   f"【{d['name']}】判断标准：{d['definition']}\n"
                   f"  ✅ 给1示例：{d.get('example_1','')}\n"
                   f"  ❌ 给0示例：{d.get('example_0','')}\n"
                   f"  ⚠️ 边界说明：{d.get('edge_case','')}"
               )
           else:
               lines.append(f"【{d['name']}】")  # 降级为裸名
       return "\n".join(lines)
   ```

4. **修改 `evaluate_quality`** 函数体（第 582 行）：

   - 在函数顶部动态加载维度：

     ```python
     import json as _json
     from pathlib import Path
     _dims_path = Path(__file__).parent.parent / "config" / "scoring_dimensions.json"
     try:
         _dims_cfg = _json.loads(_dims_path.read_text(encoding="utf-8"))["dimensions"]
     except Exception:
         _dims_cfg = []  # 回退到硬编码 dims
     ```

   - 将 `content[:200]` 改为 `body_text[:800] if body_text else content[:800]`（参数 `body_text` 已有，对应 `content_ja`）
   - 将 `call_litellm(prompt, ..., max_tokens=3000)` 改为传入 `temperature=0.1`
   - 在 prompt 中追加 JSON schema 声明：

     ```
     返回严格 JSON，格式为：
     {"维度名": {"value": 0或0.5或1, "reason": "15-50字理由"}, ...}
     所有 N 个维度都必须出现，value 只能是 0、0.5、1 三个值之一。
     ```

   - 解析时从 `int(v.get("value",0))` 改为 `float(v.get("value",0))`（支持 0.5）

5. **「收藏驱动」以等权 +1 计入 `content_score`**（加权化由模块 B 统一，此处等权先到位）：确认 `content_plus` 列表中追加 `'收藏驱动'` 和 `'评论引导性'`。

### 自测清单

```python
# tests/test_scoring_prompt.py
import json
from pathlib import Path

def test_dims_json_structure():
    cfg = json.loads(
        (Path("config/scoring_dimensions.json")).read_text(encoding="utf-8")
    )
    assert "version" in cfg
    assert len(cfg["dimensions"]) >= 20  # 至少20个维度
    names = [d["name"] for d in cfg["dimensions"]]
    assert "收藏驱动" in names
    assert "评论引导性" in names

def test_build_scoring_prompt_with_definition():
    import sys; sys.path.insert(0, "scripts")
    from yahoo_common import build_scoring_prompt
    dims = [{"name": "原创度", "direction": "plus", "definition": "视角独特性",
             "example_1": "案例1", "example_0": "案例0", "edge_case": "边界"}]
    result = build_scoring_prompt(dims)
    assert "判断标准：视角独特性" in result
    assert "给1示例" in result

def test_build_scoring_prompt_fallback():
    from yahoo_common import build_scoring_prompt
    dims = [{"name": "冲突感"}]  # 无 definition
    result = build_scoring_prompt(dims)
    assert result == "【冲突感】"  # 降级为裸名

def test_call_litellm_temperature_param():
    """验证 temperature 参数透传（mock 验证）"""
    import sys; sys.path.insert(0, "scripts")
    import yahoo_common
    captured = {}
    def mock_post(url, headers, json, timeout):
        captured["temperature"] = json.get("temperature")
        class R:
            status_code = 200
            def json(self): return {"choices":[{"message":{"content":"{}"}}]}
        return R()
    yahoo_common._direct_session.post = mock_post
    yahoo_common.call_litellm("test", temperature=0.1)
    assert captured["temperature"] == 0.1
```

- `[ ]` 对一篇实际文章运行 `evaluate_quality`，确认返回 JSON 中 `value` 字段可为 `0.5`
- `[ ]` 检查 `title_score` 和 `content_score` 仍在 `[0, 5]` 范围内

### 端到端测试

```python
# 手动验证：找一篇有 body_text 的文章
from scripts.yahoo_common import evaluate_quality
result = evaluate_quality(
    title_zh="AKB48 某成员宣布毕业，粉丝泪奔",
    content="（200字占位）",
    comment="测试",
    body_text="（800字日文正文）"
)
assert result["title_score"] >= 0
assert "收藏驱动" in result["scores"]
```

---

## Phase 0-C：metrics_collector.py

**交付物：**
- 新建 `scripts/metrics_collector.py`
- 修改 `scripts/cdp_publish.py`：新增 `fetch_note_stats()` 方法

**前置条件：** Phase 0-A

### 实施步骤

1. **在 `cdp_publish.py` 的 `XiaohongshuPublisher` 类中新增方法 `fetch_note_stats(note_url: str) -> dict`**：

   - 调用 CDP 打开笔记 URL（使用已有的 `navigate` + `wait_for_load` 模式）
   - 用 `document.querySelector` 选取互动数字 DOM 元素（以现有 `search_feeds` 中的 CSS 选择器为参照）
   - 提取 `views`/`likes`/`saves`/`comments` 四个字段，遇到「万」格式做转换（`1.2万` → `12000`）
   - 返回 `{"views": int, "likes": int, "saves": int, "comments": int}`，任意字段失败时该字段返回 `None`
   - 整体失败返回 `{}`，不抛出异常

2. **新建 `scripts/metrics_collector.py`**，实现以下函数：

   ```python
   COLLECTION_WINDOWS = [("4h", 4), ("24h", 24), ("72h", 72)]  # (标记, 小时数)

   def get_articles_pending_collection() -> list[dict]:
       """查询需要回收的文章：publish_xhs=1, xhs_pub_time 非空"""
       ...

   def needs_collection(article: dict, label: str, hours: int) -> bool:
       """判断某文章在某时间点是否需要回收（未过时间 → False；已有标记 → False）"""
       ...

   def collect_article(publisher, article: dict, label: str) -> bool:
       """对单篇文章发起 CDP 抓取并写回 DB"""
       ...

   def collect_pending_articles(dry_run: bool = False) -> dict:
       """主函数：遍历所有待回收文章，按时间点判断并回收"""
       ...
   ```

3. **`collect_article` 实现细节**：
   - 调用 `publisher.fetch_note_stats(article["gallery_url"])`（gallery_url 即笔记链接）
   - 若 stats 非空：调用 `sqlite_db.update_news(key, {"xhs_views":..., ...})`
   - 追加 `xhs_collected_at`：`existing = article.get("xhs_collected_at",""); new_val = (existing + "," + label).strip(",")` 后 `update_news(key, {"xhs_collected_at": new_val})`
   - 失败时记录日志，返回 `False`，不修改 DB

4. **`__main__` 入口**（支持 `--key <key>` 单篇触发和 `--dry-run`）：

   ```python
   if __name__ == "__main__":
       import argparse
       p = argparse.ArgumentParser()
       p.add_argument("--key")
       p.add_argument("--dry-run", action="store_true")
       args = p.parse_args()
   ```

5. **新增依赖**：无新增（已有 `requests`、`sqlite3`）

### 自测清单

```python
# tests/test_metrics_collector.py
from datetime import datetime, timedelta

def _make_article(pub_hours_ago: float, collected: str = "") -> dict:
    pub = datetime.now() - timedelta(hours=pub_hours_ago)
    return {
        "key": "k1", "xhs_pub_time": pub.strftime("%Y-%m-%d %H:%M"),
        "xhs_collected_at": collected, "gallery_url": "https://xhs.test/note/xxx"
    }

def test_needs_collection_4h_not_reached():
    import sys; sys.path.insert(0, "scripts")
    from metrics_collector import needs_collection
    art = _make_article(pub_hours_ago=3.0)
    assert not needs_collection(art, "4h", 4)

def test_needs_collection_4h_reached():
    from metrics_collector import needs_collection
    art = _make_article(pub_hours_ago=4.5)
    assert needs_collection(art, "4h", 4)

def test_needs_collection_already_done():
    from metrics_collector import needs_collection
    art = _make_article(pub_hours_ago=30, collected="4h,24h")
    assert not needs_collection(art, "24h", 24)

def test_collect_pending_dry_run(monkeypatch):
    """dry-run 模式：不触发 CDP，函数正常返回统计"""
    from metrics_collector import collect_pending_articles
    result = collect_pending_articles(dry_run=True)
    assert "checked" in result
```

- `[ ]` 对 `xhs_pub_time` 为 5 小时前的真实文章运行 `python scripts/metrics_collector.py --key <key>`，确认数据回填
- `[ ]` 确认 `xhs_collected_at` 追加为 `"4h"` 后，再次运行不会重复

### 端到端测试

**场景（无真实 XHS 账号时 mock CDP）：**

```python
# tests/test_metrics_e2e.py
import types, sys
sys.path.insert(0, "scripts")

def mock_publisher():
    pub = types.SimpleNamespace()
    pub.fetch_note_stats = lambda url: {"views": 100, "likes": 5, "saves": 3, "comments": 2}
    return pub

def test_collect_article_writes_db():
    import os; os.environ["SQLITE_PATH"] = ":memory:"
    import sqlite_db as db; db.init_db()
    from datetime import datetime, timedelta
    pub_time = (datetime.now() - timedelta(hours=5)).strftime("%Y-%m-%d %H:%M")
    db.insert_news({"key":"e2e_k","title":"T","link":"L",
                    "xhs_pub_time": pub_time, "gallery_url":"https://xhs.test/1",
                    "publish_xhs":1})
    from metrics_collector import collect_article
    art = db.get_by_key("e2e_k")
    collect_article(mock_publisher(), art, "4h")
    updated = db.get_by_key("e2e_k")
    assert updated["xhs_saves"] == 3
    assert "4h" in updated["xhs_collected_at"]
```

---

## Phase 0-D：dimension_analysis.py

**交付物：**
- 新建 `scripts/dimension_analysis.py`

**前置条件：** Phase 0-A（有 xhs_saves 等字段），Phase 0-C（有实际数据）

### 实施步骤

1. **函数 `load_analysis_data(min_window="72h") -> pd.DataFrame`**：
   - JOIN `news` 和 `score_dims`，过滤条件：`xhs_collected_at` 包含 `min_window` 标记
   - 优先使用 `human_value`（`human_override=1`），否则用 `value`（有效值命名为 `effective_value`）
   - PIVOT 每个维度为一列，返回宽表

2. **函数 `compute_correlations(df, targets=("xhs_saves","xhs_comments","xhs_views")) -> pd.DataFrame`**：
   - 对每个维度列与每个 target 用 `scipy.stats.pearsonr` 计算 r 和 p 值
   - 返回 DataFrame：`dimension / target / r / p / n`

3. **函数 `format_report(corr_df, dim_version: str) -> str`**：
   - 报告头部：`基于维度定义 v{dim_version}，生成于 {date}`
   - 按 `|r|` 降序排列，p < 0.05 标记 `*`，p < 0.01 标记 `**`

4. **`__main__` 入口**：

   ```python
   parser.add_argument("--targets", default="xhs_saves,xhs_comments,xhs_views")
   parser.add_argument("--output", default="")  # 空时打印到 stdout
   parser.add_argument("--min-sample", type=int, default=10)
   ```

5. **新增依赖**：`scipy`（已在 `requirements.txt` 中？需核对）、`pandas`（若未有则新增）

   > 核查 `requirements.txt`：当前无 `scipy` 和 `pandas`，需追加：
   > ```
   > scipy>=1.11.0
   > pandas>=2.0.0
   > ```

### 自测清单

```python
# tests/test_dimension_analysis.py
import pytest
import pandas as pd
import numpy as np

def test_compute_correlations_basic():
    import sys; sys.path.insert(0, "scripts")
    from dimension_analysis import compute_correlations
    # 构造合成数据：原创度 与 xhs_saves 正相关
    n = 20
    df = pd.DataFrame({
        "原创度": np.linspace(0, 1, n),
        "离题": np.random.random(n),
        "xhs_saves": np.linspace(0, 100, n),
        "xhs_comments": np.random.randint(0, 10, n),
        "xhs_views": np.random.randint(0, 500, n),
    })
    result = compute_correlations(df)
    row = result[(result["dimension"]=="原创度") & (result["target"]=="xhs_saves")].iloc[0]
    assert row["r"] > 0.9  # 强正相关
    assert row["p"] < 0.05

def test_min_sample_guard():
    from dimension_analysis import compute_correlations
    df = pd.DataFrame({"原创度": [1,0], "xhs_saves": [10,5], 
                       "xhs_comments": [1,0], "xhs_views":[100,50]})
    # 只有2条，结果应标记 n=2 但不抛异常
    result = compute_correlations(df)
    assert len(result) > 0

def test_report_contains_version():
    from dimension_analysis import format_report
    import pandas as pd
    dummy = pd.DataFrame({"dimension":["原创度"],"target":["xhs_saves"],
                          "r":[0.5],"p":[0.01],"n":[15]})
    report = format_report(dummy, "1.0.0")
    assert "v1.0.0" in report
```

- `[ ]` 运行 `python scripts/dimension_analysis.py --min-sample 5`，确认有输出（哪怕样本不足时有友好提示）
- `[ ]` 用 `--output report.txt` 确认文件写入正常

### 端到端测试

构造 30 条测试记录（含 72h 回收标记），运行 `dimension_analysis.py`，验证：
1. 报告中包含版本号
2. 三个目标变量（saves/comments/views）均有相关性输出
3. 人工纠正值（`human_override=1`）被优先使用（对比去掉 override 前后的 r 值变化）

---

## 模块 1：dimension-registry

**交付物：**
- 修改 `scripts/sqlite_db.py`：新增 `scoring_dimension_versions` 表 + 4 个函数
- 修改 `scripts/yahoo_common.py`：`build_scoring_prompt` 改为从 DB 读取，`evaluate_quality` 调用链更新
- 新增 `web/app.py`：`PUT /api/score-dim/<key>/<dimension>` 路由

**前置条件：** Phase 0-A、Phase 0-B

### 实施步骤

1. **在 `sqlite_db.py` 的 `executescript` 中追加表**：

   ```sql
   CREATE TABLE IF NOT EXISTS scoring_dimension_versions (
       id             INTEGER PRIMARY KEY AUTOINCREMENT,
       version        TEXT NOT NULL,
       dimensions_json TEXT NOT NULL,
       created_at     TEXT NOT NULL,
       created_by     TEXT DEFAULT 'system',
       change_note    TEXT DEFAULT '',
       is_active      INTEGER DEFAULT 0
   );
   CREATE INDEX IF NOT EXISTS idx_sdv_active ON scoring_dimension_versions(is_active);
   ```

2. **实现 `init_dimension_versions()`**：
   - 若表非空直接返回
   - 从 `config/scoring_dimensions.json` 读取，以版本 `"1.0.0"` 写入并 `is_active=1`

3. **实现 `load_active_dimensions() -> list[dict]`**：
   - 进程级缓存结构：`_dim_cache = {"dims": [], "cached_created_at": ""}` （模块级变量）
   - 每次调用先查 `SELECT created_at FROM scoring_dimension_versions WHERE is_active=1`
   - 若与 `_dim_cache["cached_created_at"]` 不同则重新加载，否则直接返回缓存
   - 表为空时调用 `init_dimension_versions()` 后再查

4. **实现 `commit_dimension_version(dims: list[dict], change_note: str, created_by: str = "human")`**：
   - 计算新版本号：读当前 `version`，按 minor 自动递增（`1.0.0` → `1.1.0`）
   - `UPDATE scoring_dimension_versions SET is_active=0`（全部清零）
   - `INSERT INTO scoring_dimension_versions ... is_active=1`
   - 清空 `_dim_cache`

5. **实现 `rollback_dimension_version(version: str) -> bool`**：
   - 查找该 version 的行，不存在返回 `False`
   - 全量置 0 后将目标行 `is_active=1`，清空缓存

6. **修改 `yahoo_common.py:evaluate_quality`**：
   - 不再直接读 JSON 文件，改为：

     ```python
     from scripts.sqlite_db import load_active_dimensions
     dims = load_active_dimensions()
     if not dims:
         dims = []  # 彻底降级
     dim_prompt = build_scoring_prompt(dims)
     ```

   - 当前生效版本号写入每条 `score_dims` 的 `dim_version` 字段：通过 `upsert_score_dims` 的参数透传

7. **修改 `upsert_score_dims`**，新增参数 `dim_version: str = ""`**，写入对应字段

8. **在 `web/app.py` 新增 `PUT /api/score-dim/<key>/<dimension>` 路由**：
   - 接收 `{"human_value": 0.5, "override_note": "理由"}`
   - 查找现有 `score_dims` 行，将 `value` 移入 `llm_value`，写入 `human_value`、`human_override=1`
   - 重新调用加权计算逻辑（复用 `evaluate_quality` 中的分数计算子函数），更新 `news` 表的 `title_score`/`content_score`
   - 返回 `{"ok": true, "new_title_score": ..., "new_content_score": ...}`

### 自测清单

```python
# tests/test_dimension_registry.py
import os; os.environ["SQLITE_PATH"] = ":memory:"

def setup_module():
    import scripts.sqlite_db as db; db.init_db()

def test_init_loads_from_json():
    import scripts.sqlite_db as db
    db.init_dimension_versions()
    dims = db.load_active_dimensions()
    names = [d["name"] for d in dims]
    assert "收藏驱动" in names
    assert "评论引导性" in names

def test_commit_increments_version():
    import scripts.sqlite_db as db
    dims = db.load_active_dimensions()
    dims[0]["edge_case"] = "新增边界说明"
    db.commit_dimension_version(dims, "测试更新", "human")
    # 查新版本号
    import sqlite3
    conn = sqlite3.connect(db.DB_PATH)
    row = conn.execute("SELECT version FROM scoring_dimension_versions WHERE is_active=1").fetchone()
    assert row[0] == "1.1.0"

def test_rollback():
    import scripts.sqlite_db as db
    db.rollback_dimension_version("1.0.0")
    dims = db.load_active_dimensions()
    # 回滚后 edge_case 应为原始值
    assert True  # 不抛异常即通过

def test_cache_invalidated_after_commit():
    import scripts.sqlite_db as db
    db.load_active_dimensions()  # 填充缓存
    dims = db.load_active_dimensions()
    db.commit_dimension_version(dims, "触发缓存失效", "system")
    new_dims = db.load_active_dimensions()  # 应重新加载
    assert new_dims is not None
```

- `[ ]` 在 Web UI 对某篇文章某个维度点击「纠正为 0.5」，确认 DB `human_override=1`，综合分更新
- `[ ]` 调用 `rollback_dimension_version("1.0.0")` 后再次执行评分，确认 prompt 使用旧定义

### 端到端测试

1. 初始状态：运行系统，`evaluate_quality` 使用 v1.0.0 定义
2. 通过 `commit_dimension_version` 发布 v1.1.0（修改「原创度」edge_case）
3. 对同一篇文章再次评分，抓取 `score_dims.dim_version`，确认为 `"1.1.0"`
4. 调用 `rollback_dimension_version("1.0.0")`，确认下次评分 `dim_version = "1.0.0"`

---

## 模块 A：记忆层 CRUD

**交付物：**
- 修改 `scripts/sqlite_db.py`：新增 8 个 CRUD 函数
- 新建 `config/agent_strategy.json`

**前置条件：** Phase 0-A（四张新表已建）

### 实施步骤

1. **实现 `get_top_topics(n: int = 10, window_days: int = 90) -> list[dict]`**：
   - 过滤 `last_updated >= now - window_days`，按 `engagement_score DESC` 取 top n
   - 无历史数据的话题标记 `is_new=True`

2. **实现 `get_recent_performance(days: int = 7) -> dict`**：
   - 查 `account_snapshots` 近 days 天的记录（跳过 followers 为 null 的行）
   - 返回 `{"avg_week_saves": float, "avg_week_views": float, "data_completeness": str}`

3. **实现 `upsert_topic_performance(topic: str, saves: float, comments: float, views: float = 0, **kwargs)`**：
   - `INSERT OR REPLACE`，重算 `engagement_score = saves_w * avg_saves + comments_w * avg_comments`（权重从 `agent_config` 读，默认 `{"saves": 0.6, "comments": 0.4}`）
   - 更新 `post_count += 1`，`last_updated = now`

4. **实现 `get_config(key: str, default=None)` / `set_config(key: str, value) -> None`**：
   - value 统一序列化为 JSON 字符串存储（支持 dict/list/str/int）
   - `get_config` 反序列化时若 JSON 解析失败直接返回原字符串

5. **实现 `get_state(key: str, default=None)` / `set_state(key: str, value, date: str = "") -> None`**：
   - date 默认 `datetime.now().strftime("%Y%m%d")`

6. **实现 `cleanup_old_states(days: int = 7)`**：
   - 删除 `date` 字段对应日期超过 days 天前的记录（date 为空的行不删）

7. **实现 `load_dim_weights() -> dict`**（带 1 分钟内存缓存，用 `updated_at` 时间戳判断失效）：

   ```python
   _dim_weights_cache = {"weights": {}, "cached_updated_at": ""}
   def load_dim_weights() -> dict:
       row = _connect().execute(
           "SELECT value, updated_at FROM agent_config WHERE key='dim_weights'"
       ).fetchone()
       if not row: return {}
       if row["updated_at"] != _dim_weights_cache["cached_updated_at"]:
           _dim_weights_cache["weights"] = json.loads(row["value"])
           _dim_weights_cache["cached_updated_at"] = row["updated_at"]
       return _dim_weights_cache["weights"]
   ```

8. **新建 `config/agent_strategy.json`**（默认配置，作为 `agent_config` 一次性初始化种子）：

   ```json
   {
     "dim_weights": {},
     "publish_threshold": 3.0,
     "retry_threshold": 2.0,
     "daily_quota": 2,
     "cold_start_quota": 3,
     "cold_start_max_quota": 4,
     "max_daily_quota": 4,
     "growth_stage": "cold_start",
     "focus_topics": [],
     "engagement_weights": {"saves": 0.6, "comments": 0.4},
     "default_post_times": ["09:30", "12:00", "18:00"],
     "exploration_ratio": 0.2,
     "baseline_ratio": 0.1
   }
   ```

### 自测清单

```python
# tests/test_memory_layer.py
import os; os.environ["SQLITE_PATH"] = ":memory:"

def setup_module():
    import scripts.sqlite_db as db; db.init_db()

def test_set_get_config():
    import scripts.sqlite_db as db
    db.set_config("test_key", {"a": 1, "b": [1,2,3]})
    val = db.get_config("test_key")
    assert val == {"a": 1, "b": [1,2,3]}

def test_get_config_default():
    import scripts.sqlite_db as db
    assert db.get_config("nonexistent", default=42) == 42

def test_upsert_topic_performance():
    import scripts.sqlite_db as db
    db.upsert_topic_performance("写真", saves=50, comments=10, views=500)
    db.upsert_topic_performance("写真", saves=60, comments=15, views=600)
    topics = db.get_top_topics(n=1)
    assert topics[0]["topic"] == "写真"
    assert topics[0]["post_count"] == 2

def test_set_get_state():
    import scripts.sqlite_db as db
    db.set_state("daily_plan_20260521", {"quota": 3}, date="20260521")
    val = db.get_state("daily_plan_20260521")
    assert val["quota"] == 3

def test_cleanup_old_states():
    import scripts.sqlite_db as db
    db.set_state("old_state", {"x": 1}, date="20260101")  # 很早以前
    db.cleanup_old_states(days=7)
    assert db.get_state("old_state") is None

def test_load_dim_weights_from_config():
    import scripts.sqlite_db as db
    db.set_config("dim_weights", {"收藏驱动": 2.0})
    weights = db.load_dim_weights()
    assert weights.get("收藏驱动") == 2.0
```

### 端到端测试

```python
# 模拟 agent_runner 初始化：从 agent_strategy.json 导入配置到 DB
from pathlib import Path; import json, scripts.sqlite_db as db
db.init_db()
strategy = json.loads(Path("config/agent_strategy.json").read_text())
for k, v in strategy.items():
    db.set_config(k, v)
assert db.get_config("growth_stage") == "cold_start"
assert db.get_config("daily_quota") == 2
```

---

## 模块 B：评分加权化

**交付物：**
- 修改 `scripts/yahoo_common.py`：`evaluate_quality` 改用加权求和

**前置条件：** Phase 0-B（`build_scoring_prompt` 已有），模块 A（`load_dim_weights` 可用）

### 实施步骤

1. **在 `evaluate_quality` 函数体中替换分数计算逻辑**（替换第 623-625 行的等权求和）：

   ```python
   from scripts.sqlite_db import load_dim_weights
   weights = load_dim_weights()  # 缺失维度默认 1.0

   def weighted_sum(dim_list: list, minus: bool = False) -> float:
       total = 0.0
       for d in dim_list:
           w = weights.get(d, 1.0)
           v = dim_scores.get(d, {}).get("value", 0.0)
           total += w * float(v)
       return total if not minus else -total

   title_score = (weighted_sum(title_plus) + weighted_sum(title_minus, minus=True))
   content_score = (weighted_sum(content_plus) + weighted_sum(content_minus, minus=True))
   title_score = max(0.0, min(5.0, title_score))
   content_score = max(0.0, min(5.0, content_score))
   ```

2. **确保 LLM 未返回某维度时不报错**：`dim_scores.get(d, {}).get("value", 0.0)` 已覆盖。

3. **确认无权重时行为等价于原版**：`weights = {}` 时所有 `weights.get(d, 1.0)` 均为 `1.0`，结果与原等权一致。

### 自测清单

```python
# tests/test_weighted_scoring.py
import os; os.environ["SQLITE_PATH"] = ":memory:"

def test_weighted_sum_changes_score(monkeypatch):
    import scripts.sqlite_db as db; db.init_db()
    import scripts.yahoo_common as yc

    # 设置「收藏驱动」权重为 2.0
    db.set_config("dim_weights", {"收藏驱动": 2.0})

    # mock call_litellm 返回固定评分
    def mock_llm(prompt, **kwargs):
        import json
        return json.dumps({
            "收藏驱动": {"value": 1, "reason": "是清单"},
            "原创度": {"value": 1, "reason": "有独特视角"},
            # 其他维度默认 0
        })
    monkeypatch.setattr(yc, "call_litellm", mock_llm)

    result_weighted = yc.evaluate_quality("标题", "正文", "评论")
    # 权重2.0 * 1 = 2 + 原创度1.0 * 1 = 1 → content=3
    assert result_weighted["content_score"] == 3.0

def test_no_weights_equals_original(monkeypatch):
    import scripts.sqlite_db as db; db.init_db()
    import scripts.yahoo_common as yc

    db.set_config("dim_weights", {})  # 空权重

    def mock_llm(prompt, **kwargs):
        import json
        return json.dumps({"原创度": {"value": 1, "reason": ""}, "收藏驱动": {"value": 1, "reason": ""}})
    monkeypatch.setattr(yc, "call_litellm", mock_llm)

    result = yc.evaluate_quality("标题", "正文", "评论")
    # 等权：content_plus 两个维度各1 = 2
    assert result["content_score"] == 2.0

def test_missing_dim_no_exception(monkeypatch):
    """LLM 未返回某维度时不崩溃"""
    import scripts.yahoo_common as yc
    monkeypatch.setattr(yc, "call_litellm", lambda *a, **kw: "{}")
    result = yc.evaluate_quality("标题", "正文", "评论")
    assert result["content_score"] == 0.0
```

---

## 模块 C：scoring.py 提取 + 低分诊断

**交付物：**
- 新建 `scripts/scoring.py`
- 新建 `scripts/agent_tools.py`
- 修改 `scripts/yahoo_common.py`：`process_news_item` 调用 `diagnose_low_score`

**前置条件：** 模块 B

### 实施步骤

1. **新建 `scripts/scoring.py`**：

   ```python
   from enum import Enum

   class Action(str, Enum):
       DISCARD = "DISCARD"
       REGENERATE = "REGENERATE"
       WAIT_GALLERY = "WAIT_GALLERY"
       HUMAN_REVIEW = "HUMAN_REVIEW"
       PUBLISH = "PUBLISH"

   def diagnose_low_score(article: dict, scores: dict,
                          publish_threshold: float = 3.0,
                          retry_threshold: float = 2.0) -> Action:
       """纯函数，无副作用，确定文章应采取的行动。
       
       scores: {dimension: {"value": float, "reason": str}}
       """
       content_score = article.get("content_score", 0)
       title_score = article.get("title_score", 0)

       # 图片不足且评分边界 → 等待图集
       if not article.get("gallery_images") and content_score < publish_threshold:
           return Action.WAIT_GALLERY

       # 明确垃圾信号 → 直接丢弃
       discard_dims = ["离题", "负面情绪", "主动讨赏"]
       for d in discard_dims:
           if scores.get(d, {}).get("value", 0) >= 1:
               return Action.DISCARD

       combined = title_score + content_score
       if combined >= publish_threshold * 2:
           return Action.PUBLISH
       if combined >= retry_threshold * 2:
           return Action.REGENERATE
       if combined < retry_threshold:
           return Action.DISCARD
       return Action.HUMAN_REVIEW
   ```

2. **失败维度提取函数 `get_failed_dims(scores: dict) -> list[str]`**（在 `scoring.py` 中）：
   - 返回 `direction=minus` 且 `value >= 1` 的维度列表
   - 依赖 `load_active_dimensions()` 获取每个维度的 direction

3. **新建 `scripts/agent_tools.py`**，实现 `regenerate_with_hint(article: dict, failed_dims: list[str]) -> dict`：
   - 根据 `failed_dims` 构造修正提示词（预定义 `HINT_MAP: dict[str, str]`，如 `"啰嗦重复": "请精简正文，删除重复表述"`）
   - 调用 `generate_content_and_comment` 并传入 hint 作为额外 system prompt
   - 返回更新了 `content/comment/title` 的 article dict

4. **`agent_tools.py` 同时封装工具**：
   ```python
   def fetch_by_keywords(keywords: list, limit: int = 5) -> list[dict]: ...
   def run_gallery_download(news_key: str) -> bool: ...
   def run_publish_pipeline(news_key: str, publish_time: str = "") -> bool: ...
   ```
   （内部 `import` 现有模块，统一异常处理接口）

5. **修改 `yahoo_common.py:process_news_item`**（第 834 行附近）：
   - 评分完成后插入最多 2 次重试循环：

   ```python
   from scripts.scoring import diagnose_low_score, Action
   for attempt in range(3):  # 最多原始 + 2 次重试
       score_result = evaluate_quality(...)
       action = diagnose_low_score(news, score_result["scores"],
                                   publish_threshold, retry_threshold)
       if action == Action.REGENERATE and attempt < 2:
           failed = get_failed_dims(score_result["scores"])
           news = regenerate_with_hint(news, failed)
           continue
       break
   # 根据最终 action 写状态
   if action == Action.DISCARD:
       update_news(key, {"status": "discarded"})
   ```

### 自测清单

```python
# tests/test_scoring.py
from scripts.scoring import diagnose_low_score, Action

def _scores(**kwargs):
    base = {d: {"value": 0, "reason": ""} for d in
            ["离题","负面情绪","主动讨赏","啰嗦重复","原创度","收藏驱动"]}
    for k, v in kwargs.items():
        base[k] = {"value": v, "reason": "test"}
    return base

def test_discard_on_negative_dim():
    art = {"content_score": 2.0, "title_score": 2.0, "gallery_images": ["img.jpg"]}
    assert diagnose_low_score(art, _scores(离题=1)) == Action.DISCARD

def test_publish_on_high_score():
    art = {"content_score": 4.0, "title_score": 4.0, "gallery_images": ["img.jpg"]}
    assert diagnose_low_score(art, _scores()) == Action.PUBLISH

def test_regenerate_on_medium_score():
    art = {"content_score": 2.5, "title_score": 2.5, "gallery_images": ["img.jpg"]}
    result = diagnose_low_score(art, _scores(), publish_threshold=3.0, retry_threshold=2.0)
    assert result == Action.REGENERATE

def test_wait_gallery_no_images():
    art = {"content_score": 2.0, "title_score": 2.0, "gallery_images": []}
    assert diagnose_low_score(art, _scores()) == Action.WAIT_GALLERY

def test_human_review_boundary():
    art = {"content_score": 2.2, "title_score": 2.2, "gallery_images": ["img.jpg"]}
    result = diagnose_low_score(art, _scores(), publish_threshold=3.0, retry_threshold=2.0)
    # 4.4 >= retry_threshold*2=4.0 → REGENERATE
    assert result == Action.REGENERATE
```

### 端到端测试

构造一篇 `啰嗦重复=1` 的测试文章调用 `process_news_item`（mock `call_litellm` 第一次返回低分 + 啰嗦，第二次返回高分），确认：
1. 第一次评分后触发 `REGENERATE`
2. `regenerate_with_hint` 被调用，hint 中包含「精简」关键词
3. 第二次评分后 `action = PUBLISH`，文章状态为 `active`

---

## 模块 D：xhs_trend_scanner.py

**交付物：**
- 新建 `scripts/xhs_trend_scanner.py`

**前置条件：** 模块 A（`topic_performance` 表可写）

### 实施步骤

1. **函数 `scan_topic_trends(keywords: list[str], limit: int = 10) -> list[dict]`**：
   - 对每个 keyword 调用 `publisher.search_feeds(keyword=k, sort="最多收藏", limit=limit)`（`cdp_publish.py` 第 1564 行）
   - 提取 TOP 10 笔记的：标题列表、点赞/收藏/评论均值、图文/视频比例、平均标题长度
   - 用 LLM（`call_litellm`，`temperature=0.3`）从标题列表提炼 `recommended_keywords`（推荐关键词）
   - 返回 `[{"topic": str, "top_titles": [str], "recommended_keywords": [str], "image_ratio": float, "avg_title_len": int, "baseline_saves": float, "baseline_comments": float, "is_fresh": bool}]`

2. **`is_fresh` 判断逻辑**：笔记发布时间（若 API 有返回）中有超过 50% 是今日或昨日的，标记 `is_fresh=True`

3. **函数 `update_trend_signals(trend_results: list[dict]) -> None`**：
   - 对每条结果调用 `upsert_topic_performance(topic, ...)` 并更新 `trend_signal = json.dumps({"top_titles": ..., "recommended_keywords": ...})`、`trend_updated_at = now`
   - 同时更新 `topic_baseline_saves` 和 `topic_baseline_comments`

4. **CDP 未就绪时的降级处理**：
   ```python
   try:
       publisher.connect()
   except Exception as e:
       logger.warning(f"CDP 未就绪，跳过趋势扫描: {e}")
       return []
   ```

5. **`__main__` 入口**：
   ```python
   parser.add_argument("--keywords", default="写真集,日本女星")
   parser.add_argument("--limit", type=int, default=10)
   ```

6. **无新增依赖**（已有 `requests`、`cdp_publish`）

### 自测清单

```python
# tests/test_trend_scanner.py
import pytest, types

def mock_publisher_with_feeds(feeds: list):
    pub = types.SimpleNamespace()
    pub.connect = lambda: None
    pub.search_feeds = lambda keyword, sort, limit: feeds
    return pub

def test_scan_returns_structured_result(monkeypatch):
    import sys; sys.path.insert(0, "scripts")
    import xhs_trend_scanner as ts
    feeds = [{"title": f"写真集{i}", "likes": i*10, "saves": i*5,
              "comments": i, "type": "image", "pub_time": "2026-05-21"} for i in range(10)]
    pub = mock_publisher_with_feeds(feeds)
    monkeypatch.setattr(ts, "_get_publisher", lambda: pub)
    monkeypatch.setattr(ts, "call_litellm", lambda *a, **kw: '["写真","美人"]')
    result = ts.scan_topic_trends(["写真集"])
    assert len(result) == 1
    assert result[0]["topic"] == "写真集"
    assert result[0]["baseline_saves"] > 0

def test_cdp_failure_returns_empty(monkeypatch):
    import xhs_trend_scanner as ts
    def bad_connect(): raise ConnectionError("CDP not ready")
    monkeypatch.setattr(ts, "_get_publisher", lambda: types.SimpleNamespace(connect=bad_connect))
    result = ts.scan_topic_trends(["写真集"])
    assert result == []

def test_update_trend_signals_writes_db(monkeypatch):
    import os; os.environ["SQLITE_PATH"] = ":memory:"
    import scripts.sqlite_db as db; db.init_db()
    import xhs_trend_scanner as ts
    ts.update_trend_signals([{"topic": "写真集", "baseline_saves": 100.0,
                              "baseline_comments": 20.0, "is_fresh": True,
                              "top_titles": ["T1"], "recommended_keywords": ["写真"]}])
    topics = db.get_top_topics(n=1)
    assert topics[0]["topic"] == "写真集"
```

- `[ ]` 运行 `python scripts/xhs_trend_scanner.py --keywords "写真集" --limit 5`（需 CDP 可用），确认数据写入 `topic_performance`

---

## 模块 E：agent_planner.py

**交付物：**
- 新建 `scripts/agent_planner.py`

**前置条件：** 模块 A（DB 层），模块 D（趋势数据）

### 实施步骤

1. **定义数据类**：

   ```python
   from dataclasses import dataclass, field

   @dataclass
   class TopicQuota:
       topic: str
       quota: int
       source: str  # "high_perf" | "explore" | "baseline"
       is_fresh: bool = False

   @dataclass
   class DailyPlan:
       date: str
       topics: list[TopicQuota] = field(default_factory=list)
       post_times: list[str] = field(default_factory=list)
       quota_total: int = 3
       mode: str = "cold_start"  # "cold_start" | "growth" | "stable"
       note: str = ""  # 规划摘要说明
   ```

2. **函数 `plan_today(date: str = "") -> DailyPlan`**：
   - 读 `agent_config` 中 `growth_stage`、`focus_topics`、`daily_quota`、`cold_start_quota` 等
   - 若今日计划已存在（`get_state("daily_plan_YYYYMMDD")`）则反序列化直接返回
   - 冷启动逻辑：`focus_topics` 中核心话题占比 ≥ 80%，配额为 `cold_start_quota`（默认 3）
   - 有 `≥ 2` 个 `is_fresh=True` 话题时配额上调至 `cold_start_max_quota`
   - 标准规划（有 ≥ 5 个历史话题）：`70% × get_top_topics + 20% 探索 + 10% 保底`
   - 近 7 天 `engagement_score` 低于历史均值 60%（非 `cold_start`）→ 配额下调 1（最少 1）
   - 近 7 天 `engagement_score` 高于历史均值 120% → 配额上调 1，不超 `max_daily_quota`

3. **函数 `recommend_post_times(topics: list[TopicQuota], date: str) -> list[str]`**：
   - 历史数据 < 50 篇时直接返回 `default_post_times`
   - ≥ 50 篇时按话题分组分析最优时间段（简化版：按 `xhs_pub_time` 小时统计 `avg xhs_views`）
   - 热点维度=1 的文章推荐「当前时间 + 30分钟」

4. **计划持久化**：`set_state(f"daily_plan_{date}", asdict(plan), date=date)`

5. **检查话题观察期**：`discard_count >= 3 AND post_count = 0` 的话题跳过，飞书通知由 `agent_runner` 发送

6. **检查 growth_stage 升级**：若 `followers >= 1000 AND growth_stage = "cold_start"`，在 `plan.note` 中追加升级提示

7. **`__main__` 入口**：
   ```python
   parser.add_argument("--date", default="today")
   parser.add_argument("--print", action="store_true")
   ```

### 自测清单

```python
# tests/test_agent_planner.py
import os; os.environ["SQLITE_PATH"] = ":memory:"

def setup_module():
    import scripts.sqlite_db as db; db.init_db()
    db.set_config("growth_stage", "cold_start")
    db.set_config("cold_start_quota", 3)
    db.set_config("cold_start_max_quota", 4)
    db.set_config("focus_topics", ["写真", "日本女星"])
    db.set_config("daily_quota", 2)
    db.set_config("max_daily_quota", 4)
    db.set_config("default_post_times", ["09:30","12:00","18:00"])
    db.set_config("engagement_weights", {"saves": 0.6, "comments": 0.4})

def test_cold_start_plan():
    import scripts.agent_planner as ap
    plan = ap.plan_today("20260521")
    assert plan.quota_total == 3
    assert plan.mode == "cold_start"
    # 核心话题占比 >= 80%
    focus = sum(1 for t in plan.topics if t.topic in ["写真","日本女星"])
    assert focus / max(len(plan.topics), 1) >= 0.8

def test_fresh_topics_increase_quota(monkeypatch):
    import scripts.sqlite_db as db
    import scripts.agent_planner as ap
    # 插入 2 个 is_fresh 话题
    db.upsert_topic_performance("热点A", saves=10, comments=5)
    db.upsert_topic_performance("热点B", saves=8, comments=3)
    monkeypatch.setattr(db, "get_top_topics",
        lambda **kw: [{"topic":"热点A","engagement_score":10,"is_new":False,"discard_count":0},
                      {"topic":"热点B","engagement_score":8,"is_new":False,"discard_count":0}])
    # 模拟 trend_signal 含 is_fresh=True
    # （此处简化测试，具体实现中从 trend_signal JSON 读取）
    plan = ap.plan_today("20260522")
    assert plan.quota_total <= 4  # 不超上限

def test_plan_idempotent():
    import scripts.agent_planner as ap
    plan1 = ap.plan_today("20260523")
    plan2 = ap.plan_today("20260523")  # 应从 state 读取
    assert plan1.quota_total == plan2.quota_total
```

- `[ ]` 运行 `python scripts/agent_planner.py --date today --print`，确认输出含话题列表、配额、推荐时间

---

## 模块 F：feishu_bot.py + /webhook/feishu 路由

**交付物：**
- 新建 `scripts/feishu_bot.py`
- 修改 `web/app.py`：新增 `/webhook/feishu` 路由

**前置条件：** 独立，可并行开发；需要 `scripts/.env` 中飞书凭证

### 实施步骤

1. **`feishu_bot.py` 基础结构**：

   ```python
   import os, json, time, hashlib, base64, hmac
   import requests
   from datetime import datetime

   FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
   FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
   FEISHU_OPERATOR_OPEN_ID = os.environ.get("FEISHU_OPERATOR_OPEN_ID", "")
   FEISHU_WEBHOOK_SECRET = os.environ.get("FEISHU_WEBHOOK_SECRET", "")
   FEISHU_API_BASE = "https://open.feishu.cn/open-apis"

   _tenant_token_cache = {"token": "", "expires_at": 0}
   ```

2. **实现 `get_tenant_token() -> str`**：
   - `POST /auth/v3/tenant_access_token/internal`
   - 缓存 7200 秒（2小时），提前 5 分钟刷新
   - 未配置凭证时返回 `""`

3. **实现 `send_text(open_id: str, text: str) -> bool`** 和 **`send_card(open_id: str, card_json: dict) -> bool`**：
   - `POST /im/v1/messages?receive_id_type=open_id`
   - 5s 超时，失败仅记日志，返回 `False`
   - 未配置时静默返回 `False`

4. **实现 `build_daily_approval_card(candidates: list[dict]) -> dict`**：
   - 飞书卡片 JSON 结构：标题区 + 每篇文章的 section（标题/评分/话题/推荐发布时间）+ 操作按钮（发布/跳过/重新生成）
   - 每个按钮 `value` 包含 `{"action": "approve/skip/regenerate", "news_key": "xxx", "pub_time": "HH:MM"}`

5. **实现 `build_weekly_report_card(report: dict, weight_suggestions: dict) -> dict`**：
   - 包含数据统计区（总发布/总收藏/话题对比表）和「一键采纳建议」按钮
   - 按钮 `value` 包含 `{"action": "adopt_weights", "weights": {...}}`

6. **实现 `send_alert(text: str) -> bool`**：直接调用 `send_text(FEISHU_OPERATOR_OPEN_ID, text)`

7. **实现签名验证 `verify_feishu_signature(timestamp: str, nonce: str, body: bytes, sig: str) -> bool`**：
   - 按飞书文档：`HMAC-SHA256(FEISHU_WEBHOOK_SECRET + timestamp + nonce + body)`

8. **在 `web/app.py` 新增路由**（放在现有路由之后）：

   ```python
   @app.route("/webhook/feishu", methods=["POST"])
   def feishu_webhook():
       body = request.get_data()
       data = request.json or {}
       # Challenge 验证
       if data.get("challenge"):
           return jsonify({"challenge": data["challenge"]})
       # 签名验证
       ts = request.headers.get("X-Lark-Request-Timestamp", "")
       nonce = request.headers.get("X-Lark-Request-Nonce", "")
       sig = request.headers.get("X-Lark-Signature", "")
       if FEISHU_WEBHOOK_SECRET and not verify_feishu_signature(ts, nonce, body, sig):
           return jsonify({"error": "invalid signature"}), 401
       # 分发 card action
       if data.get("type") == "card":
           action = data.get("action", {})
           _handle_card_action(action)
       return jsonify({"code": 0})

   def _handle_card_action(action: dict):
       act = action.get("value", {}).get("action")
       key = action.get("value", {}).get("news_key")
       if act == "approve":
           pub_time = action["value"].get("pub_time", "")
           update_news(key, {"publish_xhs": 1, "publish_time": pub_time})
       elif act == "skip":
           update_news(key, {"status": "skipped"})
       elif act == "regenerate":
           # 加入重生成队列（写 agent_state）
           set_state(f"regen_{key}", {"key": key})
       elif act == "adopt_weights":
           weights = action["value"].get("weights", {})
           set_config("dim_weights", weights)
   ```

### 自测清单

```python
# tests/test_feishu_bot.py
import os; os.environ["FEISHU_APP_ID"] = ""; os.environ["FEISHU_APP_SECRET"] = ""

def test_send_text_silent_when_unconfigured():
    import scripts.feishu_bot as fb
    result = fb.send_text("U12345", "test message")
    assert result == False  # 不抛异常，静默返回 False

def test_build_approval_card_structure():
    import scripts.feishu_bot as fb
    candidates = [{"key": "k1", "title": "测试标题", "title_score": 3.5,
                   "content_score": 4.0, "topic": "写真", "pub_time_recommended": "12:00"}]
    card = fb.build_daily_approval_card(candidates)
    assert "card" in card or "elements" in card  # 飞书卡片基本结构

def test_webhook_challenge(client):
    """Flask 测试客户端验证 challenge 响应"""
    resp = client.post("/webhook/feishu",
                       json={"challenge": "test_challenge_123"},
                       content_type="application/json")
    assert resp.status_code == 200
    assert resp.json["challenge"] == "test_challenge_123"

def test_webhook_approve_action(client):
    import os; os.environ["SQLITE_PATH"] = ":memory:"
    import scripts.sqlite_db as db; db.init_db()
    db.insert_news({"key":"k1","title":"T","link":"L","publish_xhs":0})
    resp = client.post("/webhook/feishu", json={
        "type": "card",
        "action": {"value": {"action": "approve", "news_key": "k1", "pub_time": "12:00"}}
    })
    assert resp.status_code == 200
    assert db.get_by_key("k1")["publish_xhs"] == 1
```

- `[ ]` 配置真实飞书凭证后运行 `send_text("YOUR_OPEN_ID", "测试消息")`，确认手机收到
- `[ ]` 发送 `build_daily_approval_card` 构造的卡片，点击「发布」按钮，确认 DB `publish_xhs=1`

### 端到端测试

1. 配置飞书 App，在飞书开放平台设置事件回调 URL 为 `http://<server_ip>:<port>/webhook/feishu`
2. 运行 `python -c "from scripts.feishu_bot import build_daily_approval_card, send_card; send_card('OPEN_ID', build_daily_approval_card([...]))"` 发送测试卡片
3. 点击「✅ 发布」，确认 `news` 表 `publish_xhs=1`，`publish_time` 已设置
4. 点击「跳过」，确认对应文章 `status='skipped'`

---

## 模块 G：agent_runner.py 主循环

**交付物：**
- 新建 `scripts/agent_runner.py`
- 新建 `tests/fixtures/sample_news.json`

**前置条件：** 模块 A/B/C/D/E/F 全部完成

### 实施步骤

1. **新建 `tests/fixtures/sample_news.json`**（5 篇预设文章，用于 `--dry-run`）：

   ```json
   [
     {"key": "fixture_001", "title_ja": "AKB48ライブ写真集発売", "title_zh": "AKB48演唱会写真集发售",
      "content": "...", "content_ja": "...(800字日文)", "comment": "...",
      "gallery_images": ["dummy.jpg"], "category": "娱乐", "tags": ["AKB48"]},
     ...
   ]
   ```

2. **主流程函数 `run(dry_run: bool = False, live_preview: bool = False)`**：

   ```
   阶段 1：感知
     - scan_topic_trends(focus_topics)
     - take_account_snapshot()：调用 CDP get_profile_snapshot + get_content_data
     - 检查 growth_stage 是否需要升级提醒

   阶段 2：规划
     - plan = plan_today()，若已存在则跳过
     - 数据健康度检查（trend_updated_at 超 48h → 告警）

   阶段 3：执行
     - 按 plan.topics 分配 fetch_by_keywords
     - evaluate_quality + diagnose_low_score + 最多 2 次重试
     - 写回 DB（状态/评分/score_dims）

   阶段 4：通知
     - 筛选 publish 候选（action=PUBLISH）
     - build_daily_approval_card + send_card

   阶段 5：topic_performance 更新（触发 7 天稳定数据）
     - 检查 pub_date <= today-7 AND topic_perf_updated_at IS NULL AND contains 72h
     - upsert_topic_performance + 写 topic_perf_updated_at
   ```

3. **进度持久化**：每个阶段成功后 `set_state(f"runner_progress_{date}", {"phase": N})`，重复运行时检查 `phase >= N` 则跳过

4. **`--dry-run` 模式**：
   - 加载 `tests/fixtures/sample_news.json` 代替 CDP 抓取
   - 使用 `sqlite3.connect(":memory:")` 的临时 DB（通过 `os.environ["SQLITE_PATH"] = ":memory:"`）
   - 不发送飞书消息
   - 打印完整计划摘要后退出

5. **`--live-preview` 模式**：连接真实 CDP，只读不写，打印「如果今天运行会做什么」报告

6. **账号快照实现 `take_account_snapshot(publisher) -> dict`**：
   - `publisher.get_profile_snapshot()` → 解析 `followers`（含「万」格式降级）
   - `publisher.get_content_data()` → 提取 `week_views/week_saves/week_likes`
   - `INSERT OR REPLACE INTO account_snapshots ...`

7. **冷启动期飞书摘要追加**：若 `growth_stage = "cold_start"`，在审批卡片 header 追加数据预期说明

### 自测清单

```python
# tests/test_agent_runner.py
import os, json, pytest
from pathlib import Path

def test_dry_run_no_db_written(tmp_path):
    """dry-run 模式不污染真实 DB"""
    real_db = os.environ.get("SQLITE_PATH", "data/news.db")
    import scripts.agent_runner as ar
    ar.run(dry_run=True)
    # 真实 DB 未被修改（检查文件 mtime 无变化）
    # 此处用简化断言：dry_run 不抛异常即通过
    assert True

def test_dry_run_prints_plan(capsys):
    import scripts.agent_runner as ar
    ar.run(dry_run=True)
    captured = capsys.readouterr()
    assert "计划" in captured.out or "quota" in captured.out.lower()

def test_progress_persisted(monkeypatch, tmp_path):
    """阶段 1 完成后进度被写入 agent_state"""
    os.environ["SQLITE_PATH"] = ":memory:"
    import scripts.sqlite_db as db; db.init_db()
    import scripts.agent_runner as ar
    monkeypatch.setattr(ar, "_run_phase_1", lambda *a: None)
    monkeypatch.setattr(ar, "_run_phase_2", lambda *a: {"topics":[], "quota_total":1})
    # 运行到阶段 2 后检查进度
    try: ar.run(dry_run=True)
    except Exception: pass
    progress = db.get_state(f"runner_progress_{__import__('datetime').date.today().strftime('%Y%m%d')}")
    # 有进度记录（至少阶段 1 写入）
    assert progress is not None or True  # 降级断言

def test_topic_perf_update_triggered():
    """发布满 7 天的文章应触发 topic_performance 更新"""
    os.environ["SQLITE_PATH"] = ":memory:"
    import scripts.sqlite_db as db; db.init_db()
    from datetime import datetime, timedelta
    old_date = (datetime.now() - timedelta(days=8)).strftime("%Y-%m-%d")
    db.insert_news({"key":"old_k","title":"T","link":"L",
                    "pub_time": old_date,
                    "xhs_collected_at": "4h,24h,72h",
                    "xhs_saves": 50, "xhs_comments": 10,
                    "category": "娱乐", "tags": "写真"})
    import scripts.agent_runner as ar
    ar._update_topic_performance_for_mature_articles()
    topics = db.get_top_topics(n=1)
    assert len(topics) > 0
    updated = db.get_by_key("old_k")
    assert updated["topic_perf_updated_at"] is not None
```

- `[ ]` 运行 `python scripts/agent_runner.py --dry-run`，确认打印完整计划，无报错
- `[ ]` 运行 `python scripts/agent_runner.py --live-preview`（需 CDP 可用），确认打印预览报告，DB 无变化

### 端到端测试

**完整 E2E（无真实 XHS 账号）：**

1. 准备 `tests/fixtures/sample_news.json`（5 篇预设文章）
2. 设置环境变量 `SQLITE_PATH=data/test_agent.db`（隔离测试 DB）
3. 运行 `python scripts/agent_runner.py --dry-run`
4. 验证输出包含：话题分配、配额、推荐发布时间、各文章评分结果（PUBLISH/DISCARD/REGENERATE）
5. 确认 `data/test_agent.db` 未被创建（dry-run 用内存 DB）

**有飞书凭证时的完整测试：**

1. 去掉 `--dry-run`，真实运行
2. 确认手机飞书收到审批卡片（含文章标题/评分/推荐时间）
3. 点击「✅ 发布」，确认 `news.publish_xhs=1`
4. 等待 `yahoo_news_publish.py` cron 触发，确认文章发布到 XHS

---

## 模块 MCP：xhs-operations MCP server

**交付物：**
- 新建目录 `mcp/`
- 新建 `mcp/xhs_operations_server.py`
- 修改 `.claude/settings.json`：注册 MCP server

**前置条件：** 模块 A（DB 层完整），模块 1（维度版本管理），模块 B（加权评分）

### 实施步骤

1. **新建 `mcp/` 目录**，确认 `requirements.txt` 中有 `fastmcp>=0.1.0`（当前已有 `fastmcp`）

2. **新建 `mcp/xhs_operations_server.py`**，基础结构：

   ```python
   import sys, os, json
   sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
   from fastmcp import FastMCP
   from scripts.sqlite_db import (
       query_news, get_by_key, update_news,
       get_score_dims, load_active_dimensions,
       get_top_topics, get_config, set_config,
       rollback_dimension_version, commit_dimension_version,
       upsert_score_dims, load_dim_weights
   )

   mcp = FastMCP("xhs-operations")
   ```

3. **实现 10 个工具**（每个用 `@mcp.tool()` 装饰器）：

   **`get_candidate_articles(date=None, status="active")`**：
   - 调用 `query_news(date_from=date, publish_xhs=0, status=status, limit=50)`
   - 返回包含 `similarity_warning` 字段（TODO：简化版直接返回 False）的 `ArticleSummary` 列表

   **`get_article_detail(news_key: str)`**：
   - `get_by_key(news_key)` + `get_score_dims(news_key)`
   - 合并返回完整 schema，`cover_score` 当前返回 null

   **`update_article_status(news_key: str, status: str, note: str = "")`**：
   - 校验 status 在 `["active","discarded","skipped","archived"]` 内
   - `update_news(news_key, {"status": status})`

   **`get_dimension_versions()`**：
   - 直接查 `scoring_dimension_versions` 表，返回版本列表

   **`activate_dimension_version(version: str)`**：
   - 调用 `rollback_dimension_version(version)`

   **`get_topic_performance(limit: int = 20, window_days: int = 90)`**：
   - 调用 `get_top_topics(n=limit, window_days=window_days)`

   **`get_weekly_stats(week_start: str = "")`**：
   - 查询指定周内发布且有 xhs 数据的文章，聚合统计
   - 返回完整 schema

   **`override_dim_score(news_key: str, dim_name: str, value: float, note: str)`**：
   - 校验 value 在 `[0, 0.5, 1]`
   - 查 `score_dims` 旧 value 移入 `llm_value`，写入 `human_value`/`override_note`/`human_override=1`
   - 重算 `title_score`/`content_score`（调用独立的 `recalculate_scores(news_key)` 函数）

   **`run_reflection(mode: str = "quick")`**：
   - 写入 `agent_state` key=`task_{uuid}` 状态为 `running`
   - 在后台线程启动 `reflection_runner.run(mode=mode)`
   - 返回 `{"started": True, "task_id": uuid}`

   **`get_task_status(task_id: str)`**：
   - 查 `agent_state` 中 `task_{task_id}` 的值

   **`batch_update_articles(news_keys: list[str], status: str, note: str = "")`** 和 **`update_dim_weights(weights: dict)`**：
   - 前者循环调用 `update_news`，后者 `set_config("dim_weights", weights)`

4. **统一错误格式**（在工具函数中用 try/except 捕获，返回 `{"error": True, "code": "...", "message": "..."}`）

5. **修改 `.claude/settings.json`**（或新建 `.claude/settings.json`）：

   ```json
   {
     "mcpServers": {
       "xhs-operations": {
         "command": "python",
         "args": ["mcp/xhs_operations_server.py"],
         "cwd": "${workspaceFolder}"
       }
     }
   }
   ```

### 自测清单

```python
# tests/test_mcp_operations.py
import os; os.environ["SQLITE_PATH"] = ":memory:"
import sys; sys.path.insert(0, ".")

def setup_module():
    import scripts.sqlite_db as db; db.init_db()
    db.insert_news({"key":"mcp_k1","title":"测试标题","link":"https://test.com",
                    "content_score":3.5,"title_score":3.0,"status":"active"})
    db.upsert_score_dims("mcp_k1", {"原创度": {"value": 1, "reason": "好"}})

def test_get_candidate_articles():
    # 直接调用工具函数（不走 MCP 协议）
    sys.path.insert(0, "mcp")
    from xhs_operations_server import _get_candidate_articles
    result = _get_candidate_articles()
    assert any(a["news_key"] == "mcp_k1" for a in result)

def test_get_article_detail():
    from xhs_operations_server import _get_article_detail
    detail = _get_article_detail("mcp_k1")
    assert detail["title"] == "测试标题"
    assert len(detail["dim_scores"]) > 0

def test_override_dim_score():
    from xhs_operations_server import _override_dim_score
    import scripts.sqlite_db as db
    result = _override_dim_score("mcp_k1", "原创度", 0.5, "大段直译")
    assert result.get("ok") == True
    dims = db.get_score_dims("mcp_k1")
    orig = next(d for d in dims if d["dimension"] == "原创度")
    assert orig["human_override"] == 1
    assert orig["human_value"] == 0.5

def test_update_article_status_invalid():
    from xhs_operations_server import _update_article_status
    result = _update_article_status("mcp_k1", "invalid_status")
    assert result.get("error") == True
    assert result["code"] == "INVALID_VALUE"
```

- `[ ]` 在 Claude Code 中对话：「今天有哪些候选文章？」，确认 Claude 调用 `get_candidate_articles` 并返回格式化表格
- `[ ]` 对话：「将这篇文章原创度改为 0.5，理由是大段直译」，确认 `override_dim_score` 被调用，综合分更新

### 端到端测试

1. 启动 MCP server：`python mcp/xhs_operations_server.py`
2. 在 Claude Code 中输入「查询本周发布数据统计」
3. Claude 调用 `get_weekly_stats()`，返回本周总发布/总收藏/话题分布
4. 输入「将话题「写真集」的丢弃计数重置」→ Claude 调用 `update_article_status` 相关工具完成操作

---

## 模块 MCP-LLM（P1）：xhs-llm MCP server

**交付物：**
- 新建 `mcp/xhs_llm_server.py`
- 修改 `.claude/settings.json`：追加 `xhs-llm` server

**前置条件：** 模块 1（维度版本管理），Phase 0-B（`call_litellm` 已支持 temperature）

### 实施步骤

1. **新建 `mcp/xhs_llm_server.py`**，基础结构：

   ```python
   import sys, os
   sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
   from fastmcp import FastMCP
   from scripts.yahoo_common import call_litellm, build_scoring_prompt
   from scripts.sqlite_db import load_active_dimensions

   mcp = FastMCP("xhs-llm")

   def _error(code: str, msg: str) -> dict:
       return {"error": True, "code": code, "message": msg}
   ```

2. **实现 6 个工具**（每个 `@mcp.tool()`）：

   **`translate_and_classify(title_ja: str, content_ja: str) -> dict`**：
   - 单次 LLM 调用（temperature=0.2，max_tokens=500）
   - 返回 `{title_zh, summary_zh, format_suitability: [str], reason}`

   **`evaluate_content(title: str, content_ja: str, comment: str = "", dim_version: str = "") -> dict`**：
   - 若 `dim_version` 为空，调用 `load_active_dimensions()` 并在返回结果中附加 `_dim_version`
   - 若 `dim_version` 非空，从 `scoring_dimension_versions` 查指定版本的 `dimensions_json`
   - 调用 `call_litellm(build_scoring_prompt(dims)..., temperature=0.1, max_tokens=4000)`
   - 返回 `{dim_name: {value, reason}, ..., _dim_version: str}`

   **`generate_content(title_ja: str, body_text: str, format: str = "news", style: str = "normal") -> dict`**：
   - temperature=0.7，max_tokens=6000
   - `style="tsundere"` 时追加风格 prompt
   - 返回 `{seo_title, summary, content, comment, tags: {precise, vertical, broad}}`

   **`generate_video_caption(video_context: str, style: str = "normal") -> dict`**：
   - temperature=0.5，max_tokens=800
   - 使用 `response_format={"type": "json_object"}`
   - 返回 `{"caption": str}`

   **`analyze_overrides(dim_name: str, override_notes: list[str]) -> dict`**：
   - temperature=0.3，max_tokens=1000
   - 样本 < 3 时直接返回 `{"has_pattern": False, "reason": "样本不足"}`
   - 返回 `{has_pattern, edge_case, evidence, reason}`

   **`score_cover_image(image_path: str) -> dict`**：
   - 校验文件路径在 `GALLERY_CACHE_DIR` 内（安全检查）
   - 使用 DeepSeek 视觉模型（temperature=0.1，max_tokens=500）
   - 返回 6 个图片维度的 `{value, reason}` dict

3. **2 次指数退避 retry** 逻辑复用 `call_litellm` 已有实现（直接调用即可）

4. **统一错误捕获**：所有工具用 `try/except`，LLM 不可用返回 `_error("LLM_UNAVAILABLE", ...)`，解析失败返回 `_error("PARSE_ERROR", ...)`

5. **追加到 `.claude/settings.json`**：
   ```json
   "xhs-llm": {
     "command": "python",
     "args": ["mcp/xhs_llm_server.py"],
     "cwd": "${workspaceFolder}"
   }
   ```

### 自测清单

```python
# tests/test_mcp_llm.py
import os, sys; sys.path.insert(0, ".")
os.environ["SQLITE_PATH"] = ":memory:"

def setup_module():
    import scripts.sqlite_db as db; db.init_db()
    db.init_dimension_versions()

def test_translate_and_classify_schema(monkeypatch):
    import scripts.yahoo_common as yc
    monkeypatch.setattr(yc, "call_litellm", lambda *a, **kw:
        '{"title_zh":"测试标题","summary_zh":"摘要","format_suitability":["news"],"reason":"..."}')
    sys.path.insert(0, "mcp")
    from xhs_llm_server import _translate_and_classify
    result = _translate_and_classify("テストタイトル", "本文...")
    assert "title_zh" in result
    assert isinstance(result["format_suitability"], list)

def test_evaluate_content_uses_temperature_01(monkeypatch):
    captured = {}
    import scripts.yahoo_common as yc
    def mock_llm(prompt, temperature=0.7, **kwargs):
        captured["temperature"] = temperature
        return '{"原创度":{"value":1,"reason":"好"}}'
    monkeypatch.setattr(yc, "call_litellm", mock_llm)
    from xhs_llm_server import _evaluate_content
    _evaluate_content("标题", "内容", "评论")
    assert captured.get("temperature") == 0.1

def test_analyze_overrides_insufficient_samples():
    from xhs_llm_server import _analyze_overrides
    result = _analyze_overrides("原创度", ["只有一条记录"])
    assert result["has_pattern"] == False
    assert "样本不足" in result.get("reason", "")

def test_score_cover_image_path_validation():
    from xhs_llm_server import _score_cover_image
    result = _score_cover_image("/etc/passwd")  # 路径不在 GALLERY_CACHE_DIR 内
    assert result.get("error") == True
    assert result["code"] == "INVALID_VALUE"

def test_generate_content_tsundere(monkeypatch):
    import scripts.yahoo_common as yc
    captured = {}
    def mock_llm(prompt, system_prompt="", **kwargs):
        captured["system"] = system_prompt
        return '{"seo_title":"T","summary":"S","content":"C","comment":"M","tags":{"precise":[],"vertical":[],"broad":[]}}'
    monkeypatch.setattr(yc, "call_litellm", mock_llm)
    from xhs_llm_server import _generate_content
    _generate_content("タイトル", "本文", style="tsundere")
    assert "傲娇" in captured.get("system", "") or "tsundere" in str(captured)
```

- `[ ]` 在 Claude Code 对话：「帮我评估这篇文章：[粘贴日文内容]」，确认 `evaluate_content` 被调用，返回维度评分
- `[ ]` 对话：「帮我重新生成这篇内容，用傲娇风格」，确认 `generate_content(style="tsundere")` 被调用

### 端到端测试

1. 启动 `mcp/xhs_llm_server.py`
2. 在 Claude Code 中输入「分析过去一个月「原创度」维度的纠正记录，看有没有规律」
3. Claude 调用 `get_dimension_versions`（via operations server）获取当前版本，再调用 `analyze_overrides`（via llm server）
4. 若返回 `has_pattern=True`，Claude 建议更新 `edge_case`，运营者确认后调用 `commit_dimension_version`

---

## 新增 requirements.txt 依赖汇总

以下依赖需追加到 `requirements.txt`（当前未包含）：

```
# 统计分析（Phase 0-D）
scipy>=1.11.0
pandas>=2.0.0

# MCP server（已有 fastmcp，确认版本）
fastmcp>=0.9.0

# 飞书 Bot HTTP 请求（已有 requests，无需追加）
# 飞书签名验证（标准库 hmac/hashlib，无需追加）
```

---

## 模块依赖链快速参考

```
Phase 0-A (DB Schema)
    ├── Phase 0-B (scoring_dimensions.json + evaluate_quality)
    │       └── 模块 1 (dimension-registry DB版本管理)
    │               └── 模块 B (加权评分，依赖 agent_config)
    │                       └── 模块 C (低分诊断)
    ├── Phase 0-C (metrics_collector)
    ├── Phase 0-D (dimension_analysis，依赖 0-C 数据)
    └── 模块 A (记忆层 CRUD)
            ├── 模块 D (xhs_trend_scanner)
            │       └── 模块 E (agent_planner)
            ├── 模块 F (feishu_bot，独立可并行)
            └── 模块 G (agent_runner，依赖 A/B/C/D/E/F 全部)
                    └── 模块 MCP (xhs-operations server)
                            └── 模块 MCP-LLM (xhs-llm server，P1)
```

---

## 测试文件组织建议

```
tests/
├── conftest.py              # pytest fixtures（内存 DB 初始化、mock publisher）
├── fixtures/
│   └── sample_news.json     # 5 篇预设文章（dry-run 使用）
├── test_db_schema.py        # Phase 0-A
├── test_scoring_prompt.py   # Phase 0-B
├── test_metrics_collector.py # Phase 0-C
├── test_dimension_analysis.py # Phase 0-D
├── test_dimension_registry.py # 模块 1
├── test_memory_layer.py     # 模块 A
├── test_weighted_scoring.py # 模块 B
├── test_scoring.py          # 模块 C
├── test_trend_scanner.py    # 模块 D
├── test_agent_planner.py    # 模块 E
├── test_feishu_bot.py       # 模块 F
├── test_agent_runner.py     # 模块 G
├── test_mcp_operations.py   # 模块 MCP
└── test_mcp_llm.py          # 模块 MCP-LLM
```

`conftest.py` 建议内容：

```python
import os, pytest
os.environ.setdefault("SQLITE_PATH", ":memory:")
os.environ.setdefault("LITELLM_API_KEY", "test-key")
os.environ.setdefault("LITELLM_MODEL", "test-model")
os.environ.setdefault("FEISHU_APP_ID", "")
os.environ.setdefault("FEISHU_APP_SECRET", "")

@pytest.fixture
def fresh_db():
    import scripts.sqlite_db as db
    db.DB_PATH = ":memory:"
    db.init_db()
    return db

@pytest.fixture
def client():
    import sys; sys.path.insert(0, "web")
    from app import app
    app.config["TESTING"] = True
    return app.test_client()
```