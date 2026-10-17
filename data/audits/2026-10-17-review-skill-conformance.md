# Skill 一致性审计 — xhs-daily-material-review/SKILL.md

日期：2026-10-17　审计模型：GLM（glm-5.3-flash）　条目：162

## 汇总

- 一致：22
- 部分一致：26
- 缺失：3
- 不适用：111

## 逐条对齐表

| # | skill 规则 | 期望 | 实现位置 | 结论 | 理由 |
|---|---|---|---|---|---|
| 50 | 第4层必须含「rewritten_title 抽检证据」子节：### 标题+3条「原始title→rewritten_title，mode= | 审计Step1能确认用了rewritten_title+pu | — | **缺失** | 【人工裁定】第4层「rewritten_title 抽检证据」子节未实现（review_render 无该节） |
| 153 | 低分母日的S级占比按绝对条数上限判（如18条不超过2-3条），不按百分比硬卡 | 低量日用绝对数判 | — | **缺失** | 【人工裁定】低分母日 S 级「绝对条数上限」（如18条≤2-3条）未实现（只有百分比 ≤15%） |
| 154 | 抓取日志条数≠API当日条数（重复命中不计入当日），以API total为准，非故障 | 以API total为准 | — | **缺失** | 【人工裁定】「抓取日志条数≠API当日条数」口径未显式处理 |
| 13 | 同事件条目先聚类再分级，保留1-2条最丰富的，其余合并跳过并标注聚类原因 | 先聚类后分级，同事件不重复处理 | agent/prompts/review.md:5 | **部分一致** | clusters 字段实现了同事件聚类（keys/main/note/grade），但片段未见「保留1-2条最丰富、其余 |
| 16 | AKB大TOP分流：事件型写全文preselected=1；晒照型以summary bullet入库（rewritten留空） | 按事件型/晒照型分别处理 | agent/write.py:56-57; services/import_drafts.py:69 | **部分一致** | 实现区分了晒照型bullet（preselected=1且无正文则跳过重跑）与事件型入库preselected=1，但未 |
| 22 | 第4层分析published数据前必须先检查publish_mode、rewritten_title、rewritten_content三字 | 分析基于实际发布标题而非原始title | agent/review.py:235-236 | **部分一致** | SQL 查询用 COALESCE(rewritten_title,title) 并检索 rewritten_title/ |
| 23 | 发布不足24h的数据不得用作否定性结论（爆款/冷门）的证据 | <24h只记录原始数据，不下结论 | agent/prompts/review.md:38 | **部分一致** | 【人工裁定】三段分层已入 prompt；「<24h 不下否定结论」未显式 |
| 24 | 24-48h数据只能做初动趋势参考，不可做最终定论 | 仅趋势判断 | agent/prompts/review.md:9 | **部分一致** | 实现了 layer4 发布数据回顾（conclusion/top/low/zero/anomaly），但片段中未见『24 |
| 25 | 强IP（OG级人物）素材即使<48h也慎重下零阅读/冷门结论，标注待观察 | OG级放宽一个时间段 | agent/prompts/review.md:38 | **部分一致** | 【人工裁定】同上 |
| 29 | xhs_ctr字段可能缺失或为空串，解析需同时接受两种情况，可用xhs_click_rate作替代口径 | 不因None/KeyError崩溃，CTR缺位需标注 | services/feedback_patterns.py | **部分一致** | 【人工裁定】click_rate 有替代口径；xhs_ctr 缺失兼容未显式 |
| 33 | 同人物其他素材有过不错数据（阅读300+）时，不得断言该人物不行 | 结论前查人物历史数据 | services/recommend.py:3 | **部分一致** | 选篇已纳入人物历史发布数据作为评分依据，但片段未见针对『阅读300+不得断言人物不行』的显式保护/阈值判断，边界未覆盖。 |
| 34 | 跨类目发布是强警告信号，但对强IP需加需观察24-48h的定性标注 | 跨类目结论带边界标注 | services/content_gate.py:10-16 | **部分一致** | 实现了跨类目命中→标 manual_review 的定性标注，但未见对强IP加24-48h观察期的边界标注逻辑 |
| 41 | 表头行和分隔行不能写在同一行 | 表头与分隔行分行 | services/validate_tables.py:5-18 | **部分一致** | 片段中可见 SEP 分隔行正则与逐行校验循环，但未展示表头行与分隔行是否同行的具体检查逻辑，无法确认该规则完整覆盖。 |
| 43 | 第4层反馈闭环表必须出现字面「阅读」字样（列头写「实际数据（阅读/互动）」或数值旁标注） | grep -q "阅读" 能命中 | agent/prompts/review.md:13 | **部分一致** | 候选文件中确有字面「阅读」（review.md:13、review.py:328），grep 可命中，但第4层反馈闭环实 |
| 51 | 列数少（≤3列）且每行数据量大时，用 Markdown 列表代替表格更安全易读 | 符合条件时用列表 | services/validate_tables.py | **部分一致** | 【人工裁定】有列数一致校验；未实现「少列改列表」的建议 |
| 116 | 大表格（>25行）拆成小表或用列表，避免表格跨段；最优解是写存档时不跨段 | 表格不跨段 | services/validate_tables.py:48 | **部分一致** | 有校验单表>25行并提示跨段风险，但片段未见自动拆表/分段处理，仅检测报告，拆表细节未覆盖 |
| 121 | published 接口 DESC 排序不可靠，客户端须按 xhs_pub_time 自行重排再分层 | 不信任接口排序 | agent/review.py:234 | **部分一致** | 【人工裁定】按 xhs_pub_time 排序聚合；未显式「DESC 不可靠」 |
| 122 | limit=50 页面可能漏掉真实最新发布，「管道停摆起点」等结论必须用分页聚合后 max(xhs_pub_time) 核对 | 结论基于全量聚合 | services/feedback_patterns.py | **部分一致** | 【人工裁定】聚合取 max(xhs_pub_time)；未显式分页核对 |
| 123 | published 接口 page 参数被忽略，用 offset 分页（0/250/500）聚合全量 | 用 offset 而非 page | services/feedback_patterns.py | **部分一致** | 【人工裁定】聚合口径统一；未显式 offset 分页 |
| 138 | 分层基数必须用 API published 集，不能用副本「pub_time非空」口径（含 publish_xhs=0 的未归位记录） | 基数用API published集 | services/feedback_patterns.py | **部分一致** | 【人工裁定】分层基数用 DB xhs_pub_time 口径 |
| 139 | layer4_aggregate.py 的 MAX_PUB 会把未来队列条计入，必须先排除 pub_dt>now 的未来条再取 max | 排除未来条后取max | skills/xhs-daily-material-review-layer4/scripts/layer4_aggregate.py | **部分一致** | 【人工裁定】脚本已随 skill 迁入；未来队列排除靠该脚本 |
| 145 | story_type 字段可能全为空，改用 format（news/story）+cj_len 判断体裁并在报告注明降级口径 | 体裁判定有替代方案 | services/format_route.py:6-13 | **部分一致** | 实现了用 format+lf 替代 story_type 的体裁路由降级方案，但用的是 is_long_form 而非规 |
| 146 | ts 异常低值（0.0/0.1/0.9）条目按「目标赛道+事件型才升级」处理，非目标赛道不升级 | 升级需满足两条件 | config/scoring_dimensions.json:233 | **部分一致** | 配置中有『认知度会因事件爆发临时升级』的事件型升级语义，但未见『目标赛道』这一前置条件的实现，升级双条件只覆盖了事件型一 |
| 147 | cs 普遍偏低（0.0~2.6）疑似 AI 再构成文本常态，分级靠 ts+cj_len+事件性补正 | cs塌陷不判为评分异常 | services/scoring.py + agent/review.py | **部分一致** | 【人工裁定】ts/cs 口径在评分类里；未显式「cs 偏低常态」说明 |
| 148 | <24h 极端 v>imp（如 v/imp≈388x）是当日伪影，按中间态处理、标待复核、禁写「爆款」 | 不误判为爆款 | agent/prompts/review.md:38 | **部分一致** | 实现要求按 <24h 分层报告 v>imp 异常，但未见将该异常按中间态处理、标待复核或禁止写「爆款」的明确规则，且 r |
| 149 | 单窗口零增长≠终局冻结，平台二次分发可恢复增长，此类模式结论须留余地 | 不写终局冻结结论 | agent/review.py:223-246 | **部分一致** | 【人工裁定】_entity_pub 有 zero_streak/近M条统计；结论留余地靠 prompt |
| 150 | >48h 仍 0/0 才可确认零分发；24-48h分层为空但未来队列活跃时不触发停摆告警 | 按判据区分停摆与空窗 | agent/review.py:246 | **部分一致** | 【人工裁定】zero_streak 已实现；24-48h 空但队列活跃的判据靠 prompt |
| 151 | 发布停摆告警三判据：MAX_PUB距查询超阈值+连续日0发布+定时队列清空且<24h=0，三者齐全才成立 | 三判据齐全才告警 | agent/review.py:223 + services/feedback_patterns.py | **部分一致** | 【人工裁定】有连续0曝光统计；停摆三判据未显式成规则 |
| 152 | 前一日高量日后接低量日时S/A重复率显著升高，第2/3层应优先做重复判定 | 低量日优先判重 | agent/prompts/review.md:4 | **部分一致** | 实现了第2层 date_trap 的前日重复/前日已发判定，但片段中未见「前一日高量日后接低量日时优先判重」的条件触发逻 |
| 12 | 过滤素材优先按fetch_by而非category字段；category可能误标（如グラビア被标为体育） | 用fetch_by分布判断素材结构 | services/review_archive.py:15,29,42,49 | **一致** | 实现按 fetch_by 统计分布、分组排序与展示，未依赖 category，符合规则要求。 |
| 14 | 聚类时注意日期陷阱：检查前一天已发布素材，避免同事件重复入库 | 聚类范围包含前一天已发布条目 | agent/review.py:330 | **一致** | review 输入明确包含前一天素材 JSON 并在 prompts/review.md:16 要求对照前一天已发布同事 |
| 15 | 输出顺序：先按来源分组全量列表，再聚类分析，再分级结果 | 按固定顺序输出三层内容 | agent/prompts/review.md:3-11, services/review_render.py:16-20 | **一致** | prompt 要求先输出全量分组结构化数据（date_trap/clusters/grades 等），再由 render |
| 27 | xhs_pub_time解析需兼容空值、10字符纯日期、16字符标准格式三种情况 | 分层数字正确，异常记录不错桶 | agent/review.py:234 + services/feedback_patterns.py | **一致** | 【人工裁定】xhs_pub_time 解析兼容（COALESCE/切片） |
| 30 | data-feedback-patterns.md更新时模式节必须插在跨会话趋势表之前，趋势行插在表内最后一行后 | 趋势表节唯一，无孤悬行 | services/feedback_patterns.py | **一致** | 【人工裁定】模式节+趋势行的插入位置由服务保证 |
| 36 | 存档必须包含节标题含「全量素材一览」的全量列表节，不得压缩为仅聚类分析 | 审计Step 2 grep可匹配到该节 | services/review_archive.py:38 | **一致** | 存档生成含「全量素材一览」节标题（两种格式均含），grep 可匹配，满足规则。 |
| 37 | 存档中表格前必须用##标题，不能用粗体；表格|必须在行首无前导空格 | 表格在Telegram正确渲染 | services/validate_tables.py:42 | **一致** | 校验器明确检查表格前必须是#标题且禁止粗体引导，与规则语义一致 |
| 38 | Markdown 表格必须包含分隔行 |---|---|，否则不被识别为表格 | 每个表格有分隔行 | services/validate_tables.py | **一致** | 【人工裁定】分隔行/表头/列数校验已实现 |
| 39 | 表格列数必须一致：表头、分隔、数据行的 | 数量相等 | 各行列数一致 | services/validate_tables.py:27 | **一致** | 实现了表头与分隔行列数一致性校验，效果满足规则。 |
| 40 | 表格前后各留一个空行 | 表格前后有空行 | services/validate_tables.py:36,45 | **一致** | validate_tables 明确检查表格前缺空行与表格后缺空行并报告问题，语义与规则完全对应。 |
| 42 | 单个表格内容建议不超过 25 行，避免分段时表格跨段 | 单表≤25行 | services/validate_tables.py:46 | **一致** | 【人工裁定】单表 >25 行 → problems |
| 44 | 表格单元格内禁止出现 | 字符，即使 \| 转义也不行；含竖线内容改写为纯文字 | 单元格无竖线，列数不错位 | services/review_render.py:6 | **一致** | _c 将单元格内容中的 | 替换为全角｜，保证单元格无竖线、列数不错位，效果满足规则 |
| 46 | 标题必须紧邻表格，中间不能夹任何正文行（含 blockquote > 行）；说明文字上移到标题之前 | 标题与表格间无正文行 | services/validate_tables.py:41 | **一致** | 【人工裁定】表格前必须是 # 标题（禁粗体引导） |
| 47 | 「标题→说明/引用行→表格」三明治结构写时就改成「说明行→标题→表格」，不等校验器抓 | 无三明治结构 | services/validate_tables.py:42 | **一致** | 校验器要求表格前必须是 # 标题行（否则报错），即强制「说明行→标题→表格」结构，与规则要求的目标形态一致。 |
| 105 | HTTP_PROXY 全局代理会使 DeepSeek API 连接不稳（国内服务不需代理），导致 stale stream 和 .tick. | 诊断 stale stream 时先查代理设置 | scripts/yahoo_common.py:28 | **一致** | 实现将 DeepSeek API 改为不走系统代理的直连 session，并在 yahoo_common.py:103  |
| 111 | 第3层查DB必须用 search= 参数，不要 limit=200 全量拉回过滤，会漏旧记录 | 用 --data-urlencode search= 查询 | services/related.py:46-55 | **一致** | 实现用 SQL LIKE 直接查 DB 多字段匹配并带 limit，效果等同/优于 search= 参数查询，未做全量拉 |
| 112 | 第4层必须先查 rewritten_title 再分析，原始title与实际发布标题可能完全不同 | 分析前先取发布标题 | agent/review.py:6 | **一致** | 检索采用 title/content_ja/rewritten_title 三字段 OR，明确包含 rewritten_ |
| 115 | 存档表格格式：表格前用 ## 标题而非粗体，| 在行首，必须有分隔行 | 表格渲染正常 | services/validate_tables.py:25 | **一致** | validate_tables.py 专门校验表格格式（表头、分隔行、列数、粗体行退化），语义上完整覆盖规则要求。 |
| 156 | 宣发系列长期0发布的素材（如30+条全0发布）应建议改道其他平台或待窗口，不作普通S级XHS稿 | 长期0发布系列改道 | agent/review.py:213 | **一致** | 【人工裁定】连续0曝光≥3 → 改道 gzh 已实现 |
| 157 | validate-tables.py 规则9：单元格内竖线（哪怕转义）是最高频复发点，shell命令写进单元格必中招，应写纯文字描述 | 单元格无竖线 | services/validate_tables.py:28 | **一致** | 【人工裁定】单元格竖线→列数不一致报错 |
| 158 | validate-tables.py 规则11：标题与表格间不能夹正文行（含 > 引用行和说明段），三明治结构改为说明行→标题→表格 | 标题紧邻表格 | services/validate_tables.py:41 | **一致** | 【人工裁定】标题与表格间夹正文行→报错 |
| 162 | L23 报告的「最新pub」可能是定时队列值（口径差异非误报），需区分已发布与已排定 | 区分口径差异与误报 | services/schedule.py:72; services/recommend.py:101; scripts/yahoo_news_publish.py:79 | **一致** | 实现以 xhs_pub_time 表示已排定的预发布时间（schedule/recommend 查询待发布队列），以 p |
| 1 | 技能里写了多少步就执行多少步，不能做80%跳20%；每步完成后自查所有子步骤是否完成 | 每条步骤的子步骤全部执行，不提前进入下一步 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 2 | 用户说"开始review"等触发词时，第一步必须skill_view加载本skill，不要跳过加载直接查数据 | 触发后先加载skill再执行流程 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 3 | 用户触发review时，先检查当天存档文件是否存在，存在则直接读取发送，不重新拉API跑四层 | 存档存在即复用，不重复跑流程 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 4 | 不要向用户展示中间步骤；拉数据、聚类、分析都在后台完成，存档完成后一次性发结果 | 用户只收到最终完整结果 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 5 | 未确认最终产出前不得说"跑完了/完成了"；须确认存档写入成功、segment发送成功或cron last_status=ok之一 | 有实际产出确认才可汇报完成 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 6 | 用户问结果时绝对禁止说"往上翻""之前的消息里有"；必须重新读取存档或重跑并直接贴内容 | 内容直接贴在回复中，不假设用户翻历史 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 7 | 用户说"停止/不要触发"时立即停止触发性操作，不找理由、不说"已经跑完了" | 立即停止并承认，不做解释性辩驳 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 8 | cron跑完后用户问结果：先查存档→存在则直接发→不存在则重跑cron→cron报错且用户急则主进程直做四层 | 按优先级顺序处理结果追问 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 9 | delegate_task子agent内禁止写稿、直接DB写入、标记发布；这些只允许主进程或用户明确指令下执行 | 子agent只做review层流程，不做写稿/写库/发布 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 10 | 交互模式下主进程直做四层，不用delegate_task；cron模式可分发子agent | 按模式选择执行方式 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 11 | 读取关键素材必须看内容详情，不能只看标题（story类、title_score≥4.0、目标赛道、争议标题条目） | 关键条目均查看内容详情 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 17 | 查同关键词/同人物旧记录必须用API的search参数，不能用limit全量拉回后过滤 | search覆盖全部记录，旧文章不被limit截断漏掉 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 18 | search查询中文参数用-G --data-urlencode传递，不用shell变量嵌python3 -c | 无URL编码错误和JSONDecodeError | — | **不适用** | 人工流程/工具用法（自动化流水线已取代）：shell 参数写法——人工操作 |
| 19 | search未命中时不得直接判定无旧记录，需拉全量再查一次确认 | 确认无关联后标记「无关联」而非「待补充」 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 20 | 写稿前必须完成第3层跨时间关联查询，第3层与写稿是因果关系 | 写稿内容不与已发布旧素材重复 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 21 | 关联类型按五类处置：纯重复跳过、时间线补充合并、人物呼应写续篇、新角度写新篇、无关联新选题 | 每条关联有对应处置 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 26 | 写反馈闭环前自查四项：发布时长、是否OG级IP、标题争议性、category匹配度 | 四项检查完成后再写结论 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 28 | 分层数字变动时必须同步更新趋势行，不能只改正文表 | 趋势行与正文一致 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 31 | 写入反馈模式的结论必须经得起反例检验，存在干扰变量时不断言单一因果 | 结论考虑时间、IP流量、反例、类目匹配等干扰 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 32 | 发现反例后修改模式的边界条件本身，不添加例外条款 | 模式被修正而非打补丁 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 35 | 每轮第4层完成后自查：结论若有反例是否已处理 | 反例已被识别并处理 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 45 | 表格正上方不能是粗体引导句，必须用 # 级标题（#### 也行）压住表格 | 表格前紧邻行为 # 标题 | services/validate_tables.py:1-18 | **不适用** | 候选实现是 Python 校验/审计代码（validate_tables.py 校验表格列数、skill_audit.p |
| 48 | 写完存档必须跑 validate-tables.py 校验，problems: 0 才算通过，不靠肉眼判断 | 校验输出 problems: 0 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 49 | 写存档前自查高频复发 TOP3：标题紧邻表格、单元格无竖线、粗体行不压表格 | 三条均通过 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 52 | segment-send.py 只在存档后 Step 5 调用，不在写稿、入库或其他流程调用 | 仅 Step 5 调用 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 53 | segment-send.py 失败时首选 curl fallback 脚本（保留表格渲染），不要重试 Python urllib 路径（s | 失败即切 curl fallback | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 54 | 发送前用 curl 测代理连通性（返回 302 即健康），以 curl 实测为准，不以 lsof 无输出为判据 | curl 得 302 才发 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 55 | 收到 Errno 61 Connection refused 时先 curl 测代理：通则直接切 curl fallback，不改端口、不重 | Connection refused→curl验证→fall | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 56 | macOS 无 timeout 命令，不要写 timeout 90 python3 ...；用工具自带 timeout 参数 | 不调用 timeout 命令 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 57 | macOS 不要用 grep -P 手搓提取 bot_token（BSD grep 不支持 -P 会静默失败）；直接跑 fallback 脚 | 不用 grep -P 提取 token | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 58 | 代理判据分级：单端口 000 其它端口 302=端口漂移→切该端口 fallback；全部候选端口 000+lsof 无监听+直连 000= | 按判据分级处置 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 59 | 终极 Fallback：所有发送路径失败时，存档内容作为 cron 最终响应直接输出，由系统 deliver 投递；存档仍正常落盘 | 响应含存档内容且存档已写 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 60 | cron 模式下 execute_code / from hermes_tools 被 BLOCK，Python 处理一律用内联 pytho | 无 execute_code 调用 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 61 | 并行子 agent 共享 /tmp，临时文件必须用层前缀命名（/tmp/l1_* /tmp/l23_* /tmp/l4_*）或层专属子目录， | 临时文件均带层前缀 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 62 | 并行模式下 Layer1 与 Layer23 作用域可能不对称（目标赛道 vs 全量），需在 goal 中明确范围并在存档中标注差异，不合并 | 存档标注两条线数据集差异 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 63 | 子 agent toolsets 限定 terminal/file/web；禁止调用 delegate_task、write_file 到  | 子 agent 无越权调用 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 64 | cron prompt 中 segment-send 用完整展开的绝对路径，不用 ~/ 缩写，不用 segment-send.sh | prompt 用绝对路径 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 65 | 主进程汇总时禁止用 execute_code 读取子 agent workspace 文件或做批量数据处理 | 主进程汇总阶段无 execute_code 文件读取/批处理 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 66 | Layer2-3 分类对齐验证必须用 read_file 而非 execute_code 读取 layer23 报告日期 | 验证代码使用 read_file | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 67 | 永远不要依赖 execute_code 作为 cron 流程中的关键路径 | cron 关键路径无 execute_code 依赖 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 68 | audit 脚本中的 Python 操作必须同步适配，from hermes_tools 在 cron 下不可用 | 审计脚本在 cron 下可运行 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 69 | 子 agent 获取素材时 ts 字段可能全为 0，主进程汇总应优先信任 layer1 的分级结果 | S/A 分级以 layer1 为准 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 70 | layer23 报告标注 ts 均为 0 时，其 S/A 阈值偏宽松，引用其价值建议须注明「基于自行分级」 | 引用处带自行分级标注 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 71 | layer23 报告 ts=0 时主进程不必再 curl 验证，确认其使用 cj_len+事件性降级标准即可 | 无重复 curl 验证 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 72 | 子 agent 报告头部计数可能与正文列表不符，主进程汇总必须以正文列表实际条目数为准，不能照抄标题计数 | 存档数字与实际列表条数一致 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 73 | 子 agent 不一定能 skill_view 加载 layer skill，delegate 时必须把该层核心步骤内联进 goal/con | goal 含拉取命令、date_from 铁则、分级阈值、禁 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 74 | goal 里保留 skill_view 为第一选择，加载不到不阻塞，按内联步骤执行 | 子 agent 无 skill 时仍能完成产出 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 75 | 验收不依赖子 agent 声称加载了 skill，用输出完整性校验（如 key 全覆盖比对） | 以输出完整性为验收标准 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 76 | 子 agent 无 skill_view 时可用 read_file 直接读取该 layer 的 SKILL.md 作为 fallback | fallback 路径可取得 skill 全文 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 77 | layer skill 路径前缀不对称：layer1 在 creative/ 子目录下，layer23 与 layer4 不在；写路径后须自 | delegate context 中路径无多余 creati | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 78 | 不要把「能 read_file 到 skill」当作前提，内联步骤仍是第一保证 | read_file 失败时流程仍可执行 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 79 | 主进程不应依赖读取子 agent workspace 文件作为数据传递主通道，应从 delegate_task 返回的 summary 提取 | 汇总数据来自 summary | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 80 | summary 缺精确 key 时可读当日 review_l1_report_* 文件补全，只读当日文件，禁止读历史日期文件 | 仅读取当日日期的报告文件 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 81 | workspace 报告文件应视为取全 key 的默认路径：L1 delegate 一返回，主进程立刻 read_file 当日报告文件再构 | L1 返回后立即读取报告文件 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 82 | 报告文件路径以 L1 summary 里报出的为准（可能在 /tmp 或 workspace），不要硬编码 workspace 路径去 se | 先按 summary 报告路径读取 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 83 | delegate 时必须将 layer1 的显式分级结果（S/A 列表含 key 和标题）放入 layer23 的 context 字段 | context 含 S/A 列表 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 84 | layer23 goal 须写明：基于第1层 S/A 列表分析、不重新分类、不通过 search_files 查找旧报告文件 | goal 含禁重分类与禁搜旧文件指令 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 85 | layer23 可拉今日素材取 content_ja 全文，但不得自行重定 S/A 阈值 | S/A 阈值沿用 layer1 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 86 | layer23 发现值得关注的未评级素材，只能以「补充建议」提及，不改变原有分级 | 原分级不被改动 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 87 | 主进程汇总时必须验证 layer23 报告引用的素材日期是否为今日；若用了旧数据须自行补做第2~3层分析 | layer23 报告素材均为今日日期 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 88 | cron 表达式使用系统本地时区 CST，不要改成 UTC 修正形式（如 0 2 * * *） | next_run_at 显示 +08:00 正确时间 | — | **不适用** | 人工流程/工具用法（自动化流水线已取代）：cron 时区写法——Hermes cron 配置 |
| 89 | cron 健康检查除 last_run_at 外还要看所有 job 的 next_run_at；多个 job 的 next_run_at 停 | 多 job next_run_at 停滞即判定调度器挂 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 90 | 调度器停 tick 导致当日 review 未跑时，主进程须补跑（补 L1/L3 → 补存档 L2/L4 → 审计 → segment-se | 当日存档与审计补齐 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 91 | 排查 cron 故障向用户汇报时必须先数据后结论，每步结论附原始日志/命令输出，禁止推测性语言 | 汇报含证据行，无「可能/大概」措辞 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 92 | 手动重跑 cron 后须确认 last_run_at 更新、output 目录有新文件、用户收到 deliver | 三项确认均通过 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 93 | 测试 job 的 prompt 必须模拟真实 cron 完整流程，不能跳过任何步骤（如 segment-send） | 测试含全部步骤且用户收到输出 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 94 | 不要直接修改主 cron prompt 来测试，须创建独立 one-shot 测试 job，且用 ISO 时间戳（now+1m 等语法不支持 | 测试用独立 job + ISO schedule | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 95 | 测试新 cron 架构前必须清理旧状态（删除同日期存档），测试后清理测试 job | 测试前删同日存档，测试后删 job | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 96 | 测试 job 与主 cron 同时跑会输出混淆（都 deliver=origin 发到同一对话），须注意区分 | 测试期间输出可区分 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 97 | delegate_task 分发前必须确认被引用的 layer skill 真实存在，否则子 agent 卡死在 skill_view 导致 | 引用的 layer skill 均已创建 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 98 | layer23 分类膨胀问题持续存在（倾向将商务/经济新闻升格为 S），主进程汇总应优先信任 layer1 分级，layer23 价值建议按 | S 级判定以 layer1 为准 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 99 | layer4 子 agent 有 600s 超时风险（大量反馈闭环分析消耗 tool call 配额），主进程须准备 fallback | layer4 超时时主进程可接管 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 100 | Layer 4 delegate_task 超时时主进程必须补做：curl拉published→时间分层→反馈闭环→append data- | 超时后补做完成且审计全PASS | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 101 | 补做第4层后主进程仍需跑完整审计（Step 3 data-feedback-patterns.md更新审计），不能跳过 | 审计步骤完整执行 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 102 | cron prompt 引用 layer skill 前必须先 skill_view 确认该 skill 真实存在 | skill_view 成功返回后才可引用 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 103 | 新创建的 layer skill 必须经测试 job 验证后才可挂到 cron prompt | 未验证的 skill 不得上 cron | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 104 | 主 cron 的 enabled_toolsets 不要设置，让子 agent 自行决定所需工具 | enabled_toolsets 留空 | — | **不适用** | 人工流程/工具用法（自动化流水线已取代）：enabled_toolsets——Hermes 子agent 配置 |
| 106 | 当日 total=0 时先区分「抓取失败」vs「真无新闻」：查 fetch_runner.log，全关键词 timeout+未找到新闻=抓取 | 先诊断再决定是否3日扩展 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 107 | 抓取失败且近期素材已 review 过时，不做3日扩展重审，只交付故障报告+第4层发布回顾，不含S/A清单 | 存档不含第1~3层和S/A清单 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 108 | 故障报告若误列旧素材为S/A，写稿cron会重写旧稿并把已发布条 publish_xhs 回退为0，导致重复发文 | 严禁在故障报告中列S/A清单 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 109 | ingest 与 publish 是解耦链路：素材断供≠发布停摆，故障报告须同时给出发布管道状态（MAX_PUB+队列条数） | 报告含发布管道对照信息 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 110 | 仅当三者齐备才扩展日期范围：当日0条+抓取只是延迟未跑（无timeout证据）+近期素材未被review过 | 三者不齐不扩展 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 113 | 第3层与写稿是因果关系：写稿前必须先做第3层，否则写出重复内容 | 先第3层后写稿 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 114 | 执行纪律：skill 写了多少步就做多少步，不做80%跳20% | 步骤完整执行 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 117 | 代理端口不定期漂移，遇 Connection Refused 先 echo $http_proxy 确认当前端口 | 先确认端口再排查 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 118 | segment-send.py 的 socks 代理 fallback 因 socks 包未安装总是失败，推荐用 curl 手动发送 | 发送失败时改用 curl fallback | — | **不适用** | 人工流程/工具用法（自动化流水线已取代）：segment-send socks——已被 services/deliver |
| 119 | 主进程汇总时应自行用 curl 验证一次，防 layer23 子 agent 错误断言 search 不查历史数据或引用旧 workspac | 主进程独立核实子agent结论 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 120 | L4/L23 子 agent 报告中的数字是记录 id 不是 key 前缀，按 str(r.get('id')) 过滤，不要按 key st | 用 id 字段匹配记录 | — | **不适用** | 人工流程/工具用法（自动化流水线已取代）：L4/L23 子agent 报告 id——流水线无子agent |
| 124 | layer23 可能把定时发布队列误报为已发布：子agent称已发布时先查 xhs_pub_time 是否未来时间，措辞用「已排定定时发布」 | 区分已发布与定时队列 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 125 | layer23 可能把「pub_time 已排定但 publish_xhs 未归位」的记录当作最新已发布，MAX_PUB 必须由主进程以 p | MAX_PUB 以主进程聚合为准 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 126 | L23 报的 MAX_PUB 与 published 聚合不一致时，一律以聚合为准，可用 title search+事件关键词双检索二次确证 | 不一致时聚合优先 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 127 | MAX_PUB 三处一致时无需双检索确证，但主进程独立 offset 聚合复核始终保留为常规步骤 | 聚合复核为固定步骤 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 128 | L23/L4 报的 published 总条数可能虚高，分层基数与管道健康度一律以主进程 offset 分页聚合为准 | 计数以主进程聚合为准 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 129 | 标准执行模板：layer1 先行 → layer23+layer4 并行，layer23 沿用 layer1 的 S/A 列表不重分级，可规 | S/A 对齐无膨胀 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 130 | Layer1 与 Layer23 作用域须对齐（Layer1 过滤赛道但 Layer23 用全量会导致不对称），双方应使用同一数据集 | 两层作用域一致 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 131 | 以正文列表计数为准，不轻信报告标题中的数字（标题写17条正文实为15条的案例） | 计数核对正文列表 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 132 | delegate layer1 时在 goal/context 中硬性要求输出完整40字符key，子agent默认截断为12字符前缀 | 报告含完整key | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 133 | L1 summary 的 key 截断不因硬性指令而消除，workspace 报告文件是取全 key 的可靠路径 | 从报告文件取全key | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 134 | L1 报告落点可能在 /tmp 而非 workspace，主进程按 L1 summary 报的路径 read_file | 按报告路径读取 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 135 | L4 子 agent 报「脚本不存在」可能是 delegate context 路径多写了前缀（如 creative/），需核对路径 | 先核对路径再下结论 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 136 | L1 报告「按来源分组全量列表」可能整组漏掉，主进程必须核对各组条数之和==total并从原始JSON补齐 | 分组求和等于total | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 137 | API search= 不索引 content_ja 日文正文，查日文内容需读副本文件 | 日文检索走副本 | services/news.py:12 | **不适用** | 规则是关于 API search= 不索引日文正文的检索纪律，候选片段仅是 DB 访问收敛与 content_ja 读取 |
| 140 | layer4_aggregate.py 是两个位置参数（空格分隔），传单引号单参数会静默回落到机器时间造成1h偏差 | 参数格式正确且无静默偏差 | — | **不适用** | 人工流程/工具用法（自动化流水线已取代）：layer4_aggregate 位置参数——人工命令行 |
| 141 | 模式编号必须取文件正文最大值，不能只看趋势表最后一行，否则会撞号 | 编号取正文最大值 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 142 | 审计Step3最高编号统计用行首 ### N. + bullet **#N 核对，防编号撞车 | 编号统计覆盖两种格式 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 143 | 审计Step3日期grep有false-negative陷阱：优先 ^| $FULL 完整日期匹配，短日期兜底 | 日期校验不漏检 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 144 | 审计Step4的grep会捕到自指否定句（如「无待补充」字样）属误报，需人工识别 | 识别自指句误报 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 155 | L23检出标题误标（主角张冠李戴）时须核实content_ja并建议合并改主角，避免成稿主角错位 | 主角与内容主体一致 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 159 | 存档写完必须跑 validate-tables.py 校验表格渲染，problems>0 时修正后复跑至0 | problems: 0 才通过 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |
| 160 | curl fallback 为默认发送路径，代理端口漂移时脚本读 $http_proxy 自动切换 | 发送走curl fallback | — | **不适用** | 人工流程/工具用法（自动化流水线已取代）：curl fallback 代理漂移——已被 services/deliver |
| 161 | 主进程直做模式和 delegate_task 分发模式均可行，选择依赖用户偏好和API负载 | 两种模式均可接受 | — | **不适用** | 人工 agent 流程（自动化流水线已取代） |

---
> 「一致」条目已省略（见 JSON）。完整清单：`config/skill_audit/items.json`。