# Twitter/英文分发表 — 完整管线架构

> 版本：v1.0 | 日期：2026-06-12 | 分支：feat/en-pipeline
> 前置里程碑：v1.0-milestone-chinese-pipeline（中文管线完整版）

---

## 角色定义

| 角色 | 是谁 | 职责 |
|------|------|------|
| **Cron A** | Hermes cron job | Yahoo抓取，定时自动跑（已有） |
| **你** | Jeremy | 素材review、写稿决策、发布决策 |
| **Hermes酱** | 主agent（我） | 写稿时同时输出中文稿+英文初稿，你在场时才工作 |
| **Cron B** | Hermes cron job（英文评审+发布） | 英文终审→发Twitter→更新静态站，自动跑 |
| **Cron C** | Hermes cron job | 静态站GitHub Pages部署（自动） |

---

## 完整流程图

```
                         时段 A（需要你在场）
                         ═══════════════════
Cron A ──→ Yahoo抓取 ──→ 素材入库(content_ja)
                              │
                              ▼
                          你review素材 → 决定哪条写
                              │
                              ▼
                 Hermes酱写稿（一次LLM调用，双输出）
                     ├── 中文：rewritten_title + rewritten_content
                     │      ↓
                     │   你review中文稿 → 过or改
                     └── 英文：en_title + en_content（初稿）
                              │
                              ▼
                         标记 publish_xhs=1
                         （你的决策："发"）
                              │
                              │  <──── 你的在场结束
                              ▼

                         时段 B（完全自动，不需要任何人）
                         ════════════════════════════════
Cron B ──→ 每30分钟扫DB
              │
              ├── 条件：publish_xhs=1 AND en_publish_twitter=0
              │
              ▼
          读 content_ja + en_title + en_content
              │
              ▼
          LLM英文评审（子agent，用 DeepSeek Pro V4）
               ├── 语法检查 → 修正明显错误
              ├── 成员名核对 → 确保罗马音正确
              ├── 术语保护 → senbatsu等不意译
              └── 流畅度润色
              │
              ▼
          ★ 双层评审
              │
              ├── 【Layer 1: 长文评审 (en_content)】
              │   ├── 语法 + 术语 + 成员名（同原有）
              │   ├── 叙事完整性：起承转合有没有
              │   └── 信息量：比原文多了什么新角度
              │
              └── 【Layer 2: 推文评审 (en_tweet)】
                  ├── ≤280 chars（硬限制，超则截断或重写）
                  ├── 读者测试：读完这条推文我知道了一件什么事？
                  ├── 必须包含：结果/名场面 + 钩子
                  │   ❌ "A和B进行了逆再生舞蹈对决" → 没信息
                  │   ✅ "A赢了B的逆再生舞蹈对决，因为XXX" → 有信息
                  └── 建议附链接（如果有英文站URL）
              │
              ▼
          输出终版英文
              │
              ├── en_title + en_content（长文，进入静态站管线）
              ├── en_tweet（推文版，供手动发布）
              ├── en_publish_twitter（由你手动或Cron B更新）
              └──
              └──→ 生成静态站 Markdown 页面
                      │
                      ▼
                  Cron C ──→ git commit + push
                              ↓
                          Cloudflare Pages 自动部署
                              ↓
                          xxx.com 上线（全自动）
```

---

## DB新增字段

`news` 表增加以下字段（不破坏现有字段）：

| 字段 | 类型 | 写出者 | 用途 |
|------|------|--------|------|
| `en_title` | TEXT | **Hermes酱**（写稿时同步输出） | 英文标题初稿 |
| `en_content` | TEXT | **Hermes酱**（写稿时同步输出） | 英文正文初稿 |
| `en_tweet` | TEXT | **Cron B**评审后输出 | 推文版（≤280 chars） |
| `en_publish_twitter` | INTEGER | **你**手动或**Cron B**回写 | 0=未发，1=已发 |
| `en_pub_time` | TEXT | **Cron B**回写 | 实际发布时间（YYYY-MM-DD HH:MM） |

---

## 触发条件一览

| 动作 | 触发者 | 触发条件 | 是否需要你在场 |
|------|--------|----------|---------------|
| Yahoo抓取 | Cron A | 定时（每30分钟） | ❌ |
| 写稿+英文初稿 | **Hermes酱** | **你在场** + 你说"写" | ✅ |
| 中文review | **你** | Hermes酱写完草稿后 | ✅ |
| 标记 publish_xhs=1 | **你** | review通过后 | ✅ |
| 英文评审+发Twitter | Cron B | 每30分钟扫DB | ❌ |
| 英文评审+生成静态站页 | Cron B（同一趟） | 发推后 | ❌ |
| 静态站git push | Cron C | 新页面生成后 | ❌ |

---

## 英文评审规则（Cron B prompt核心内容）

LLM子agent在评审英文版时，必须遵守以下约束：

### 1. 语法检查
- 修正时态错误（过去/现在混用）
- 修正冠词遗漏（a/the）
- 修正主谓不一致

### 2. 成员名核对
- 成员姓名使用标准罗马音拼写（如 Nogizaka46 成员名参照公式站拼写）
- 避免自动纠正为英语化拼写（如将 Maiyan → Maiyan 保留，不改为 May Yan）
- 不确定时保留原文罗马音

### 3. 术语保护（禁止以下术语被意译）

| 日语术语 | 英文保留 | 禁止意译为 |
|----------|----------|-----------|
| senbatsu | senbatsu | "selection members" |
| center | center | "center position" / "main performer" |
| genin | genin / kenkyusei | "trainee" / "rookie" |
| kage | kage | "backstage" |
| handshake event | handshake event | "fan meeting" (loss of specific meaning) |
| sousenkyo | sousenkyo / general election | "popularity vote" |
| captain | captain | "leader" (loss of AKB48 system meaning) |
| solo-con | solo concert | — OK to use "solo concert" but prefer "solo-con" |
| kouhai / senpai | kouhai / senpai | "junior" / "senior" |
| ○期生 | ○th generation | OK to translate number |
| ○○グループ | ○○-group | OK |
### 4. 评审流程（双层）

1. 先读 `content_ja`（日文原文）理解原文语境
2. 再读 `en_title` + `en_content`（初稿）
3. **Layer 1 — 长文评审**：逐条检查语法/名称/术语 + 叙事完整性 + 信息增量
4. **Layer 2 — 推文评审**：
   - 生成一条 ≤280 chars 的推文（en_tweet）
   - 读者测试：读完这条推文我知道了一件什么事？
   - 必须包含结果/名场面 + 钩子。❌ 只描述事件过程的不合格
5. 输出：
   - `en_title_final` / `en_content_final`（终版长文）
   - `en_tweet_final`（终版推文）
   - `reader_test_passed: true|false`——false 则标记需人工复审

---

## 技术依赖

| 组件 | 状态 | 备注 |
|------|------|------|
| DB加字段 | ✅ 完成 | en_title / en_content / en_tweet / en_publish_twitter / en_pub_time |
| Web UI详情页 | ✅ 完成 | English卡片（含Title/Content/Tweet编辑框，auto-resize） |
| sqlite_db.py allowed | ✅ 完成 | 全部en_*字段加入白名单 |
| update.sh 增加 en 字段 | ✅ 完成 | en_publish_twitter 加入INT_FIELDS |
| Hermes酱写稿双输出 | ✅ 功能完成 | 写稿时同时输出中文+英文初稿 |
| 英文评审规则（双层） | 📄 已定义 | 见上方英文评审规则章节 |
| cron job (Cron B) | 🔲 待创建 | 每30分钟，用 DeepSeek Pro V4，双层评审→写DB |
| Twitter API | ❌ 无API额度 | @JapanEntRept 认证通过，但New Free tier无发推额度(402)。当前：手动发推 |
| 静态站生成+部署 (Cron C) | 🔲 下一阶段 | Astro/Hugo + Cloudflare Pages |

---

## 开发顺序

| 优先级 | 阶段 | 内容 |
|--------|------|------|
| **P0** | 1 | DB加 en_title / en_content / en_publish_twitter / en_pub_time 字段 |
| **P0** | 2 | 改写稿prompt：写中文稿时同时输出英文初稿 |
| **P1** | 3 | update.sh 增加 en_* 字段支持 |
| **P1** | 4 | 建 Cron B：英文评审→发Twitter（需等Twitter API key就绪） |
| **P2** | 5 | 建 Cron C：静态站生成+deploy |
| **P2** | 6 | webapp API 增加 en_* 字段（review时可见） |

---

*文档维护者：Hermes Agent*
*最后更新：2026-06-12*
