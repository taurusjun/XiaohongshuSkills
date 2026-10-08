# 小红书字数计算规则

**正文字数和标题长度使用不同算法，必须调用脚本，禁止手写函数。**

## 正文字数（内容统计）

平台规则：所有字符×1，换行符不计。

```bash
python3 ~/.hermes/skills/creative/xhs-write-publish-flow/scripts/xhs_word_count.py "正文内容"
# 输出: 920字

# 检查是否超出上限
python3 ~/.hermes/skills/creative/xhs-write-publish-flow/scripts/xhs_word_count.py --check "正文内容" 1000
# 输出: ✅  920字/1000上限
```

目标：~1000字。

## 标题长度（显示截断）

平台规则：CJK汉字/标点/全角符号×1，其余字符（英文/数字/假名/空格）×0.5，向上取整。

```bash
python3 ~/.hermes/skills/creative/xhs-write-publish-flow/scripts/xhs_word_count.py --check-title "标题文字" 20
# 输出: ✅  标题19字/20上限
```

目标：**一律 ≤20字（news/story 同限，无例外）**；标题长度只认 `xhs_title_len()`，禁止手写第二份算法。

## 实测验证

key=`10f245269955712f06f40691079f7679214451c4`（影山优佳14岁考下足球裁判证）：
- `len()` = 929，换行符 = 9
- 正文字数 = 920 ✅（与小红书平台一致）
