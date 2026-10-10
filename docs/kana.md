# 假名处理（kana）

写稿要把日文假名（人名/组合/节目/作品名）中文化，且入库硬门禁要求**正文+标题假名 ≤5**。分两层：

## 1. 机械替换（`services/kana.py::replace`）
- 读 **`config/kana_replace.json`**（词条→中译，长词优先）做全局 `replace`。
- **只在字典命中时才换**；字典没有的（新词/碎片）原样保留 → 就是「假名>5」的来源。
- 附带清理：`・`→`·`、双重书名号 `《《…》》`→`《…》`。

## 2. LLM 整句重译（改稿循环）
`_produce` 门禁不过时（attempts>1）把残留假名反馈回 LLM，要求其**重新输出新的 title/body**：
- 反馈**带整句上下文**（不是只给几个假名），让 LLM 能整词/整条作品名重译。
- 示例反馈：
  ```
  残留假名：把下列整句里含假名的词/作品名/节目名翻成中文或罗马字（标题+正文都要，不要留下任何假名）：
      · 10月23日开幕的《春よ来い、マジで来い》，是她第一次正经站上舞台
  ```

## 3. 新词登记（`new_terms` / `log_pending`）
写稿结束自动跑 `log_pending()`，把残留假名候选追加到 **`data/kana_pending.jsonl`**（去重）。候选来源：
- 连续假名 ≥2 的片断；
- **《…》/「…」里含假名的整段作品名**（如 `春よ来い、マジで来い`）。

## 4. 自动入表（`auto_promote`）
- LLM 批量把 pending 词条**中译** → 写入 `config/kana_replace.json`（先自动、人工后审）。
- 审计落 **`data/kana_autopromoted.jsonl`**（term/zh/ctx/ts）。
- 批次写稿末尾自动调用；也可 `python -m cli kana autopromote [--dry-run]`。
- **片段防护**（`_is_fragment` + 规则）：跳过
  - 长度 <3、助词（から/まで/あり…）、首尾长音符 `ー`；
  - **紧邻汉字的碎片**（如 `败れざる者` 里的 `れざる`、`レーザー` 里的 `ーザー`）——防全局替换污染。

## 5. 人工复核（自动入表）
- 页面 **`/kana-autopromoted`**（素材页「🔤 假名词表」）：列表 + 语境 + **一键撤回**。
- API：`GET /api/kana-autopromoted`、`POST /api/kana-autopromoted/<term>/revert`。
- CLI：`python -m cli kana autolist` / `python -m cli kana revert <term>`。

## 原则
- 字典只收**完整词条**（人名/组合/节目/作品名）。
- **碎片/混排**（`败れざる者`、`卡兹雷ーザー`）交给 **LLM 看整句重译**，不退字典。
- 旧 skill 经验：`references/10-01-length-and-patch-pitfalls.md`、`first-person-testimony-renwei-fp.md`。

## 测试
`tests/services/test_kana.py`（replace/count/new_terms 整段作品名/auto_promote 片段防护）。
