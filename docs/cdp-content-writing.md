# CDP 如何向小红书页面写入内容

> 调查时间 2026-10-05 · 远端分支 `dev2` · 全部行号对应 `scripts/cdp_publish.py`（除注明外）

## 一句话结论

**它从不直接赋值**，而是针对三类元素用三种不同写法 —— 因为小红书是 Vue + ProseMirror，naive 的 `el.value = x` / `el.textContent = x` 框架收不到，值会在提交时丢掉。

| 目标 | 手法 | 原因 |
|---|---|---|
| 标题、定时时间（`<input>`） | 原生 setter + 手动派发事件 | 绕过 Vue 对 `value` 的劫持 |
| 正文（contenteditable） | 直接建 DOM 节点 | 富文本编辑器没有 `value` |
| 图片（`<input type=file>`） | `DOM.setFileInputFiles` | 浏览器安全限制，JS 无法设值 |
| 话题标签 | **真实键盘事件** | 只有真实按键才会生成带话题 ID 的 chip |
| 按钮点击 | `Input.dispatchMouseEvent` | 真实鼠标事件才触发框架 handler |

## 传输层

- `_send()`（`:631`）—— CDP WebSocket 请求/响应，带 message id 匹配；收到不匹配的帧（事件）就跳过；遇到 `Promise was collected`（code `-32000`）抛 `_PromiseCollectedError` 供上层重试
- `_evaluate()`（`:898`）—— 封装 `Runtime.evaluate`，`returnByValue: true` + `awaitPromise: true`；**带一次重试**，专门应对 Chrome 在内存压力下（尤其图片上传期间）GC 掉 Promise 的 bug；JS 执行出错会抛 `CDPError`

## 1. 标题 —— 原生 setter + 事件

`_fill_title()`（`:4669`）

```js
var nativeSetter = Object.getOwnPropertyDescriptor(
    window.HTMLInputElement.prototype, 'value').set;
el.removeAttribute('maxlength');        // 先拆掉字数限制
el.focus();
nativeSetter.call(el, "标题");           // 绕过 Vue 对 value 的劫持
el.dispatchEvent(new Event('input',  {bubbles: true}));
el.dispatchEvent(new Event('change', {bubbles: true}));
el.blur();
```

**为什么必须这么绕**：Vue 把 `value` 劫持成受控组件，直接 `el.value = x` 只改 DOM、不触发框架 onChange，框架内部状态仍为空 —— 点发布时提交的是空标题。用**原型上的原生 setter** 赋值，再手动 `dispatchEvent` 通知框架状态已变。

## 2. 正文 —— 直接建 DOM 节点

`_fill_content()`（`:4697`）

```js
while (el.firstChild) el.removeChild(el.firstChild);   // 清空
for (const line of text.split("\n")) {
  const p = document.createElement("p");
  if (line) p.textContent = line;
  else p.appendChild(document.createElement("br"));    // 空行必须插 <br>
  el.appendChild(p);
}
el.dispatchEvent(new Event("input", {bubbles: true}));
```

三个细节：

- **空行插 `<br>`** —— 空的 `<p>` 会被 ProseMirror 折叠，段落间距消失
- **编辑器多级探测** `_find_content_editor_selector()`（`:4181`）：`div.tiptap.ProseMirror` → `div.ProseMirror[contenteditable]` → `div.ql-editor`（小红书换过编辑器）
- **兜底定位**：若选中元素不可见（`offsetParent === null`），改找 `[data-placeholder]` 含「输入正文描述」的节点，再向上 5 层内找 `role="textbox"` 的祖先

## 3. 图片 —— 走 CDP 的 DOM 域

`_upload_images()`（`:4530`）

```python
node_id = self._query_node_id(".upload-input")   # DOM.getDocument → DOM.querySelector
self._send("DOM.setFileInputFiles", {"nodeId": node_id, "files": [path]})
```

- 文件 input 有安全限制，**JS 设不了值**，必须走 CDP
- 每张传完回读 `el.files.length` 校验；为 0 就换备用选择器（`input[type="file"]`）重试一次
- 每张传完 `_wait_for_uploaded_images(index)`（`:4140`）轮询预览图数量（`_count_uploaded_images()` `:4119` 用 4 个候选选择器取最大值）—— **确认服务端真收下了**，不是本地塞进去就算
- 传之前先 `_activate_current_tab()`（`:4515`）：change 事件在后台标签页会被节流

## 4. 话题标签 —— 唯一用真实按键的地方

`_select_topics()`（`scripts/publish_pipeline.py:185`）

```python
publisher._send("Input.dispatchKeyEvent", {"type": "keyDown", "key": ch, "text": ch})
publisher._send("Input.dispatchKeyEvent", {"type": "keyUp",   "key": ch})
```

流程：敲 `#` + 标签名 → 等 1.5s（带 jitter）让联想下拉弹出 → JS `el.click()` 点第一个匹配的 `.item`。

代码注释写得很直白：

> `execCommand/insertText bypasses XHS event handlers and never creates topic chips.`

**只有真实按键事件**才会让小红书把它转成带真实话题 ID 的 tiptap-topic chip；用 JS 插文字只是一段普通文本，**没有话题权重**。

配套细节：
- 敲之前先用 `Input.dispatchMouseEvent` 在编辑器中心**点一下鼠标** —— 注释：没有 VNC 时纯 `focus()` 不足以让编辑器接收键盘输入
- 换行发真实 Enter `keyDown`/`keyUp`（ProseMirror 需要真实 Enter 才建新段落），不是 `\n`
- 下拉确认用 JS `el.click()` 而非 `dispatchMouseEvent`/`ArrowDown+Enter` —— 注释：后两者在没有活动显示器时不可靠

### 与上游的衔接（标签为什么在正文最后一行）

`yahoo_news_publish.py` 把标签拼成正文的最后一行：

```python
tags_str = " ".join(f"#{t}" for t in info.get("tags", [])[:10])
xhs_content = f"{xhs_content}\n{tags_str}"
```

`publish_pipeline.py:137` 的 `_extract_topic_tags_from_last_line()` 再把这一行**摘出来**，交给 `_select_topics()` 用真实按键敲进去。所以「正文最后一行放 `#标签`」这个约定（README / SKILL.md 里写的）真正的含义是：**那一行不是正文，是话题选择指令**。

## 5. 定时发布时间 —— 同样的原生 setter 套路

`_set_schedule_post_time()`（`:4769`）

1. 点 `.post-time-wrapper .d-switch` 开关（读 `aria-checked` 判断是否已开）
2. 原生 setter 设日期 input 的值 + `input`/`change`/`blur` 事件

特意拆成**两个同步 evaluate**（不用 async/await），注释说明：避免 Chrome 在后台标签页把 Promise GC 掉。

## 6. 点击按钮 —— 真实鼠标事件

`_click_publish()`（`:4961`）

- `_wait_for_publish_button_ready()`（`:4365`）先等按钮可用（视频模式还要等转码）
- `scrollIntoView` 后用 `_get_publish_button_rect()`（`:4227`）取 `xhs-publish-btn` 矩形中心
- `Input.dispatchMouseEvent` 发 `mousePressed` / `mouseReleased`，**不是 `el.click()`**（话题下拉那个特例除外）

## 贯穿全篇的技巧：伪装页面可见

填正文、写话题、设时间、点发布之前都会先跑：

```js
Object.defineProperty(document, 'visibilityState', {get: () => 'visible', configurable: true});
Object.defineProperty(document, 'hidden', {get: () => false, configurable: true});
```

**为什么必须**：Chrome 无显示器 / VNC 断开时页面被判为 hidden，Vue 的事件处理器与 rAF 会被节流或跳过 —— 症状是「事件发出去了但框架没反应」。伪装成 visible 直接绕过。

## 小红书改版时的排查顺序

按「最可能被改版打断」排序：

1. `SELECTORS`（`:126`）—— 所有选择器集中在此，先核对 `image_text_tab` / `upload_input` / `title_input` / `content_editor` / `publish_button`
2. 编辑器选择器链 —— `_find_content_editor_selector()`（`:4181`）的三级探测是否还有命中的
3. 上传确认 —— `_wait_for_uploaded_images()`（`:4140`）的预览图选择器
4. 话题下拉 —— `_select_topics()` 里 `.item` 的 DOM 结构和匹配文本
5. 定时开关 —— `.post-time-wrapper .d-switch` 的 `aria-checked` 语义

## 已知脆弱点

- **话题标签依赖下拉文本匹配**：`el.innerText.includes(tag)` 是子串匹配，标签名互为子串时（如 `AKB` 与 `AKB48`）可能点错；且只点第一个匹配项，不校验是否真的生成了 chip
- **正文清空是破坏性的**：`while (el.firstChild) removeChild` 直接清空编辑器，若页面已有草稿内容会被一并抹掉
- **`files.length` 只证明文件进了 input**，最终仍靠预览图数量间接确认上传成功
