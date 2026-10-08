# 批后审计：已写稿件的 content_ja 覆盖完整性检查

**触发场景：**
- 用户要求审查已写稿件的内容完整性
- 前一天/当天写的多篇稿件被指出单薄，需要全量排查
- 自己发现可能犯"前500字"错误，但不确定其他篇

---

## 步骤1：拉取所有已写稿件并初筛

```bash
sqlite3 ~/PG/XiaohongshuSkills/data/news_dev.db "
SELECT key,
       substr(title,1,40),
       LENGTH(rewritten_content),
       LENGTH(content_ja),
       CASE
         WHEN LENGTH(content_ja) >= 2000 AND LENGTH(rewritten_content) < 700  THEN '⚠️疑似前500字'
         WHEN LENGTH(content_ja) >= 1500 AND LENGTH(rewritten_content) < 500  THEN '⚠️疑似前500字'
         WHEN LENGTH(content_ja) >= 1000 AND LENGTH(rewritten_content) < 400  THEN '⚠️疑似前500字'
         WHEN LENGTH(content_ja) < 500                                        THEN '✅短素材'
         ELSE '✅ok'
       END as verdict
FROM news
WHERE rewritten_content != ''
  AND (publish_xhs IS NULL OR publish_xhs = 0)
ORDER BY LENGTH(content_ja) DESC;
" | column -t -s '|'
```

---

## 步骤2：对疑似条目通读content_ja后半段

```bash
ALL_PROXY='' curl -s --noproxy '*' "http://127.0.0.1:5000/api/news/<key>" | python3 -c "
import sys, json
d = json.load(sys.stdin)
ja = d.get('content_ja','') or ''
rc = d.get('rewritten_content','') or ''
print(f'content_ja: {len(ja)} chars, rewritten: {len(rc)} chars')
print('=== content_ja 800字以后 ===')
print(ja[800:])
"
```

**漏写判断标准（后半段包含以下内容 = 漏写）：**
- 独立的故事段落/新事件节点
- 完整的论证链/分析段落
- 不同角度的人物引语（半句带过不算）
- 纯榜单数据/链接列表 → **不算漏写，跳过**

---

## 步骤3：处理与汇报

| 判定 | 处理 |
|------|------|
| 确实只读了前500字，有漏写 | 逐条重写（通读全文后覆盖旧稿），走完整renwei→密度→review流程 |
| 后半段为榜单数据/链接/图片标记 | 标记为短news/快讯，通过审计 |
| 已覆盖全文核心信息 | ✅通过，不改 |

汇报格式（表格）：
```
稿件标题 | content_ja | rewritten | 判定 | 处理
```

---

## 实战案例（6/27审计）

```
影山优佳战术板   2876 | 205  → ⚠️前500字 → V2重写1169字 ✅9.8/10
拉乌尔30次失败   1961 | 908  → V2已通过 ✅9.6/10
中岛健人电影     1147 | 896  → ✅通读全文，11/11信息点覆盖
佐久间CM多素材    327+ | 1252 → ✅多素材合并，信息充分
imase逆袭/榜单   1536 | 167  → ✅后半段纯榜单数据，无叙事
```
