# 多页文章抓取流程（7/11经验）

当用户提供一个URL指向**分页长文**（如映画チャンネルの5ページ構成）时，内容分散在多个页面中，而DB中该key的`content_ja`可能只包含页面1的片段摘要，不足以覆盖全文。

## 适用场景

- 用户发来URL说「这篇文章有价值」
- DB中已有该key（可能是自动抓取），但`content_ja`是简短摘要而非全文
- 页面有分页导航（`./2/`、`./3/`、nextpage等）

## 流程

### 1. 下载首页
```bash
curl -sL --noproxy '*' "https://eigachannel.jp/movie/189329/" -o /tmp/eiga_p1.html
```

### 2. 识别分页结构
```bash
# 找nextpage
grep -oP 'nextpage[^>]*>.*?</div>' /tmp/eiga_p1.html
# 找分页链接
grep -oP 'href=["'"'"'][^"'"'"']*/\d+/["'"'"']' /tmp/eiga_p1.html | sort -u
```

### 3. 下载所有页面
```bash
for i in 2 3 4 5; do
  curl -sL --noproxy '*' "https://eigachannel.jp/movie/189329/$i/" -o "/tmp/eiga_p${i}.html"
done
```

### 4. 提取正文
Python提取`<h2>`、`<h3>`、`<p>`标签内容，过滤掉无关模块（侧栏、推荐阅读等）：
```python
h2s = re.findall(r'<h[23][^>]*>(.*?)</h[23]>', html, re.DOTALL)
ps = re.findall(r'<p[^>]*>(.*?)</p>', html, re.DOTALL)
```

过滤词（各站不同）：「バイオハザード」「コーヒーが冷め」「スパイダーマン」「デューン」「ゴジラ」「映画チャンネル」「広告」「当サイト」「TOP」

### 5. 写稿
- 新改写应覆盖全文内容，不只覆盖首页出现的内容
- 原有key的改写（如果是单页摘要版本）应替换为全文改写
- 入库时设置`related_keys`关联到中村丽乃等同系列素材

## 注意

- 有些站点的`detail`端点key和`list`端点key后缀不一致。始终用list端点的key
- 写稿前先读DB已有`rewritten_title`/`rewritten_content`判断是否需要覆盖
- 如果页面是WordPress（通常有`wp-json/oembed`），可用REST API直接获取内容
