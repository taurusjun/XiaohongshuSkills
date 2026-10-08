# en_tweet 生成规则

## 什么时候生成

写中文稿的**同一轮 LLM 调用**中一并生成。不分两次。

## 硬约束

- **≤280 chars**（len() 计数，含空格和 hashtag）
- **纯英文** — 禁止日文（ひらがな/カタカナ/漢字）和中文字符
- **hashtag 必须用英文或罗马字** — 正确：#FRUITSZIPPER #AKB48 #ChoTokimekiSendenbu #NakagawaRunka，错误：#仲川瑠夏 #超心动宣传部 #ときめき宣伝部
- **禁止泛标签** — #JPop #idol #JPOP #IDOL #Japan
- **2-4 个具体 hashtag**，从素材 tags 字段提取

## 内容要求

- 必须包含：名场面或结果 + 钩子（为什么值得点开看）
- 不是陈述"进行了XX比赛"，而是"谁在XX中做了什么"
- 如果素材有 image_url，可在推文中附带图片提示

## 常见错误

1. **LLM 输出带前缀**（"Here is the tweet:"、"推文："、`"`引号包裹）→ 写代码清洗
2. **hashtag 用了中文/日文**（#仲川瑠夏）→ prompt 里给具体正反示例
3. **超 280 不重写** → 代码层做循环检测，超了重采
4. **同一批多篇，hashtag 雷同** → 各篇的 hashtag 应反映各自素材的独特 tags

## 清洗代码参考

```python
tweet = llm_response.strip()
for prefix in ["Here is the tweet:", "推文:", "Tweet:", "tweet:"]:
    if tweet.startswith(prefix):
        tweet = tweet[len(prefix):].strip()
tweet = tweet.strip('"').strip("'").strip()
```
