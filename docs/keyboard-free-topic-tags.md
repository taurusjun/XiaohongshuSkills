# 话题标签写入方案（实测修订版）

> 调查/实测时间 2026-10-05 · 远端分支 `dev2` · 所有结论均有线上实测支撑

## 一、前提修正：标签并不需要 VNC

原问题「必须开 VNC 才能输入 tag 成功」。**实测证伪**：无 VNC 的健康标签页上，焦点、按键、下拉、点选、chip 生成全部正常：

| 检查项 | 结果 |
|---|---|
| `document.hasFocus()` | True |
| JS `focus()` 后焦点在编辑器内 | True |
| `Input.dispatchKeyEvent` 敲 `#AKB` | 编辑器文本变成 `#AKB` ✓ |
| 话题下拉 | 50 个可见 `.item` ✓ |
| 点选 → chip | ✓ 带真实 topic id |

真正会让整条流程崩掉的是另一个 bug：**`DOM.setFileInputFiles` 收到相对路径会让渲染进程永久卡死**（`Runtime.evaluate`/`Page.enable` 全部超时、不可恢复）。它发生在上传阶段（标签之前），容易被误读成「标签需要 VNC」。

**因此本方案的目标是「更稳、更快、可校验」，不是「让标签能用」。**

## 二、实测得到的三个关键事实

### 1. Tiptap Editor 实例可直接拿到 ✅

```js
document.querySelector('div.ProseMirror').editor   // Tiptap Editor: .state/.view/.schema/.commands/.getJSON()
```

（`el.pmViewDesc` 存在但被压缩过、没有 `.view`；`el.editor` 才是可用入口。）

节点类型：`paragraph, mention, doc, text, poi, travel, moment, instant, topic, user, image`

### 2. chip 的文档表示 ✅

```json
{"type":"topic","attrs":{"data":{
   "id":"61922f820000000001006a43",
   "link":"xhsdiscover://topic/v2/61922f8235e0a9000146cf1f",
   "name":"AKB"}}}
```

**纯 Tiptap 事务插入已实测成功**（`chips: 2->3`，无需键盘/API/下拉）：

```js
ed.chain().focus('end').insertContent({
  type: 'topic',
  attrs: {data: {id, link, name}}
}).run()
```

### 3. 话题搜索 API 不能直接调 ❌

```
POST /api/galaxy/v2/creator/servicegw/v2/search/topics
Body {"keyword":"欅坂","topic_round_start_time":<ms>,"content":"<编辑器纯文本>"}
→ 真实响应: {"code":0,"data":{"topics":[{"topic_info_dto":{
     "id":"5c5c5bd9000000000d039cbe","name":"欅坂",
     "link":"xhsdiscover://topic/v2/5c5c5bda097875000108684e", ...}}]}}
```

但页面内 `fetch()` 裸调 **返回 406 + `{"code":-1,"success":false}`** —— 该接口要求页面 JS 生成的签名 header（XHS 的 `x-s`/`x-t` 体系），**无法从外部复现**。

**结论：新话题的 id 只能由页面自己的请求路径产生（即敲字触发），没有捷径。**

## 三、方案：两级缓存

| 层级 | 条件 | 动作 | 依赖 |
|---|---|---|---|
| **Tier 1** | 缓存命中 | 纯 Tiptap 事务插入 | 无（零键盘/零 API/零下拉）**已实测通过** |
| **Tier 2** | 缓存未命中 | 敲字触发页面签名请求 → 取 id → 点精确匹配项 → 回写缓存 | 键盘（已验证无 VNC 可用） |

### 缓存表

```sql
CREATE TABLE IF NOT EXISTS topic_cache (
  name       TEXT PRIMARY KEY,   -- 话题名（不含 #）
  topic_id   TEXT NOT NULL,
  link       TEXT NOT NULL,
  source     TEXT,               -- 'harvest' | 'publish'
  updated_at TEXT
);
```

（按规则 9，只经 `sqlite3` CLI 或 Python `sqlite3` 模块操作。）

### Tier 1 实现（零键盘）

```js
(() => {
  const ed = document.querySelector('div.ProseMirror').editor;
  const cnt = () => document.querySelectorAll('div.ProseMirror a.tiptap-topic').length;
  const before = cnt();
  ed.chain().focus('end').insertContent({
    type: 'topic', attrs: {data: {id: ID, link: LINK, name: NAME}}
  }).run();
  return {ok: cnt() > before, chips: before + '->' + cnt()};
})()
```

### Tier 2 实现（对现有 `_select_topics` 的三处加固）

1. **轮询下拉**而非固定 1.5s
2. **精确匹配**：取 `.item` 首行、去掉 `#` 与浏览量后缀，与目标名**全等**才点 —— 消除 `AKB`→`AKB48`、`乃木坂`→`乃木坂46` 误配（生产库里这两组真实并存：`AKB` 41 次 / `AKB48` 13 次 / `AKB,AKB48` 16 次）
3. **以 chip 数量为准校验**：`a.tiptap-topic` 前后对比，失败写入 `failed_tags` 并明确打印（现有代码 `failed_tags` 从未被 append、`clicked` 从未被使用，失败是静默的）

顺带：从 CDP `Network` 抓该接口响应，可直接得到权威 `{id, link, name}` 并回写缓存（`metrics_collector` 已有同款抓包模式可复用）。

### 缓存预热（一次性，可选）

对 DB 里出现过的去重标签（约 30 个：`AKB`/`AKB48`/`乃木坂`/`乃木坂46`/`欅坂`/`日向坂`/`cosplay`/`コスプレ`/`美人`…）跑一次 Tier 2 采集，把 id 全部落库。之后常规发布**几乎全部走 Tier 1**，实现事实上的零键盘。

## 四、兜底与降级

| 失败点 | 动作 |
|---|---|
| Tier 1：`el.editor` 取不到（Tiptap 升级） | 降级 Tier 2 |
| Tier 1：`insertContent` 抛错 | 降级 Tier 2 |
| Tier 2：下拉未出现 | 计入 `failed_tags`，明确报错 |
| Tier 2：无精确匹配 | 计入 `failed_tags`，**不猜**（宁可少一个话题，不要错配） |
| 两级都失败 | `❌ Topic NOT selected: {tag}` + 写 error log（规则 6） |

## 五、收益

- 缓存命中时**完全不依赖**键盘、焦点、下拉渲染 → 无 VNC / headless 稳定
- 精确匹配消除同族话题误配
- 可校验，不再有假成功
- Tier 1 省掉每标签 1.5s 固定等待 + 逐字符 0.06s 延迟

## 六、风险

- `el.editor` 是 Tiptap 实现细节，非公开契约 → 必须有 Tier 2 兜底
- 缓存会陈旧（话题 id 一般稳定，风险低）；可加 TTL 或失败时失效重取
- Tier 2 仍依赖键盘 —— 这是签名机制决定的，无法绕过
