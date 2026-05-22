## MODIFIED Requirements

### recommend_post_times — is_fresh 优先排序

**原行为：** 直接取 `default_post_times[:len(topics)]`，无视 is_fresh。

**新行为：**
- is_fresh=True 的话题优先分配最早时段
- 按 `(0 if topic.is_fresh else 1)` 排序后再分配时间

### explore 候选排序 — is_fresh 优先

**原行为：** explore 候选只排除 discard_count>=3，其余随机顺序。

**新行为：**
- explore 候选按 `(0 if is_fresh else 1, -engagement_score)` 排序
- is_fresh=True 且 engagement_score 高的话题优先进入 explore 槽

### Test Cases

- TC-PL1: 3个话题 [A(is_fresh=True), B(is_fresh=False), C(is_fresh=True)]，最早时段分配给 A 或 C
- TC-PL2: explore 候选中 is_fresh=True 话题排在 is_fresh=False 话题前（engagement_score 相同时）
