# 公众号编辑器图片块：三个 bug 与两个坑

> 调查/修复时间 2026-10-06 · 远端分支 `dev2` · 提交 `9f816a8` / `9c81691` / `24d67ac`
> 涉及文件：`web/wechat_views.py`（单文件，EditorJS 内嵌在 Python 模板字符串里）

## 一、结论速览

| # | 症状 | 根因 | 提交 |
|---|---|---|---|
| 1 | 图片位置显示 `The block can not be displayed correctly.` | 块类型是 `image`，但 EditorJS `tools` 没注册 `image` | `9f816a8` |
| 2 | 图片能显示，但改不了（只有说明可编辑） | 官方 `ImageTool` 已填图状态下**没有**换图/删除入口 | `9c81691` |
| 3 | 换图弹窗里有一张裂图 | 素材库混入远程封面 URL，缩略图无条件拼 `/local-image?path=` | `24d67ac` |

三个 bug 都**不是**图片文件或 `/local-image` 接口的问题 —— 该接口实测返回 200 + 83006 字节正常 JPEG。

## 二、链路：图片在公众号编辑器里怎么走

页面 `/wechat/<key>`，EditorJS 挂在 `#wxEditorjs`。

正文里的图片**不是** HTML，而是纯文本标记，存在 `news.wechat_content`：

```
【图片1：/Users/user/.cache/xhs_images/<key>/article_00.jpg】      ← 单路径 → image 块
【图片2：/a.jpg|/b.jpg】                                          ← 多路径 → galleryImage 块
```

| 环节 | 函数 | 位置 |
|---|---|---|
| 加载（文本 → 块） | `_txt2blocks(txt)` | `:328` |
| 保存（块 → 文本） | `_blocks2txt(blocks)` | `:317` |
| 侧栏缩略图插入 | `insImg(path)` → `_ed.blocks.insert('image', …)` | `:303` / `:307` |
| 路径 ↔ 代理 URL | `_imgUrl()` / `_imgPath()` | `:311` / `:312` |
| 选图弹窗 | `openImgPicker(cb)` | `:356` |
| 多图块 | `class GalleryImageBlock` | `:380` |
| 编辑器初始化 | `_initEd()` | `:427` |

编辑器内用 `/local-image?path=<本地路径>` 代理显示（浏览器与服务器不同机，`file://` 会被跨协议拦），存回正文时用 `_imgPath()` 还原成真实本地路径。

## 三、Bug 1：块类型 `image` 没有对应工具

`_txt2blocks:347` 与 `insImg:307` 产生的块类型是 `'image'`：

```js
if(ps.length===1&&(ps[0].indexOf('http')===0||ps[0].indexOf('/')===0))
  blocks.push({type:'image',data:{file:{url:_imgUrl(ps[0])},…}});
```

而 `_initEd` 的 `tools` 只注册了 `header` / `quote` / `galleryImage`。EditorJS 找不到名为 `image` 的 tool，就把块渲染成 `ce-stub`：

```
DIV.ce-stub__subtitle → ce-stub__info → ce-stub → ce-block__content → ce-block
  → codex-editor__redactor → codex-editor → DIV#wxEditorjs
```

正文里 2 个单路径标记 ↔ 浏览器里 2 个 stub，数量精确对应。

**佐证**：`@editorjs/image@2.10.3` 这个脚本一直在 `:515` 加载，却从未注册 —— 就是漏了这一步。

**修法**：注册 `image:{class:…}`。注册前先查了 UMD 源码确认预填数据能直接渲染：

```js
data.file = i || {url:""},  i && i.url && this.ui.fillImage(i.url)
```

且 `config.uploader` 是可选的（`this.config.uploader && typeof … === "function"` 有守卫）。

## 四、Bug 2：官方 `ImageTool` 已填图后不可换图

`@editorjs/image` 2.10.3 的 `render()` 全文：

```js
render(){return this.toggleStatus("empty"), this.nodes.wrapper}
fillImage(i){ …创建 <img>… this.toggleStatus("filled") }
```

节点只有 `image-tool__image` / `image-tool__image-preloader` / `image-tool__image-picture` / `image-tool__caption`。**上传区（`imagePreloader` + `fileButton`）在 filled 状态下被隐藏**，所以插入后只能改说明，换不了图。这是官方工具本身如此，不是注册漏了。

**修法**：子类化官方工具，在块内注入「🔄 换图 / ✕ 删除」，两个动作都走 EditorJS 自身的 `blocks` API：

```js
_swap(path){                                   // 换图
  const i=this._index(); if(i<0)return;
  const go=()=>this.api.blocks.insert('image',{file:{url:_imgUrl(path)},…},{},i,true);
  const r=this.api.blocks.delete(i);
  if(r&&typeof r.then==='function')r.then(go);else go();   // delete 在部分版本返回 Promise
}
_del(){ const i=this._index(); if(i>=0)this.api.blocks.delete(i); }
```

`_index()` 优先 `blocks.getBlockIndex(block.id)`，回退到按 `holder.contains(wrapper)` 匹配 —— 因为工具实例拿到的 `block` 是 BlockAPI，`id` 未必在所有版本都在。

块类型仍是 `image`，`insImg` 的插入路径不变。

**关于 `blocks.update()`**：`ImageTool` 里 `update` 出现 **0 次**（无 `update()` 方法），所以没走 `update`，改用 `delete` + `insert` 同位重建。

## 五、Bug 3：素材库混入远程封面 URL

`all_images` 的构造（`wechat_views.py:787` 起）分三步，第 2 步无条件收 `news.image_url`：

```python
# 2. Cover image from news.image_url
if news.get('image_url'):
    _add_imgs([news['image_url']], '封面')
```

未手动换过封面时 `image_url` 仍是 Yahoo 的远程地址：

```
https://newsatcl-pctr.c.yimg.jp/t/amd-img/20261005-01443521-mvwalk-000-1-view.jpg?exp=10800
```

两个后果：

1. **与侧栏封面重复** —— 同一个封面在素材列表里又出现一次；
2. **素材库混入远程地址** —— 正文图片必须本地（发布时经 `media/uploadimg` 换成 `mmbiz.qpic.cn` 地址），远程 URL 写进正文微信显示不出来。

而侧栏模板 `:187` 对远程有判断，**选图弹窗与 `GalleryImageBlock` 没有**（修复后分别是 `:371` / `:397`，内容已改为 `img.src=_imgUrl(p)`），原先无条件拼 `/local-image?path=`，远程 URL 被当成服务器本地路径 → 404：

```
192.168.0.53 GET /local-image?path=https://newsatcl-pctr.c.yimg.jp/... 404
```

**修法**：`:814` 只收以 `/` 开头的本地路径；两处缩略图改用已有的 `_imgUrl()`。封面本身在侧栏 `sbCvImg` 单独渲染（模板 `:169` 已兼容远程），不受影响。

## 六、坑 A：EditorJS 异步加载 vs 类声明求值顺序

**这是本轮最贵的一个错**，症状是编辑器**整片空白**，控制台也不显眼。

EditorJS 及三个工具由 `L()` 四级回调异步加载（`:512`→`:515`）：

```js
L(editorjs.js, ()=>L(header.js, ()=>L(quote.js, ()=>L(image.js, ()=>_initEd()))));
```

而内联 `<script>` 里的 `class X extends ImageTool` 是**在页面解析时求值**的（类声明不像函数声明那样延迟）。那一刻 `ImageTool` 还不存在 →

- `ReferenceError` → **整段内联脚本中断** → `_ed` 从未创建 → 编辑器空白、`stubs:0`、`imgs:[]`。

**规则**：凡是 `extends` 一个异步加载的 CDN 类，类定义必须放在回调链末端（本例是 `_initEd()` 内部）。同时 `_initEd` 的前置检查补上 `typeof ImageTool==='undefined'`。

> 函数声明会被提升，所以 `class GalleryImageBlock`（不继承任何东西）放在顶层一直没问题 —— 这也是为什么这个坑第一次踩到时不容易联想。

## 七、坑 B：探测 UI 会触发自动保存，污染生产数据

编辑器有自动保存（`:309`）：

```js
function _markDirty(){_dirty=true;…;_stimer=setTimeout(function(){if(_dirty)doSave(true);},4000);}
```

**任何 `onChange` 之后 4 秒就会 `PUT /api/wechat/<key>` 写库。** 用 CDP 探测时点一下「换图」并选图，4 秒后这条 news 的 `wechat_content` 就被改掉了 —— 我这次就踩了，一度以为数据丢了。

**安全做法**（三选一）：

1. **只读探测** —— 读 DOM、读 `_ed`、读 `_allImgs`，不点任何触发 `onChange` 的控件；
2. **抢窗口** —— 点完立刻 `GET /json/close/<tab_id>` 关掉标签页（< 4s），并用 DB 的 sha1 前后对比确认没写库；
3. **验证前先记基线** —— `sqlite3 … "SELECT wechat_content …" | shasum`，事后比对。

**事后取证**：`~/db-backup/news_dev.db.prev`（上一小时备份，`com.xhs.db-backup` 每小时 :17）与线上逐字节比对，可判定是否真的被改。本轮就是这样确认「污染已被覆盖回原状、sha1 一致」的。

## 八、验证记录（真实 Chrome，非推断）

修复前：

| 检查项 | 值 |
|---|---|
| `#wxEditorjs .ce-stub` | **2** |
| `#wxEditorjs img` | 0 |

修复后（只读探测，13s 等待）：

| 检查项 | 值 |
|---|---|
| 页面 JS 异常 | 0 |
| `ce-stub` | 0 |
| `image-tool--filled` | 2，`naturalWidth` 均 640 |
| 控件 | `display:flex`，按钮 `[🔄 换图, ✕]` |
| `_ed.configuration.tools` | 含 `image` 与 `galleryImage` |
| 保存回写 | `【图片N：<真实本地路径>】`，无 `/local-image` 泄漏 |

点 ✕（4s 窗口内关页，DB sha1 未变）：图片块 2→1、img 2→1、`stub` 0。

素材列表：4 项（含 1 个远程）→ 3 项全本地，残留远程 URL `[]`；3 张缩略图 `naturalWidth` 640/426/640 全部加载；侧栏封面仍为远程且 `nw=2048`。

`pytest -q --ignore=tests/test_gap1_collected_at.py` → 38 passed。

## 九、未决

**正文里的远程图片 URL**：目前 `_imgUrl()` 允许选到远程 URL 并插入正文。`wechat_publisher.publish_article` 只上传以 `/` 开头的本地路径（`used_paths` 里 `if inner.startswith("/")`），远程 URL 会原样进 HTML → 微信正文显示不出来。

本轮通过「素材库只收本地路径」从源头避免了（Bug 3），但**发布路径本身仍未处理远程 URL**。若要彻底兜住，应在 `publish_article` 里对 `http` 开头的标记先 `_download_cover()` 落本地再 `_upload_image_for_content()`。尚未实施。
