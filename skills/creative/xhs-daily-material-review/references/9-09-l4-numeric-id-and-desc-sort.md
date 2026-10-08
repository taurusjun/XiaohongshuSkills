# L4/L23 报告的数字 id vs hex key + published 接口 DESC 排序不可靠（9/9）

## 现象

主进程汇总存档时，想为第4层反馈闭环补精确的 rewritten_title（实际发布标题），看到 L4 子 agent 报告写「id=6891 板野育儿」「id=6821 永尾恋爱自曝」「id=6932 前田亚美上班族」，误以为这些是 `key` 的前缀（因为 9/4-9/5 批次的 key 确实长这样：6813=cca15f6d3ef4、6814=847a9c6ff1ba、6819=b4fb3b65c54f、6577=89859d6c64b5）。

第一次验证 curl 按 key startswith 过滤 → **空返回**。

## 真相

- L4/L23 子 agent 报告的「6891/6821/6932/5701/5648/5714」是 **news 表的数字 id 字段**，不是 hex key。
- 数字 id 与对应 key 示例（9/9 实测）：

| 数字 id | hex key | 实际发布标题（rewritten_title） |
|---|---|---|
| 6932 | 306270619ebf | 前田亚美宣布新身份：9月1日起，我当上班族了 |
| 6891 | f43ef26399fd | 板野友美4岁女儿只讲英语，网友替她丈夫担心 |
| 6821 | 926b46c773c9 | AKB永尾玛利亚: 没男友时邀约常达三位数 |
| 6762 | 9bc18c979596 | 小嶋阳菜晒LV新包，评论区却问：穿裤子了吗 |
| 6527 | c4d89740781f | 前田敦子晒怼脸照，眼睛水润到犯规：34岁皮肤状态太能打 |
| 5701 | a48dddf577e0 | 嫁入梨园4个月，能条爱未「脸变了」 |
| 5648 | 60f1960b2b76 | 生驹里奈爆红前后，staff态度两副面孔 |
| 5714 | fbe97b4a9423 | 三上悠亜引退后首拍：兔耳渔网袜浴室照，身材依旧能打（mode=normal） |

## 正确做法（主进程补全反馈闭环标题）

```bash
curl -s --noproxy '*' "http://127.0.0.1:5000/api/news?publish_xhs=published&sort_by=xhs_pub_time&sort_dir=DESC&limit=50" | python3 -c "
import sys, json
d = json.load(sys.stdin)
want = {'5701','6891','6821','6932'}   # 数字 id，用 str 比较
for r in d.get('rows', []):
    if str(r.get('id','')) in want:
        print(f\"id={r.get('id')} | key={r.get('key','')[:12]} | mode={r.get('publish_mode','')} | pub={r.get('xhs_pub_time','')} | v={r.get('xhs_views',0)} | rew={(r.get('rewritten_title','') or '(空)')[:45]} | orig={(r.get('title','') or '')[:30]}\")
"
```

注意：`str(r.get('id',''))` 与 `want` 集合比较——不要用 int() 硬转（部分行 id 可能缺失）。

## DESC 排序不可靠（同次实测）

`sort_by=xhs_pub_time&sort_dir=DESC&limit=50` 返回的**首条是 09-05 18:08（id 6936）而非最新 09-06 08:59（id 6932）**。L4 子 agent 与主进程都观测到。处理：拉回后一律客户端按 xhs_pub_time 降序自行重排再分层/取 Top，不要信任接口顺序。
